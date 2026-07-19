import os
import platform
import socket
import subprocess


FALLBACK_DEVICE_NAME = "Local Blender"
MAX_DEVICE_NAME_LENGTH = 80


def local_device_name(hostname=None, environment=None):
    """Return a concise, user-recognizable name for this Blender computer."""
    environment = environment if environment is not None else os.environ
    if environment.get("COMPUTERNAME"):
        candidate = environment["COMPUTERNAME"]
    elif platform.system() == "Darwin":
        candidate = _macos_computer_name() or hostname or socket.gethostname()
    elif hostname is not None:
        candidate = hostname
    else:
        candidate = socket.gethostname()
    name = str(candidate or "").strip()
    if name.lower().endswith(".local"):
        name = name[:-6]
    return name[:MAX_DEVICE_NAME_LENGTH] or FALLBACK_DEVICE_NAME


def _macos_computer_name():
    try:
        result = subprocess.run(
            ["scutil", "--get", "ComputerName"],
            capture_output=True,
            check=False,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""
