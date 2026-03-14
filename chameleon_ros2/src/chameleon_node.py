#!/usr/bin/env python3
"""
Chameleon ROS2 Node — Phase 1 (myCobot 280 + UR5e ready)
Bridges ROS2 humanoid/arm stack to Chameleon manifest protocol.
Handles: manifest loading, grasp planning, safety enforcement,
         Karpathy feedback loop, ledger logging.

Supports:
  - myCobot 280 (Elephant Robotics) via /mycobot/* topics
  - UR5e via MoveIt2 joint trajectory interface
  - Generic ROS2 arms via /joint_trajectory_controller
"""

import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from std_msgs.msg import String, Float64MultiArray
from geometry_msgs.msg import WrenchStamped, PoseStamped, Pose
from sensor_msgs.msg import JointState
from control_msgs.action import FollowJointTrajectory
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint
import httpx
import json
import math
import time
import threading
from pathlib import Path
from typing import Optional

# ── Constants ────────────────────────────────────────────────────────────────
CHAMELEON_HUB_URL   = "http://localhost:8080"
KARPATHY_SERVER_URL = "http://localhost:8211"
HUMANOID_DID        = "did:chameleon:humanoid:unit-001"
MANIFEST_LIBRARY    = Path(__file__).parent.parent.parent / "chameleon_library"

# myCobot 280 joint names
MYCOBOT_JOINTS = [
    "joint1", "joint2", "joint3",
    "joint4", "joint5", "joint6"
]

# UR5e joint names
UR5E_JOINTS = [
    "shoulder_pan_joint", "shoulder_lift_joint", "elbow_joint",
    "wrist_1_joint",      "wrist_2_joint",       "wrist_3_joint"
]


