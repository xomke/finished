import random
import threading
import time

from . import addon_preferences
from . import api_client
from . import device_connection_state
from .device_name import local_device_name
from . import local_log
from .onboarding import is_device_token_configured
from .version import ADDON_VERSION_STRING


INITIAL_CHECK_MIN_SECONDS = 5.0
INITIAL_CHECK_MAX_SECONDS = 10.0
NORMAL_CHECK_INTERVAL_SECONDS = 90.0
CHECK_JITTER_SECONDS = 20.0
DEVICE_CHECK_TIMEOUT_SECONDS = 3.0
FIRST_FAILURE_BACKOFF_SECONDS = 120.0
LATER_FAILURE_BACKOFF_SECONDS = 300.0
IDLE_RECHECK_SECONDS = 90.0
BUSY_RECHECK_SECONDS = 1.0


_lock = threading.Lock()
_thread = None
_pending_result = None
_pending_feedback = None
_timer_registered = False
_stopped = True


def register():
    start(initial=True)


def unregister():
    stop()


def start(*, initial=False):
    global _stopped
    _stopped = False
    _ensure_timer_registered(_initial_delay() if initial else 0.0)


def stop():
    global _stopped
    global _timer_registered

    _stopped = True
    try:
        import bpy

        timers = bpy.app.timers
        if _timer_registered and timers.is_registered(_timer_callback):
            timers.unregister(_timer_callback)
    except Exception:
        pass
    _timer_registered = False


def schedule_soon(delay_seconds=0.0):
    global _stopped
    _stopped = False
    _ensure_timer_registered(delay_seconds)


def report_transport_success():
    _queue_transport_feedback({"kind": "success", "at": time.time()})


def report_transport_auth_failure(error="Invalid device token"):
    _queue_transport_feedback(
        {
            "kind": "auth_failure",
            "at": time.time(),
            "error": str(error or "Invalid device token"),
        }
    )


def report_transport_network_failure(error="Server unreachable"):
    _queue_transport_feedback(
        {
            "kind": "network_failure",
            "at": time.time(),
            "error": str(error or "Server unreachable"),
        }
    )


def _timer_callback():
    global _timer_registered
    _timer_registered = False

    if _stopped:
        return None

    preferences = addon_preferences.current_preferences()
    if preferences is None:
        _ensure_timer_registered(IDLE_RECHECK_SECONDS)
        return None

    _apply_pending_result(preferences)
    _apply_pending_feedback(preferences)

    token = getattr(preferences, "device_token", "").strip()
    if not is_device_token_configured(token):
        return None

    state = device_connection_state.from_preferences(preferences)
    if state.status in {
        device_connection_state.STATUS_INVALID,
        device_connection_state.STATUS_UNPAIRED,
    }:
        return None

    now = time.time()
    if _has_running_check():
        _ensure_timer_registered(BUSY_RECHECK_SECONDS)
        return None

    if state.next_check_after and now < state.next_check_after:
        _ensure_timer_registered(max(BUSY_RECHECK_SECONDS, state.next_check_after - now))
        return None

    _start_check(preferences, token, now)
    _ensure_timer_registered(BUSY_RECHECK_SECONDS)
    return None


def _start_check(preferences, token, now):
    global _thread

    state = device_connection_state.mark_checking(
        device_connection_state.from_preferences(preferences),
        now,
    )
    device_connection_state.write_preferences(preferences, state)

    api_base_url = preferences.api_base_url
    device_name = local_device_name()
    blender_version = _blender_version()
    thread = threading.Thread(
        target=_run_check,
        args=(api_base_url, token, device_name, blender_version, ADDON_VERSION_STRING),
        name="FinishedDeviceConnectionCheck",
        daemon=True,
    )
    with _lock:
        _thread = thread
    thread.start()


def _run_check(api_base_url, token, device_name, blender_version, addon_version):
    started_at = time.time()
    result = api_client.check_in_device(
        api_base_url,
        token,
        device_name=device_name,
        blender_version=blender_version,
        addon_version=addon_version,
        timeout=DEVICE_CHECK_TIMEOUT_SECONDS,
    )
    finished_at = time.time()

    global _pending_result
    with _lock:
        _pending_result = {
            "result": result,
            "started_at": started_at,
            "finished_at": finished_at,
        }


