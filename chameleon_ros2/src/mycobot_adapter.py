#!/usr/bin/env python3
"""
Chameleon myCobot 280 Adapter
Direct Python SDK bridge — bypasses ROS2 for simple single-arm setups.
Uses pymycobot library to control myCobot 280 directly from Chameleon manifests.

Usage:
    python mycobot_adapter.py --manifest ../../chameleon_library/kitchen/stovetop_kettle_manifest.json
    python mycobot_adapter.py --manifest ../../chameleon_library/living_room/remote_control_manifest.json --dry-run
"""

import json
import time
import argparse
import threading
from pathlib import Path

try:
    from pymycobot.mycobot import MyCobot
    MYCOBOT_AVAILABLE = True
except ImportError:
    MYCOBOT_AVAILABLE = False
    print("[WARN] pymycobot not installed — running in dry-run mode")
    print("       Install with: pip install pymycobot")


class ChameleonMyCobotAdapter:
    """
    Connects a Chameleon manifest directly to a myCobot 280 arm.
    Reads safe parameter ranges from the manifest and executes actions.
    """

    # myCobot 280 joint limits (degrees)
    JOINT_LIMITS = {
        "joint1": (-165, 165),
        "joint2": (-165, 165),
        "joint3": (-165, 165),
        "joint4": (-165, 165),
        "joint5": (-165, 165),
        "joint6": (-175, 175),
    }

    # Serial port defaults by OS
    PORT_DEFAULTS = {
        "darwin": "/dev/ttyUSB0",   # macOS
        "linux":  "/dev/ttyUSB0",   # Linux
        "win32":  "COM3",           # Windows
    }

    def __init__(self, manifest_path: str, port: str = None,
                 baud: int = 115200, dry_run: bool = False):
        self.manifest   = json.loads(Path(manifest_path).read_text())
        self.dry_run    = dry_run or not MYCOBOT_AVAILABLE
        self.arm        = None
        self._lock      = threading.Lock()
        self.last_score = None

        # Load safety limits from manifest
        safety = self.manifest.get("safety", {})
        self.max_force_n = safety.get("maxForceNewtons", 15.0)
        self.cross_check = safety.get("humanoidCrossCheckRequired", False)

        print(f"[Chameleon] Loaded: {self.manifest.get('commonName')}")
        print(f"[Chameleon] Max force: {self.max_force_n}N | "
              f"Cross-check: {self.cross_check}")

        if not self.dry_run:
            import sys
            detected_port = port or self.PORT_DEFAULTS.get(sys.platform, "/dev/ttyUSB0")
            print(f"[Chameleon] Connecting to myCobot on {detected_port}...")
            self.arm = MyCobot(detected_port, baud)
            time.sleep(0.5)
            print("[Chameleon] myCobot connected ✅")
        else:
            print("[Chameleon] DRY RUN mode — no arm movement")

    def get_action_params(self, action_name: str) -> dict:
        """Get default parameters for an action from the manifest."""
        actions = self.manifest.get("actions", {})
        action  = actions.get(action_name, {})
        params  = action.get("parameters", {})
        return {k: v["default"] for k, v in params.items()}

    def clamp_joints(self, angles: list) -> list:
        """Clamp joint angles to myCobot safe limits."""
        clamped = []
        for i, (name, (lo, hi)) in enumerate(self.JOINT_LIMITS.items()):
            val = angles[i] if i < len(angles) else 0.0
            clamped.append(max(lo, min(hi, val)))
        return clamped

    def send_angles(self, angles: list, speed: int = 30, label: str = ""):
        """Send joint angles to myCobot with safety clamping."""
        safe_angles = self.clamp_joints(angles)
        if label:
            print(f"  [{label}] angles={[round(a, 1) for a in safe_angles]}")
        if not self.dry_run and self.arm:
            self.arm.send_angles(safe_angles, speed)

    def execute_fill(self, params: dict = None) -> dict:
        """
        Execute kettle fill action.
        Maps manifest parameters to myCobot 280 joint sequence.
        """
        if params is None:
            params = self.get_action_params("fill")

        lift_cm   = params.get("liftHeightCm", 15)
        tilt_deg  = params.get("pourTiltAngleDeg", 120)
        fill_frac = params.get("fillStopFraction", 0.8)

        if self.cross_check:
            print("⚠  Cross-check required — confirm safe to proceed (y/n): ", end="")
            if input().strip().lower() != "y":
                print("[Chameleon] Execution cancelled by operator.")
                return {"cancelled": True}

        print(f"\n[Chameleon] Executing FILL | "
              f"lift={lift_cm}cm tilt={tilt_deg}° fill={fill_frac}")

        # Home position
        self.send_angles([0, 0, 0, 0, 0, 0], speed=20, label="HOME")
        time.sleep(1.5)

        # Approach kettle handle
        self.send_angles([0, -30, 60, -30, 0, 0], speed=25, label="APPROACH")
        time.sleep(1.5)

        # Lift to manifest liftHeightCm
        lift_joint2 = -30 - (lift_cm * 0.8)
        self.send_angles([0, lift_joint2, 60, -30, 0, 0], speed=20, label="LIFT")
        time.sleep(1.5)

        # Pour tilt
        tilt_joint5 = tilt_deg - 120
        self.send_angles([0, lift_joint2, 60, -30, tilt_joint5, 0],
                         speed=15, label="POUR")

        # Hold for fill_frac duration (scaled 1-3 seconds)
        hold_time = 1.0 + (fill_frac * 2.0)
        print(f"  [HOLD] Pouring for {hold_time:.1f}s (fill_frac={fill_frac})")
        time.sleep(hold_time)

        # Return to neutral
        self.send_angles([0, -30, 60, -30, 0, 0], speed=20, label="RETRACT")
        time.sleep(1.0)
        self.send_angles([0, 0, 0, 0, 0, 0], speed=25, label="HOME")
        time.sleep(1.5)

        print("[Chameleon] FILL complete ✅")
        return {
            "action":   "fill",
            "params":   params,
            "success":  True,
            "duration": hold_time
        }

    def execute_press_button(self, params: dict = None) -> dict:
        """
        Execute remote control button press.
        Maps manifest parameters to myCobot joint sequence.
        """
        if params is None:
            params = self.get_action_params("press_button")

        force_n   = params.get("pressForceN", 2.0)
        angle_deg = params.get("fingerTipAngleDeg", 45)
        speed_cms = params.get("approachSpeedCms", 5.0)

        approach_speed = max(10, min(50, int(speed_cms * 5)))

        print(f"\n[Chameleon] Executing PRESS_BUTTON | "
              f"force={force_n}N angle={angle_deg}° speed={speed_cms}cm/s")

        # Home
        self.send_angles([0, 0, 0, 0, 0, 0], speed=20, label="HOME")
        time.sleep(1.0)

        # Position finger over button
        finger_joint5 = angle_deg - 45
        self.send_angles([0, -20, 40, -20, finger_joint5, 0],
                         speed=approach_speed, label="POSITION")
        time.sleep(1.0)

        # Press — joint6 closes finger
        press_joint6 = force_n * 8
        self.send_angles([0, -20, 40, -20, finger_joint5, press_joint6],
                         speed=approach_speed, label="PRESS")
        time.sleep(0.3)

        # Release
        self.send_angles([0, -20, 40, -20, finger_joint5, 0],
                         speed=20, label="RELEASE")
        time.sleep(0.5)

        # Return home
        self.send_angles([0, 0, 0, 0, 0, 0], speed=25, label="HOME")
        time.sleep(1.0)

        print("[Chameleon] PRESS_BUTTON complete ✅")
        return {
            "action":  "press_button",
            "params":  params,
            "success": True
        }

    def execute_action(self, action_name: str, params: dict = None) -> dict:
        """Route to correct action executor."""
        dispatch = {
            "fill":         self.execute_fill,
            "press_button": self.execute_press_button,
        }
        fn = dispatch.get(action_name)
        if fn is None:
            print(f"[Chameleon] Unknown action: {action_name}")
            return {"error": f"Unknown action: {action_name}"}
        return fn(params)

    def run_karpathy_session(self, action_name: str,
                              iterations: int = 20,
                              karpathy_url: str = "http://localhost:8211"):
        """
        Run a full Karpathy optimisation session on the physical arm.
        Each iteration executes a real action and gets scored.
        """
        import urllib.request
        import random

        manifest_actions = self.manifest.get("actions", {})
        action           = manifest_actions.get(action_name, {})
        param_defs       = action.get("parameters", {})
        params           = {k: v["default"] for k, v in param_defs.items()}
        best_params      = dict(params)
        best_score       = -999
        scores           = []

        print(f"\n{'='*55}")
        print(f"  Chameleon Karpathy Session — {self.manifest.get('commonName')}")
        print(f"  Action: {action_name} | Iterations: {iterations}")
        print(f"{'='*55}\n")

        for i in range(1, iterations + 1):
            # Execute on real arm
            result = self.execute_action(action_name, params)

            # Send to Karpathy server for scoring
            try:
                payload = json.dumps({"parameters": params}).encode()
                req = urllib.request.Request(
                    f"{karpathy_url}/api/chameleon/experiment",
                    data=payload,
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    scored = json.loads(r.read())
                score = scored.get("score", 0.0)
            except Exception as e:
                print(f"[{i:03d}] Karpathy server error: {e}")
                score = 0.0

            if score > best_score:
                best_score  = score
                best_params = dict(params)
                marker = " ◀ BEST"
            else:
                marker = ""

            scores.append(score)
            print(f"[{i:03d}] score={score:+.4f} params={params}{marker}")

            # Propose next change
            field = random.choice(list(params.keys()))
            if field in param_defs:
                cfg   = param_defs[field]
                step  = (cfg["max"] - cfg["min"]) * 0.1
                delta = random.choice([-step, step])
                params = dict(best_params)
                params[field] = round(
                    max(cfg["min"], min(cfg["max"], best_params[field] + delta)), 3)

        print(f"\n{'='*55}")
        print(f"  BEST SCORE : {best_score:+.4f}")
        print(f"  BEST PARAMS: {json.dumps(best_params, indent=4)}")
        print(f"{'='*55}\n")
        return best_params, best_score


def main():
    parser = argparse.ArgumentParser(
        description="Chameleon myCobot 280 Adapter")
    parser.add_argument("--manifest",   required=True,
                        help="Path to Chameleon manifest JSON")
    parser.add_argument("--action",     default="fill",
                        help="Action to execute (fill, press_button)")
    parser.add_argument("--port",       default=None,
                        help="Serial port (default: auto-detect)")
    parser.add_argument("--dry-run",    action="store_true",
                        help="Simulate without moving arm")
    parser.add_argument("--karpathy",   action="store_true",
                        help="Run full Karpathy optimisation session")
    parser.add_argument("--iterations", type=int, default=20,
                        help="Karpathy iterations (default: 20)")
    args = parser.parse_args()

    adapter = ChameleonMyCobotAdapter(
        manifest_path=args.manifest,
        port=args.port,
        dry_run=args.dry_run
    )

    if args.karpathy:
        adapter.run_karpathy_session(
            action_name=args.action,
            iterations=args.iterations)
    else:
        result = adapter.execute_action(args.action)
        print(f"\nResult: {json.dumps(result, indent=2)}")


if __name__ == "__main__":
    main()
