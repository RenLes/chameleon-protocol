from setuptools import setup, find_packages
import os
from glob import glob

package_name = "chameleon_ros2"

setup(
    name=package_name,
    version="1.0.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages",
            ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (os.path.join("share", package_name, "launch"),
            glob("launch/*.py")),
        (os.path.join("share", package_name, "config"),
            glob("config/*.yaml")),
    ],
    install_requires=[
        "setuptools",
        "httpx",
        "pymycobot",
    ],
    zip_safe=True,
    maintainer="David Qicatabua",
    maintainer_email="RenLesApps@outlook.com",
    description="Chameleon Protocol ROS2 bridge for humanoid robot arms",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "chameleon_node    = chameleon_ros2.src.chameleon_node:main",
            "mycobot_adapter   = chameleon_ros2.src.mycobot_adapter:main",
        ],
    },
)
