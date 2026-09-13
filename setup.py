import os
import re

from setuptools import find_packages, setup


def read_requirements():
    req_file = os.path.join(os.path.dirname(__file__), "requirements.txt")
    if not os.path.exists(req_file):
        return []
    with open(req_file, encoding="utf-8") as f:
        lines = f.readlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]

def read_version():
    init_path = os.path.join(os.path.dirname(__file__), "backend", "__init__.py")
    with open(init_path, encoding="utf-8") as f:
        match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', f.read(), re.MULTILINE)
    if match:
        return match.group(1)
    raise RuntimeError("__version__ not found in backend/__init__.py")

setup(
    name="backend",
    version=read_version(),
    description=("Web dashboard for managing and monitoring a remote vLLM "
                 "inference service: model scan/start/stop/download, "
                 "hardware monitoring, SSH console"),
    author="vLLM-Dashboard",
    packages=find_packages(exclude=["tests", "tests.*"]),
    install_requires=read_requirements(),
    entry_points={
        "console_scripts": [
            "backend=backend.main:main",
        ],
    },
    python_requires=">=3.10",
)
