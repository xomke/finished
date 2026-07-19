"""Main-thread scheduling for explicit verified update package downloads."""

import json
import threading
import time

from . import addon_preferences
from . import local_log
from . import render_handlers
from . import update_checker
from . import update_download
from . import update_install_handoff
from .release_metadata import ReleaseMetadataError, compare_versions, parse_release_metadata
from .version import ADDON_VERSION_STRING


BUSY_RECHECK_SECONDS = 1.0

_lock = threading.Lock()
_thread = None
_pending_request = None
_pending_result = None
_timer_registered = False
_stopped = False
_generation = 0


def register():
    return None


def unregister():
    stop()


def stop():
    global _pending_request, _pending_result, _timer_registered, _stopped, _generation
    _stopped = True
    _generation += 1
    with _lock:
        _pending_request = None
        _pending_result = None
    try:
        import bpy

        timers = bpy.app.timers
        if _timer_registered and timers.is_registered(_timer_callback):
            timers.unregister(_timer_callback)
    except Exception:
        pass
    _timer_registered = False


def request_download(preferences):
    """Queue a validated release for background download without starting network work here."""

    if _has_running_download() or _has_pending_request() or render_handlers.current_session() is not None:
        return False
    metadata = _metadata_from_preferences(preferences)
    if metadata is None:
        return False
    global _pending_request, _stopped
    with _lock:
        _pending_request = metadata
    _stopped = False
    preferences.update_download_state = "queued"
    preferences.update_download_error = ""
    _schedule_timer_soon()
    return True


def _timer_callback():
    global _timer_registered, _pending_request
    _timer_registered = False
    if _stopped:
        return None

    preferences = addon_preferences.current_preferences()
    if preferences is None:
        return None
    _apply_pending_result(preferences)
    if _has_running_download():
        _ensure_timer_registered(BUSY_RECHECK_SECONDS)
        return None
    with _lock:
        metadata = _pending_request
        _pending_request = None
    if metadata is None:
        return None
    if render_handlers.current_session() is not None:
        _write_failure(preferences, "render_active")
        return None
    _start_download(preferences, metadata)
    _ensure_timer_registered(BUSY_RECHECK_SECONDS)
    return None


def _start_download(preferences, metadata):
    global _thread
    preferences.update_download_state = "downloading"
    blender_version = _blender_version()
    with _lock:
        generation = _generation
    thread = threading.Thread(
        target=_run_download,
        args=(metadata, blender_version, generation),
        name="FinishedUpdateDownload",
        daemon=True,
    )
    with _lock:
        _thread = thread
    thread.start()


def _run_download(metadata, blender_version, generation):
    result = update_download.download_and_verify(metadata, blender_version)
    with _lock:
        global _pending_result
        if _stopped or generation != _generation:
            return
        _pending_result = result


def _apply_pending_result(preferences):
    global _pending_result, _thread
    with _lock:
        result = _pending_result
        _pending_result = None
        if _thread is not None and not _thread.is_alive():
            _thread = None
    if result is None:
        return
    if result.prepared:
        _start_post_exit_install(preferences, result.path)
        return
    _write_failure(preferences, result.error or "download_failed")


def _start_post_exit_install(preferences, package_path):
    """Arm the safe post-exit installer after the explicit download action succeeds."""

    try:
        import bpy
    except ImportError:
        _write_failure(preferences, "post_exit_install_unavailable")
        return
    handoff = update_install_handoff.start_handoff(
        package_path, package_name=__package__, bpy_module=bpy
    )
    if not handoff.started:
        _write_failure(preferences, handoff.error or "post_exit_install_unavailable")
        return
    preferences.update_download_state = "install_pending_exit"
    preferences.update_prepared_package_path = str(package_path)
    preferences.update_install_result_path = handoff.result_path
    preferences.update_install_helper_pid = handoff.helper_pid
    preferences.update_download_error = ""
    local_log.info("Finished? update downloaded, verified, and prepared for installation after Blender exits.")


def _write_failure(preferences, error):
    preferences.update_download_state = "download_failed"
    preferences.update_download_error = error
    preferences.update_prepared_package_path = ""
    preferences.update_install_result_path = ""
    preferences.update_install_helper_pid = 0
    local_log.info(f"Finished? update package was not prepared: error={error}")


def _metadata_from_preferences(preferences):
    if getattr(preferences, "update_check_state", "") != update_checker.CHECK_UPDATE_AVAILABLE:
        return None
    data = {
        "schema_version": 1,
        "channel": getattr(preferences, "update_latest_channel", ""),
        "status": getattr(preferences, "update_latest_status", ""),
        "version": getattr(preferences, "update_latest_version", ""),
        "download_url": getattr(preferences, "update_latest_download_url", ""),
        "sha256": getattr(preferences, "update_latest_sha256", ""),
    }
    minimum = getattr(preferences, "update_latest_min_blender_version", "")
    if minimum:
        data["min_blender_version"] = minimum
    for field in ("notes_ru", "notes_en"):
        value = getattr(preferences, f"update_latest_{field}", "")
        if value:
            data[field] = value
    try:
        metadata = parse_release_metadata(json.dumps(data))
        if compare_versions(metadata.version, ADDON_VERSION_STRING) <= 0:
            return None
        return metadata
    except ReleaseMetadataError:
        return None


def _has_running_download():
    with _lock:
        return _thread is not None and _thread.is_alive()


def _has_pending_request():
    with _lock:
        return _pending_request is not None


def _schedule_timer_soon():
    global _timer_registered
    try:
        import bpy

        timers = bpy.app.timers
        if _timer_registered and timers.is_registered(_timer_callback):
            timers.unregister(_timer_callback)
        _timer_registered = False
    except Exception:
        _timer_registered = False
    _ensure_timer_registered(0.0)


def _ensure_timer_registered(first_interval):
    global _timer_registered
    if _timer_registered or _stopped:
        return
    try:
        import bpy

        bpy.app.timers.register(_timer_callback, first_interval=first_interval)
        _timer_registered = True
    except Exception as exc:
        local_log.warning(f"Finished? update download timer failed: {exc}")


def _blender_version():
    try:
        import bpy

        version = getattr(bpy.app, "version", ())
        return ".".join(str(part) for part in version[:3]) if version else ""
    except (AttributeError, ImportError, TypeError):
        return ""