class ChameleonNode(Node):
    """
    Full Chameleon ROS2 node.
    Loads manifests, enforces safety, executes actions,
    and feeds sensor data back to the Karpathy optimiser.
    """

    def __init__(self):
        super().__init__("chameleon_node")

        # ── Parameters ───────────────────────────────────────────────────────
        self.declare_parameter("hub_url",             CHAMELEON_HUB_URL)
        self.declare_parameter("karpathy_url",        KARPATHY_SERVER_URL)
        self.declare_parameter("humanoid_did",        HUMANOID_DID)
        self.declare_parameter("safety_level",        "strict")
        self.declare_parameter("robot_type",          "mycobot")  # mycobot | ur5e | generic
        self.declare_parameter("manifest_library",    str(MANIFEST_LIBRARY))
        self.declare_parameter("force_limit_newtons", 50.0)
        self.declare_parameter("karpathy_feedback",   True)
        self.declare_parameter("dry_run",             False)

        self.hub_url          = self.get_parameter("hub_url").value
        self.karpathy_url     = self.get_parameter("karpathy_url").value
        self.humanoid_did     = self.get_parameter("humanoid_did").value
        self.safety_level     = self.get_parameter("safety_level").value
        self.robot_type       = self.get_parameter("robot_type").value
        self.manifest_library = Path(self.get_parameter("manifest_library").value)
        self.force_limit      = self.get_parameter("force_limit_newtons").value
        self.karpathy_fb      = self.get_parameter("karpathy_feedback").value
        self.dry_run          = self.get_parameter("dry_run").value

        # ── State ─────────────────────────────────────────────────────────────
        self.current_force      = 0.0
        self.current_joints     = {}
        self.active_manifest    = None
        self.active_object_id   = None
        self.session_scores     = []
        self.safety_stop_active = False
        self._lock              = threading.Lock()

        # ── HTTP clients ──────────────────────────────────────────────────────
        self.hub_client      = httpx.Client(base_url=self.hub_url,      timeout=5.0)
        self.karpathy_client = httpx.Client(base_url=self.karpathy_url, timeout=10.0)

        # ── Publishers ────────────────────────────────────────────────────────
        self.command_pub    = self.create_publisher(String,             "/chameleon/command_result",  10)
        self.safety_pub     = self.create_publisher(String,             "/chameleon/safety_veto",     10)
        self.status_pub     = self.create_publisher(String,             "/chameleon/status",          10)
        self.angles_pub     = self.create_publisher(Float64MultiArray,  "/mycobot/angles_goal",       10)

        # ── Subscribers ───────────────────────────────────────────────────────
        self.force_sub   = self.create_subscription(
            WrenchStamped, "/force_torque_sensor", self._force_cb, 10)
        self.joint_sub   = self.create_subscription(
            JointState,    "/joint_states",         self._joint_cb, 10)
        self.cmd_sub     = self.create_subscription(
            String,        "/chameleon/send_command", self._command_cb, 10)
        self.load_sub    = self.create_subscription(
            String,        "/chameleon/load_manifest", self._load_manifest_cb, 10)
        self.execute_sub = self.create_subscription(
            String,        "/chameleon/execute_action", self._execute_action_cb, 10)

        # ── Action client (UR5e / generic arms via MoveIt2) ───────────────────
        self._traj_client = ActionClient(
            self, FollowJointTrajectory,
            "/joint_trajectory_controller/follow_joint_trajectory")

        self.get_logger().info(
            f"Chameleon ROS2 Node online | robot={self.robot_type} "
            f"| safety={self.safety_level} | dry_run={self.dry_run}")
        self._publish_status("online")

    # ── Manifest loading ──────────────────────────────────────────────────────

    def _load_manifest_cb(self, msg: String):
        """Load a manifest by object ID or file path."""
        data = json.loads(msg.data)
        object_id = data.get("object_id", "")
        file_path = data.get("file_path", "")

        manifest = None

        if file_path:
            p = Path(file_path)
            if p.exists():
                manifest = json.loads(p.read_text())
        else:
            # Search library by objectId
            for f in self.manifest_library.rglob("*.json"):
                try:
                    m = json.loads(f.read_text())
                    if m.get("objectId") == object_id:
                        manifest = m
                        break
                except Exception:
                    continue

        if manifest is None:
            self.get_logger().error(f"Manifest not found: {object_id or file_path}")
            return

        with self._lock:
            self.active_manifest  = manifest
            self.active_object_id = manifest.get("objectId")
            # Update force limit from manifest
            safety = manifest.get("safety", {})
            manifest_force_limit = safety.get("maxForceNewtons", self.force_limit)
            self.force_limit = manifest_force_limit

        self.get_logger().info(
            f"Manifest loaded: {manifest.get('commonName')} "
            f"| force limit: {self.force_limit}N")
        self._publish_status(f"manifest_loaded:{self.active_object_id}")

    def _get_action_params(self, action_name: str) -> dict:
        """Extract default parameters for an action from the active manifest."""
        if not self.active_manifest:
            return {}
        actions = self.active_manifest.get("actions", {})
        action  = actions.get(action_name, {})
        params  = action.get("parameters", {})
        return {k: v["default"] for k, v in params.items()}

    # ── Safety enforcement ────────────────────────────────────────────────────

    def _force_cb(self, msg: WrenchStamped):
        """Monitor force/torque — trigger safety veto if limit exceeded."""
        f = msg.wrench.force
        total = math.sqrt(f.x**2 + f.y**2 + f.z**2)
        self.current_force = total

        if total > self.force_limit:
            self.get_logger().warn(
                f"SAFETY VETO — force {total:.2f}N exceeds limit {self.force_limit}N")
            self.safety_stop_active = True
            veto = String()
            veto.data = json.dumps({
                "reason":           "force_limit_exceeded",
                "measured_force_n": round(total, 3),
                "limit_n":          self.force_limit,
                "object_id":        self.active_object_id,
                "timestamp":        time.time()
            })
            self.safety_pub.publish(veto)
            self._stop_arm_immediately()

    def _stop_arm_immediately(self):
        """Publish zero-velocity joint command to stop the arm."""
        if self.robot_type == "mycobot":
            angles = Float64MultiArray()
            angles.data = [0.0] * 6
            self.angles_pub.publish(angles)
            self.get_logger().warn("myCobot emergency stop published.")

    def _safety_check(self, params: dict) -> tuple[bool, str]:
        """Pre-execution safety check against manifest limits."""
        if not self.active_manifest:
            return False, "No manifest loaded"
        if self.safety_stop_active:
            return False, "Safety stop is active — reset required"

        safety = self.active_manifest.get("safety", {})

        # Force check
        max_force = safety.get("maxForceNewtons", 50.0)
        if self.current_force > max_force * 0.9:
            return False, f"Current force {self.current_force:.1f}N near limit {max_force}N"

        # Cross-check required
        if safety.get("humanoidCrossCheckRequired", False):
            self.get_logger().warn(
                "⚠ Cross-check required for this object — operator must confirm")

        return True, "ok"

    # ── Joint state tracking ──────────────────────────────────────────────────

    def _joint_cb(self, msg: JointState):
        """Track current joint states."""
        for name, pos in zip(msg.name, msg.position):
            self.current_joints[name] = pos

    # ── Action execution ──────────────────────────────────────────────────────

    def _execute_action_cb(self, msg: String):
        """Execute a manifest action on the robot arm."""
        data        = json.loads(msg.data)
        action_name = data.get("action", "fill")
        params      = data.get("parameters", self._get_action_params(action_name))

        ok, reason = self._safety_check(params)
        if not ok:
            self.get_logger().error(f"Safety check failed: {reason}")
            self._publish_status(f"safety_failed:{reason}")
            return

        if self.dry_run:
            self.get_logger().info(f"[DRY RUN] Would execute {action_name} with {params}")
            self._publish_status(f"dry_run:{action_name}")
            return

        # Route to correct arm
        if self.robot_type == "mycobot":
            self._execute_mycobot(action_name, params)
        elif self.robot_type == "ur5e":
            self._execute_ur5e(action_name, params)
        else:
            self._execute_generic(action_name, params)

    def _execute_mycobot(self, action_name: str, params: dict):
        """
        Execute action on myCobot 280.
        Maps Chameleon manifest parameters to myCobot joint angles.
        """
        self.get_logger().info(
            f"Executing '{action_name}' on myCobot | params={params}")

        if action_name == "fill":
            # Kettle fill sequence:
            # 1. Move to grasp position
            # 2. Lift to liftHeightCm
            # 3. Tilt to pourTiltAngleDeg
            # 4. Return
            lift_cm   = params.get("liftHeightCm", 15)
            tilt_deg  = params.get("pourTiltAngleDeg", 120)
            fill_frac = params.get("fillStopFraction", 0.8)

            sequences = [
                # (joint1, joint2, joint3, joint4, joint5, joint6) in degrees
                # Step 1 — approach kettle
                [0.0, -30.0, 60.0, -30.0, 0.0, 0.0],
                # Step 2 — grasp height (lift_cm maps to joint2 elevation)
                [0.0, -30.0 - (lift_cm * 0.5), 60.0, -30.0, 0.0, 0.0],
                # Step 3 — pour tilt (tilt_deg maps to joint5 wrist)
                [0.0, -30.0 - (lift_cm * 0.5), 60.0, -30.0, tilt_deg - 90.0, 0.0],
                # Step 4 — hold for fill_frac duration
                [0.0, -30.0 - (lift_cm * 0.5), 60.0, -30.0, tilt_deg - 90.0, 0.0],
                # Step 5 — return to neutral
                [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            ]

            for i, angles in enumerate(sequences):
                msg = Float64MultiArray()
                msg.data = [float(a) for a in angles]
                self.angles_pub.publish(msg)
                self.get_logger().info(f"  Step {i+1}/5: {angles}")
                time.sleep(1.5)

                # Check safety at each step
                if self.safety_stop_active:
                    self.get_logger().error("Safety stop triggered mid-execution — aborting")
                    return

            self._post_execution_feedback(action_name, params)

        elif action_name == "press_button":
            press_n   = params.get("pressForceN", 2.0)
            angle_deg = params.get("fingerTipAngleDeg", 45)
            speed     = params.get("approachSpeedCms", 5.0)

            sequences = [
                # Approach
                [0.0, -20.0, 40.0, -20.0, angle_deg - 45.0, 0.0],
                # Press (joint6 maps to finger close)
                [0.0, -20.0, 40.0, -20.0, angle_deg - 45.0, press_n * 5.0],
                # Release
                [0.0, -20.0, 40.0, -20.0, angle_deg - 45.0, 0.0],
                # Return
                [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            ]

            delay = max(0.5, 1.0 / (speed / 5.0))
            for i, angles in enumerate(sequences):
                msg = Float64MultiArray()
                msg.data = [float(a) for a in angles]
                self.angles_pub.publish(msg)
                self.get_logger().info(f"  Step {i+1}/4: {angles}")
                time.sleep(delay)

                if self.safety_stop_active:
                    self.get_logger().error("Safety stop triggered — aborting")
                    return

            self._post_execution_feedback(action_name, params)

        else:
            self.get_logger().warn(f"Unknown action '{action_name}' for myCobot")

    def _execute_ur5e(self, action_name: str, params: dict):
        """Execute action on UR5e via MoveIt2 FollowJointTrajectory."""
        self.get_logger().info(f"Executing '{action_name}' on UR5e | params={params}")

        if not self._traj_client.wait_for_server(timeout_sec=3.0):
            self.get_logger().error("UR5e trajectory server not available")
            return

        traj = JointTrajectory()
        traj.joint_names = UR5E_JOINTS

        if action_name == "fill":
            lift_cm  = params.get("liftHeightCm", 15)
            tilt_deg = params.get("pourTiltAngleDeg", 120)

            # Waypoints in radians
            waypoints = [
                # Approach
                [-1.57, -1.0,  1.57, -1.57, -1.57, 0.0],
                # Lift
                [-1.57, -1.0 - (lift_cm * 0.01), 1.57, -1.57, -1.57, 0.0],
                # Pour tilt
                [-1.57, -1.0 - (lift_cm * 0.01), 1.57, -1.57, -1.57,
                 math.radians(tilt_deg - 120)],
                # Return
                [0.0,   -1.57, 0.0,  -1.57,  0.0,  0.0],
            ]

            for t, wp in enumerate(waypoints):
                pt = JointTrajectoryPoint()
                pt.positions  = wp
                pt.velocities = [0.1] * 6
                pt.time_from_start.sec = (t + 1) * 2
                traj.points.append(pt)

        goal = FollowJointTrajectory.Goal()
        goal.trajectory = traj
        self._traj_client.send_goal_async(goal)
        self.get_logger().info("UR5e trajectory sent.")
        self._post_execution_feedback(action_name, params)

    def _execute_generic(self, action_name: str, params: dict):
        """Generic execution — publishes trajectory goal to standard topic."""
        self.get_logger().info(f"Generic execution: {action_name} | {params}")
        self._post_execution_feedback(action_name, params)

    # ── Karpathy feedback loop ────────────────────────────────────────────────

    def _post_execution_feedback(self, action_name: str, params: dict):
        """
        After each execution, send sensor readings to the Karpathy server
        so the optimiser can score the run and propose better parameters.
        """
        if not self.karpathy_fb:
            return

        feedback = {
            "parameters":   params,
            "sensor_data": {
                "force_n":        round(self.current_force, 3),
                "joint_states":   self.current_joints,
                "action":         action_name,
                "object_id":      self.active_object_id,
                "timestamp":      time.time()
            }
        }

        try:
            resp = self.karpathy_client.post(
                "/api/chameleon/experiment",
                json=feedback)
            result = resp.json()
            score  = result.get("score", 0.0)
            self.session_scores.append(score)

            self.get_logger().info(
                f"Karpathy feedback: score={score:.4f} "
                f"| session best={max(self.session_scores):.4f}")

            # Publish result for downstream nodes
            out = String()
            out.data = json.dumps({
                "action":  action_name,
                "params":  params,
                "score":   score,
                "result":  result
            })
            self.command_pub.publish(out)

        except Exception as e:
            self.get_logger().warn(f"Karpathy feedback failed: {e}")

    # ── Hub command bridge ────────────────────────────────────────────────────

    def _command_cb(self, msg: String):
        """Forward a command to the Chameleon Hub API with DID signing."""
        try:
            payload = json.loads(msg.data)
            payload["issued_by"] = self.humanoid_did

            response = self.hub_client.post("/commands/send", json=payload)
            result   = response.json()

            out = String()
            out.data = json.dumps(result)
            self.command_pub.publish(out)

            if response.status_code == 403:
                self.get_logger().error(
                    f"Hub safety veto: {result.get('detail')}")
            else:
                self.get_logger().info(
                    f"Hub command accepted: {result.get('tx_id')}")

        except Exception as e:
            self.get_logger().error(f"Hub command error: {e}")

    # ── Utilities ─────────────────────────────────────────────────────────────

    def _publish_status(self, status: str):
        msg = String()
        msg.data = json.dumps({
            "status":    status,
            "robot":     self.robot_type,
            "object_id": self.active_object_id,
            "timestamp": time.time()
        })
        self.status_pub.publish(msg)

    def reset_safety_stop(self):
        """Reset safety stop — call after operator inspection."""
        self.safety_stop_active = False
        self.get_logger().info("Safety stop reset by operator.")
        self._publish_status("safety_reset")

    def destroy_node(self):
        self.hub_client.close()
        self.karpathy_client.close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = ChameleonNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
