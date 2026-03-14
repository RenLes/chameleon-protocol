"""
Chameleon ROS2 Launch File
Launches the Chameleon node alongside robot state publisher.
"""

from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package="chameleon_ros2",
            executable="chameleon_node",
            name="chameleon_node",
            output="screen",
            parameters=[{
                "hub_url": "http://localhost:8080",
                "safety_level": "strict",
                "humanoid_did": "did:chameleon:humanoid:unit-001"
            }]
        ),
        Node(
            package="robot_state_publisher",
            executable="robot_state_publisher",
            name="robot_state_publisher",
            output="screen"
        )
    ])
