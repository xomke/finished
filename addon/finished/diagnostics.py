from . import local_log
from . import notifier
from . import render_handlers
from . import device_connection_state
from . import heartbeat
from . import command_polling
from . import transports
from .eta import format_duration


RECENT_SERVER_SLOW_SECONDS = 10 * 60.0


def connection_status_label(preferences):
    state = device_connection_state.from_preferences(preferences)
    if not getattr(preferences, "device_token", "").strip():
        return "Not connected"
    if state.status == device_connection_state.STATUS_VALID:
        return "Connected"
    if state.status in {
        device_connection_state.STATUS_INVALID,
        device_connection_state.STATUS_UNPAIRED,
    }:
        return "Reconnect needed"
    if state.status == device_connection_state.STATUS_CHECKING:
        return "Checking"
    if state.status == device_connection_state.STATUS_SERVER_UNREACHABLE:
        return "Server unreachable"
    return "Unknown"


def public_connection_status_label(preferences):
    label = connection_status_label(preferences)
    if label in {"Connected", "Reconnect needed"}:
        return label
    if label in {"Checking", "Unknown", "Server unreachable"} and getattr(
        preferences, "device_token", ""
    ).strip():
        return "Checking connection"
    return "Not connected"


def connection_snapshot_lines(preferences, now_seconds=None):
    state = device_connection_state.from_preferences(preferences)
    now = _now_seconds(now_seconds)
    pending_events = (
        transports.pending_server_event_count()
        + transports.pending_server_status_count()
        + heartbeat.pending_heartbeat_count()
        + command_polling.pending_command_count()
    )

    lines = [
        f"Telegram connection: {connection_status_label(preferences)}",
        f"Last server check: {_relative_time(state.last_check_at, now)}",
        f"Server recently slow: {_yes_no(_server_recently_slow(state, now))}",
        f"Last error: {state.last_error or 'none'}",
        f"Pending events: {pending_events}",
    ]
    return lines


def session_snapshot_lines():
    session = render_handlers.current_session() or render_handlers.last_session()

    if session is None:
        return ["Session: none"]

    current_frame = session.current_frame if session.current_frame is not None else "-"

    return [
        f"Session ID: {session.session_id}",
        f"Status: {session.status}",
        f"Project: {session.project_name}",
        f"Frames: {session.frame_start}-{session.frame_end} step {session.frame_step}",
        f"Current frame: {current_frame}",
        f"Progress: {session.completed_frames} / {session.total_frames} frames ({session.progress_percent:.1f}%)",
        f"Average frame time: {notifier.format_average_frame_time_value(session)}",
        f"Elapsed: {format_duration(session.elapsed_seconds)}",
        f"ETA: {format_duration(session.eta_seconds)}",
    ]


def recent_event_lines(limit=8):
    return local_log.recent_compact(limit)


def recent_notification_lines(limit=5):
    session = render_handlers.current_session() or render_handlers.last_session()
    if session is None:
        events = notifier.recent_notifications(limit)
    else:
        events = notifier.recent_notifications_for_session(session.session_id, limit)

    if not events:
        return ["No mock notifications yet."]
    return [event.preview_line() for event in events]


def _now_seconds(now_seconds):
    if now_seconds is not None:
        return float(now_seconds)

    import time

    return time.time()


def _relative_time(timestamp, now):
    timestamp = float(timestamp or 0.0)
    if timestamp <= 0.0:
        return "never"
    age_seconds = max(0, int(now - timestamp))
    if age_seconds < 1:
        return "just now"
    if age_seconds == 1:
        return "1 second ago"
    if age_seconds < 60:
        return f"{age_seconds} seconds ago"

    age_minutes = age_seconds // 60
    if age_minutes == 1:
        return "1 minute ago"
    if age_minutes < 60:
        return f"{age_minutes} minutes ago"

    age_hours = age_minutes // 60
    if age_hours == 1:
        return "1 hour ago"
    return f"{age_hours} hours ago"


def _server_recently_slow(state, now):
    if state.status not in {
        device_connection_state.STATUS_VALID,
        device_connection_state.STATUS_SERVER_UNREACHABLE,
    }:
        return False
    if state.last_failure_at <= 0.0:
        return False
    if state.last_failure_at < state.last_success_at:
        return False
    return now - state.last_failure_at <= RECENT_SERVER_SLOW_SECONDS


def _yes_no(value):
    return "yes" if value else "no"
