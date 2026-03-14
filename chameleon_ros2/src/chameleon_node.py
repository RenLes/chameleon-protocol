#!/usr/bin/env python3
"""
Chameleon ROS2 Node (formerly UDAP)
Bridges ROS2 humanoid robot stack to Chameleon Hub API.
Handles: object detection, grasp planning, safety enforcement, ledger logging.
"""

import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from geometry_msgs.msg import WrenchStamped, PoseStamped
from sensor_msgs.msg import JointState
import httpx
import json
import asyncio
from typing import Optional

CHAMELEON_HUB_URL = "http://localhost:8080"
HUMANOID_DID = "did:chameleon:humanoid:unit-001"


class ChameleonNode(Node):
    """ROS2 node for Chameleon protocol integration."""

    def __init__(self):
        super().__init__("chameleon_node")
        self.get_logger().info("Chameleon ROS2 Node starting...")

        # Publishers
        self.command_pub = self.create_publisher(String, "/chameleon/command_result", 10)
        self.safety_pub = self.create_publisher(String, "/chameleon/safety_veto", 10)

        # Subscribers
        self.force_sub = self.create_subscription(
            WrenchStamped, "/force_torque_sensor", self.force_callback, 10
        )
        self.joint_sub = self.create_subscription(
            JointState, "/joint_states", self.joint_callback, 10
        )
        self.cmd_sub = self.create_subscription(
            String, "/chameleon/send_command", self.command_callback, 10
        )

        self.current_force = 0.0
        self.http_client = httpx.Client(base_url=CHAMELEON_HUB_URL, timeout=5.0)
        self.get_logger().info("Chameleon ROS2 Node online.")

    def force_callback(self, msg: WrenchStamped):
        """Monitor force/torque — trigger safety veto if limit exceeded."""
        total_force = (
            msg.wrench.force.x ** 2 +
            msg.wrench.force.y ** 2 +
            msg.wrench.force.z ** 2
        ) ** 0.5
        self.current_force = total_force

        if total_force > 50.0:
            self.get_logger().warn(f"Force limit exceeded: {total_force:.2f}N — triggering safety stop.")
            veto_msg = String()
            veto_msg.data = json.dumps({
                "reason": "force_limit_exceeded",
                "measured_force_n": total_force
            })
            self.safety_pub.publish(veto_msg)

    def joint_callback(self, msg: JointState):
        """Log joint states for Isaac Sim digital twin sync."""
        pass  # Telemetry hook — send to InfluxDB in production

    def command_callback(self, msg: String):
        """Receive a Chameleon command from the ROS2 topic and forward to Hub."""
        try:
            payload = json.loads(msg.data)
            payload["issued_by"] = HUMANOID_DID

            response = self.http_client.post("/commands/send", json=payload)
            result = response.json()

            out_msg = String()
            out_msg.data = json.dumps(result)
            self.command_pub.publish(out_msg)

            if response.status_code == 403:
                self.get_logger().error(f"Safety veto from Hub: {result.get('detail')}")
            else:
                self.get_logger().info(f"Command accepted: {result.get('tx_id')}")

        except Exception as e:
            self.get_logger().error(f"Command error: {str(e)}")

    def destroy_node(self):
        self.http_client.close()
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
