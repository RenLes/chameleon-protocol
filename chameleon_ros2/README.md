# Chameleon ROS2 Bridge

Connects any ROS2 robot arm to the Chameleon manifest protocol.

## Supported Hardware

| Robot | Status | Notes |
|-------|--------|-------|
| **myCobot 280** (Elephant Robotics) | ✅ Phase 1 complete | Direct SDK + ROS2 |
| **UR5e** (Universal Robots) | ✅ Phase 1 complete | MoveIt2 trajectory |
| **Unitree G1** | 🔄 Planned Phase 2 | Full humanoid |
| Generic ROS2 arm | ✅ Generic adapter | Joint trajectory |

## Quick Start

### myCobot 280 (no ROS2 required)
```bash
pip install pymycobot

# Dry run — test without moving arm
python src/mycobot_adapter.py \
  --manifest ../chameleon_library/kitchen/stovetop_kettle_manifest.json \
  --dry-run

# Real arm — fill kettle
python src/mycobot_adapter.py \
  --manifest ../chameleon_library/kitchen/stovetop_kettle_manifest.json \
  --action fill

# Real arm — press remote control button
python src/mycobot_adapter.py \
  --manifest ../chameleon_library/living_room/remote_control_manifest.json \
  --action press_button

# Run Karpathy optimisation on real arm (20 iterations)
python src/mycobot_adapter.py \
  --manifest ../chameleon_library/kitchen/stovetop_kettle_manifest.json \
  --karpathy --iterations 20
```

### ROS2 (full stack)
```bash
# Build
cd ~/ros2_ws
colcon build --packages-select chameleon_ros2

# Launch (myCobot)
ros2 launch chameleon_ros2 chameleon_launch.py robot_type:=mycobot

# Launch (UR5e)
ros2 launch chameleon_ros2 chameleon_launch.py robot_type:=ur5e

# Load a manifest
ros2 topic pub /chameleon/load_manifest std_msgs/String \
  '{"data": "{\"object_id\": \"did:chameleon:kitchen:stovetop-kettle-v1\"}"}'

# Execute an action
ros2 topic pub /chameleon/execute_action std_msgs/String \
  '{"data": "{\"action\": \"fill\"}"}'
```

## Architecture

```
Chameleon Manifest
      ↓
chameleon_node.py          ← ROS2 node (safety, routing, Karpathy feedback)
      ↓
mycobot_adapter.py         ← myCobot 280 direct SDK
      ↓
/mycobot/angles_goal       ← ROS2 topic → physical arm
      ↓
Force/torque sensor        ← feedback → safety veto if exceeded
      ↓
Karpathy server            ← score run → propose better params
```

## ROS2 Topics

| Topic | Type | Direction | Purpose |
|-------|------|-----------|---------|
| `/chameleon/load_manifest` | String | IN | Load object manifest |
| `/chameleon/execute_action` | String | IN | Execute action |
| `/chameleon/send_command` | String | IN | Hub command bridge |
| `/chameleon/command_result` | String | OUT | Execution result |
| `/chameleon/safety_veto` | String | OUT | Safety stop signal |
| `/chameleon/status` | String | OUT | Node status |
| `/mycobot/angles_goal` | Float64MultiArray | OUT | Joint angles |
| `/force_torque_sensor` | WrenchStamped | IN | Force monitoring |
| `/joint_states` | JointState | IN | Joint state feedback |
