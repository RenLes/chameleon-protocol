"""
Chameleon ROS2 Launch File
Launches the Chameleon node alongside robot state publisher.

Usage:
    # myCobot 280
    ros2 launch chameleon_ros2 chameleon_launch.py robot_type:=mycobot

    # UR5e
    ros2 launch chameleon_ros2 chameleon_launch.py robot_type:=ur5e

    # Dry run (no arm movement)
    ros2 launch chameleon_ros2 chameleon_launch.py dry_run:=true
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([

        # ── Launch arguments ─────────────────────────────────────────────────
        DeclareLaunchArgument("robot_type",   default_value="mycobot",
                              description="Robot type: mycobot | ur5e | generic"),
        DeclareLaunchArgument("hub_url",      default_value="http://localhost:8080",
                              description="Chameleon Hub URL"),
        DeclareLaunchArgument("karpathy_url", default_value="http://localhost:8211",
                              description="Karpathy experiment server URL"),
        DeclareLaunchArgument("safety_level", default_value="strict",
                              description="Safety level: strict | moderate | permissive"),
        DeclareLaunchArgument("dry_run",      default_value="false",
                              description="Dry run — no arm movement"),
        DeclareLaunchArgument("karpathy_feedback", default_value="true",
                              description="Enable Karpathy feedback loop"),

        # ── Chameleon node ────────────────────────────────────────────────────
        Node(
            package="chameleon_ros2",
            executable="chameleon_node",
            name="chameleon_node",
            output="screen",
            parameters=[{
                "robot_type":         LaunchConfiguration("robot_type"),
                "hub_url":            LaunchConfiguration("hub_url"),
                "karpathy_url":       LaunchConfiguration("karpathy_url"),
                "safety_level":       LaunchConfiguration("safety_level"),
                "dry_run":            LaunchConfiguration("dry_run"),
                "karpathy_feedback":  LaunchConfiguration("karpathy_feedback"),
                "humanoid_did":       "did:chameleon:humanoid:unit-001",
                "force_limit_newtons": 15.0,
            }]
        ),

        # ── Robot state publisher ─────────────────────────────────────────────
        Node(
            package="robot_state_publisher",
            executable="robot_state_publisher",
            name="robot_state_publisher",
            output="screen"
        ),
    ])