def _blender_version():
    try:
        import bpy

        version = getattr(bpy.app, "version", ())
        if version:
            return ".".join(str(part) for part in version[:3])
        return str(getattr(bpy.app, "version_string", "") or "")
    except (AttributeError, ImportError, TypeError):
        return ""


def _apply_pending_result(preferences):
    global _pending_result
    global _thread

    with _lock:
        pending = _pending_result
        _pending_result = None
        thread = _thread
        if thread is not None and not thread.is_alive():
            _thread = None

    if pending is None:
        return

    result = pending["result"]
    checked_at = pending["finished_at"]
    state = device_connection_state.apply_device_check_result(
        device_connection_state.from_preferences(preferences),
        result,
        checked_at,
        device_token_configured=is_device_token_configured(
            getattr(preferences, "device_token", "")
        ),
    )
    state = _with_next_check_after(state, checked_at)
    device_connection_state.write_preferences(preferences, state)

    if result.ok:
        local_log.info("Finished? background Telegram connection check succeeded.")
    elif result.status_code == 401:
        preferences.enable_server_transport = False
        local_log.warning(
            "Finished? background Telegram connection check found an invalid token."
        )
    else:
        local_log.warning(
            "Finished? background Telegram connection check failed: "
            f"error={_api_error(result)}"
        )


def _apply_pending_feedback(preferences):
    global _pending_feedback

    with _lock:
        feedback = _pending_feedback
        _pending_feedback = None

    if feedback is None:
        return

    state = device_connection_state.from_preferences(preferences)
    checked_at = feedback["at"]
    kind = feedback["kind"]

    if kind == "success":
        state = device_connection_state.mark_valid(state, checked_at)
        state = _with_next_check_after(state, checked_at)
        local_log.info("Finished? transport delivery marked Telegram connection valid.")
    elif kind == "auth_failure":
        state = device_connection_state.mark_invalid(
            state,
            checked_at,
            error=feedback.get("error") or "Invalid device token",
        )
        preferences.enable_server_transport = False
        local_log.warning(
            "Finished? transport delivery marked Telegram connection invalid."
        )
    elif kind == "network_failure":
        state = device_connection_state.mark_server_unreachable(
            state,
            checked_at,
            feedback.get("error") or "Server unreachable",
        )
        state = _with_next_check_after(state, checked_at)
        local_log.warning(
            "Finished? transport delivery reported a recent server/network failure."
        )
    else:
        return

    device_connection_state.write_preferences(preferences, state)


def _queue_transport_feedback(feedback):
    global _pending_feedback
    with _lock:
        _pending_feedback = feedback
    schedule_soon()


def _with_next_check_after(state, now):
    if state.last_failure_at == now and state.last_error:
        return device_connection_state.DeviceConnectionState(
            status=state.status,
            last_success_at=state.last_success_at,
            last_check_at=state.last_check_at,
            last_failure_at=state.last_failure_at,
            last_error=state.last_error,
            failure_count=state.failure_count,
            next_check_after=now + _failure_backoff(state.failure_count),
        )

    if state.status == device_connection_state.STATUS_VALID:
        return device_connection_state.DeviceConnectionState(
            status=state.status,
            last_success_at=state.last_success_at,
            last_check_at=state.last_check_at,
            last_failure_at=state.last_failure_at,
            last_error=state.last_error,
            failure_count=state.failure_count,
            next_check_after=now + _normal_interval(),
        )

    return state


def _has_running_check():
    with _lock:
        return _thread is not None and _thread.is_alive()


def _ensure_timer_registered(first_interval):
    global _timer_registered

    if _timer_registered:
        return

    try:
        import bpy

        bpy.app.timers.register(_timer_callback, first_interval=first_interval)
        _timer_registered = True
    except Exception as exc:
        local_log.warning(f"Finished? connection monitor timer failed: {exc}")


def _initial_delay():
    return random.uniform(INITIAL_CHECK_MIN_SECONDS, INITIAL_CHECK_MAX_SECONDS)


def _normal_interval():
    return max(
        1.0,
        NORMAL_CHECK_INTERVAL_SECONDS
        + random.uniform(-CHECK_JITTER_SECONDS, CHECK_JITTER_SECONDS),
    )


def _failure_backoff(failure_count):
    if failure_count <= 1:
        return FIRST_FAILURE_BACKOFF_SECONDS
    return LATER_FAILURE_BACKOFF_SECONDS


def _api_error(result):
    if result.error:
        return result.error
    if result.status_code:
        return f"HTTP {result.status_code}"
    return "unknown error"
