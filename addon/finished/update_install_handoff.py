"""Launch the post-exit installer without replacing a loaded extension."""

from dataclasses import dataclass
import json
from pathlib import Path
import os
import subprocess
import sys

from .release_metadata import ReleaseMetadataError, compare_versions
from . import update_monitor


EXPECTED_EXTENSION_ID = "finished"
SYSTEM_PYTHON = Path("/usr/bin/python3")
RECONCILIATION_POLL_SECONDS = 1.0
_reconciliation_callback = None


@dataclass(frozen=True)
class HandoffResult:
    started: bool
    error: str = ""
    result_path: str = ""
    helper_pid: int = 0


def repository_module_for_package(package_name, repositories):
    parts = package_name.split(".")
    if len(parts) != 3 or parts[0] != "bl_ext" or parts[2] != EXPECTED_EXTENSION_ID:
        return ""
    module = parts[1]
    return module if any(getattr(repo, "module", "") == module for repo in repositories) else ""


def start_handoff(package_path, *, package_name, bpy_module, popen=subprocess.Popen, process_id=None):
    """Start a detached helper. It waits; this function never installs the package itself."""

    if sys.platform != "darwin":
        return HandoffResult(False, "post_exit_install_unsupported")
    package = Path(package_path)
    if not package_path or not package.is_absolute() or package.suffix.lower() != ".zip" or not package.is_file():
        return HandoffResult(False, "prepared_package_missing")
    repositories = getattr(getattr(bpy_module.context.preferences, "extensions", None), "repos", ())
    repository = repository_module_for_package(package_name, repositories)
    helper = Path(__file__).with_name("post_exit_extension_install.py")
    blender_binary = Path(getattr(bpy_module.app, "binary_path", ""))
    # Blender terminates its bundled Python as part of app shutdown.  The
    # detached helper must therefore use macOS' independent system Python.
    python_binary = SYSTEM_PYTHON
    if not repository or not helper.is_file() or not blender_binary.is_file() or not python_binary.is_file():
        return HandoffResult(False, "post_exit_install_unavailable")
    result_path = package.with_suffix(".install-result.json")
    command = [str(python_binary), str(helper), "--wait-pid", str(process_id or os.getpid()),
               "--blender", str(blender_binary), "--repo", repository, "--package", str(package),
               "--result-file", str(result_path)]
    try:
        helper_process = popen(command, close_fds=True, start_new_session=True)
    except OSError:
        return HandoffResult(False, "post_exit_install_launch_failed")
    helper_pid = getattr(helper_process, "pid", 0)
    return HandoffResult(True, result_path=str(result_path), helper_pid=helper_pid if helper_pid > 0 else 0)


def reconcile_after_start(preferences, current_version, *, process_exists=None):
    """Apply the helper receipt after a later Blender start without trusting arbitrary files."""

    if getattr(preferences, "update_download_state", "") != "install_pending_exit":
        return ""
    process_exists = process_exists or _helper_process_is_running
    result_path = Path(getattr(preferences, "update_install_result_path", ""))
    if not result_path.is_file() or result_path.stat().st_size > 1024:
        if process_exists(getattr(preferences, "update_install_helper_pid", 0)):
            return ""
        _clear_pending_handoff(preferences)
        preferences.update_download_state = "download_failed"
        preferences.update_download_error = "install_failed"
        return "failed"
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    _remove_file(result_path)
    _clear_pending_handoff(preferences)
    target_version = getattr(preferences, "update_latest_version", "")
    installed = result == {"schema_version": 1, "status": "installed"}
    try:
        version_matches = compare_versions(current_version, target_version) >= 0
    except ReleaseMetadataError:
        version_matches = False
    _remove_file(getattr(preferences, "update_prepared_package_path", ""))
    preferences.update_prepared_package_path = ""
    if installed and version_matches:
        preferences.update_download_state = "not_downloaded"
        preferences.update_download_error = ""
        update_monitor.clear_available_update(preferences)
        return "installed"
    preferences.update_download_state = "download_failed"
    preferences.update_download_error = "install_failed"
    return "failed"


def _clear_pending_handoff(preferences):
    preferences.update_install_result_path = ""
    preferences.update_install_helper_pid = 0


def _process_exists(process_id):
    if not isinstance(process_id, int) or process_id <= 0:
        return False
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _helper_process_is_running(process_id):
    if not _process_exists(process_id):
        return False
    if sys.platform != "darwin":
        return True
    try:
        completed = subprocess.run(
            ["ps", "-o", "command=", "-p", str(process_id)],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    return completed.returncode == 0 and "post_exit_extension_install.py" in completed.stdout


def schedule_reconciliation(preferences, current_version, bpy_module, *, reconcile=reconcile_after_start):
    """Keep checking briefly after startup when the helper finishes after Blender reopens."""

    global _reconciliation_callback
    timers = getattr(getattr(bpy_module, "app", None), "timers", None)
    if timers is None or _reconciliation_callback is not None:
        return False

    def callback():
        global _reconciliation_callback
        if reconcile(preferences, current_version) == "":
            return RECONCILIATION_POLL_SECONDS
        _reconciliation_callback = None
        return None

    try:
        timers.register(callback, first_interval=RECONCILIATION_POLL_SECONDS)
    except Exception:
        return False
    _reconciliation_callback = callback
    return True


def cancel_reconciliation(bpy_module=None):
    global _reconciliation_callback
    if _reconciliation_callback is None:
        return
    try:
        if bpy_module is None:
            import bpy as bpy_module
        timers = bpy_module.app.timers
        if timers.is_registered(_reconciliation_callback):
            timers.unregister(_reconciliation_callback)
    except Exception:
        pass
    _reconciliation_callback = None


def _remove_file(path):
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass
