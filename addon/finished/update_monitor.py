"""Schedule bounded add-on update checks after Blender registration has completed."""

import threading
import time

from . import addon_preferences
from . import local_log
from . import update_checker
from . import update_notifications
from . import render_handlers
from .version import ADDON_VERSION_STRING


AUTOMATIC_CHECK_INTERVAL_SECONDS = 60 * 60
STARTUP_DELAY_SECONDS = 10.0
STARTUP_NOTICE_DELAY_SECONDS = 0.1
IDLE_RECHECK_SECONDS = 60.0
BUSY_RECHECK_SECONDS = 1.0
RENDER_RECHECK_SECONDS = 60.0


_lock = threading.Lock()
_thread = None
_pending_result = None
_timer_registered = False
_stopped = True
_generation = 0
_manual_check_requested = False
_startup_check_pending = False
_startup_notice_pending = False
_startup_notice_timer_registered = False


def register():
    global _startup_check_pending
    global _startup_notice_pending
    _startup_check_pending = True
    _startup_notice_pending = True
    start(initial=True)
    _ensure_startup_notice_timer_registered()


def unregister():
    stop()


def start(*, initial=False):
    global _stopped
    _stopped = False
    _ensure_timer_registered(STARTUP_DELAY_SECONDS if initial else 0.0)


def auto_check_setting_changed(preferences):
    """Resume scheduling when the user re-enables automatic checks."""

    if getattr(preferences, "auto_check_updates", True):
        start(initial=False)


def stop():
    global _stopped
    global _timer_registered
    global _generation
    global _manual_check_requested
    global _startup_check_pending
    global _startup_notice_pending
    global _startup_notice_timer_registered

    _stopped = True
    _manual_check_requested = False
    _startup_check_pending = False
    _startup_notice_pending = False
    _generation += 1
    _discard_pending_result()
    try:
        import bpy

        timers = bpy.app.timers
        if _timer_registered and timers.is_registered(_timer_callback):
            timers.unregister(_timer_callback)
        if _startup_notice_timer_registered and timers.is_registered(_startup_notice_timer_callback):
            timers.unregister(_startup_notice_timer_callback)
    except Exception:
        pass
    _timer_registered = False
    _startup_notice_timer_registered = False


def request_manual_check():
    """Queue one explicit check and return immediately without overlapping workers."""

    global _stopped
    global _manual_check_requested
    if _has_running_check():
        return False
    _stopped = False
    _manual_check_requested = True
    _schedule_timer_soon()
    return True


def _timer_callback():
    global _timer_registered
    _timer_registered = False

    if _stopped:
        return None

    preferences = addon_preferences.current_preferences()
    if preferences is None:
        _ensure_timer_registered(IDLE_RECHECK_SECONDS)
        return None

    _notify_startup_available_update(preferences)
    _apply_pending_result(preferences)
    if _has_running_check():
        _ensure_timer_registered(BUSY_RECHECK_SECONDS)
        return None

    if render_handlers.current_session() is not None:
        _ensure_timer_registered(RENDER_RECHECK_SECONDS)
        return None

    now = time.time()
    global _manual_check_requested
    global _startup_check_pending
    manual_check_requested = _manual_check_requested
    startup_check_pending = _startup_check_pending
    if not manual_check_requested and not startup_check_pending and not getattr(preferences, "auto_check_updates", True):
        return None
    due_at = next_check_at(preferences)
    if not manual_check_requested and not startup_check_pending and now < due_at:
        _ensure_timer_registered(max(BUSY_RECHECK_SECONDS, due_at - now))
        return None

    _manual_check_requested = False
    _startup_check_pending = False
    _start_check(preferences, now)
    _ensure_timer_registered(BUSY_RECHECK_SECONDS)
    return None


def next_check_at(preferences):
    """Return the earliest automatic-check timestamp from persistent safe state."""

    last_attempt_at = max(0.0, float(getattr(preferences, "update_last_attempt_at", 0.0)))
    if not last_attempt_at:
        return 0.0
    return last_attempt_at + AUTOMATIC_CHECK_INTERVAL_SECONDS


def _start_check(preferences, started_at):
    global _thread

    preferences.update_last_attempt_at = started_at
    preferences.update_check_state = update_checker.CHECK_CHECKING
    blender_version = _blender_version()
    with _lock:
        generation = _generation
    thread = threading.Thread(
        target=_run_check,
        args=(blender_version, started_at, generation),
        name="FinishedUpdateCheck",
        daemon=True,
    )
    with _lock:
        _thread = thread
    thread.start()


def _run_check(blender_version, started_at, generation):
    result = update_checker.check_for_update(ADDON_VERSION_STRING, blender_version)
    finished_at = time.time()
    with _lock:
        global _pending_result
        if _stopped or generation != _generation:
            return
        _pending_result = {
            "result": result,
            "started_at": started_at,
            "finished_at": finished_at,
        }


def _apply_pending_result(preferences):
    global _pending_result
    global _thread

    with _lock:
        pending = _pending_result
        _pending_result = None
        if _thread is not None and not _thread.is_alive():
            _thread = None

    if pending is None:
        return

    result = pending["result"]
    preferences.update_check_state = result.state
    preferences.update_last_error = result.error
    if result.state == update_checker.CHECK_FAILED:
        local_log.info(f"Finished? update check failed safely: error={result.error or 'unknown'}")
        return

    preferences.update_last_success_at = pending["finished_at"]
    _write_metadata(preferences, result.metadata)
    _notify_new_available_update(preferences, result)
    local_log.info(f"Finished? update check completed: state={result.state}")


def _write_metadata(preferences, metadata):
    if metadata is None:
        return
    preferences.update_latest_version = metadata.version
    preferences.update_latest_channel = metadata.channel
    preferences.update_latest_status = metadata.status
    preferences.update_latest_min_blender_version = metadata.min_blender_version or ""
    preferences.update_latest_download_url = metadata.download_url
    preferences.update_latest_sha256 = metadata.sha256
    preferences.update_latest_notes_ru = metadata.notes_ru or ""
    preferences.update_latest_notes_en = metadata.notes_en or ""


def clear_available_update(preferences):
    """Remove release details after a confirmed post-exit installation."""

    preferences.update_check_state = update_checker.CHECK_NOT_CHECKED
    preferences.update_last_error = ""
    for name in (
        "update_latest_version", "update_latest_channel", "update_latest_status",
        "update_latest_min_blender_version", "update_latest_download_url", "update_latest_sha256",
        "update_latest_notes_ru", "update_latest_notes_en",
    ):
        setattr(preferences, name, "")


def _notify_new_available_update(preferences, result):
    metadata = result.metadata
    if result.state != update_checker.CHECK_UPDATE_AVAILABLE or metadata is None:
        return
    if getattr(preferences, "update_notified_version", "") == metadata.version:
        return
    preferences.update_notified_version = metadata.version
    update_notifications.notify_update_available(metadata.version)


def _notify_startup_available_update(preferences):
    """Show a brief reminder once per Blender launch for a known newer version."""

    global _startup_notice_pending
    if not _startup_notice_pending:
        return
    has_available_update = (
        getattr(preferences, "update_check_state", "") == update_checker.CHECK_UPDATE_AVAILABLE
        and getattr(preferences, "update_latest_version", "")
        and getattr(preferences, "update_download_state", "") != "install_pending_exit"
    )
    if not has_available_update:
        _startup_notice_pending = False
        return False
    if update_notifications.notify_update_available(preferences.update_latest_version):
        _startup_notice_pending = False
        return True
    return False


def _has_running_check():
    with _lock:
        return _thread is not None and _thread.is_alive()


def _discard_pending_result():
    global _pending_result
    with _lock:
        _pending_result = None


def _ensure_timer_registered(first_interval):
    global _timer_registered
    if _timer_registered or _stopped:
        return
    try:
        import bpy

        bpy.app.timers.register(_timer_callback, first_interval=first_interval)
        _timer_registered = True
    except Exception as exc:
        local_log.warning(f"Finished? update timer failed: {exc}")


def _ensure_startup_notice_timer_registered():
    global _startup_notice_timer_registered
    if _startup_notice_timer_registered or _stopped:
        return
    try:
        import bpy

        bpy.app.timers.register(_startup_notice_timer_callback, first_interval=STARTUP_NOTICE_DELAY_SECONDS)
        _startup_notice_timer_registered = True
    except Exception as exc:
        local_log.warning(f"Finished? update notice timer failed: {exc}")


def _startup_notice_timer_callback():
    global _startup_notice_timer_registered
    _startup_notice_timer_registered = False
    if _stopped:
        return None
    preferences = addon_preferences.current_preferences()
    if preferences is None:
        _startup_notice_timer_registered = True
        return IDLE_RECHECK_SECONDS
    _notify_startup_available_update(preferences)
    return None


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


def _blender_version():
    try:
        import bpy

        version = getattr(bpy.app, "version", ())
        if version:
            return ".".join(str(part) for part in version[:3])
        return str(getattr(bpy.app, "version_string", "") or "")
    except (AttributeError, ImportError, TypeError):
        return ""
