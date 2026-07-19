from . import local_log
from .eta import format_duration
from .event_payload import build_event_payload
from .frame_sample_batch import build_frame_sample_batch
from .i18n import t
from .notification_policy import StatusUpdatePolicy
from .transports import NotificationEvent
from .transports import clear_notifications
from .transports import current_transport
from .transports import recent_notifications
from .transports import recent_notifications_for_session


_status_policy = StatusUpdatePolicy()


class LocalMockNotifier:
    def __init__(self, language_code="en"):
        self.language_code = language_code

    def render_started(self, session):
        title = t("render_started", self.language_code)
        _record_notification(
            "started",
            title,
            format_started_message(session, self.language_code),
            build_event_payload("render_started", session),
            local_log.info,
        )

    def status_updated(self, session, now_seconds, frame_timings=None):
        if not _status_policy.should_send_status(now_seconds):
            return

        title = t("render_status", self.language_code)
        frame_samples = _build_frame_samples(frame_timings)
        _record_notification(
            "status",
            title,
            format_status_message(session, self.language_code),
            build_event_payload("render_status", session, frame_samples=frame_samples),
            local_log.info,
        )

    def render_finished(self, session):
        title = t("render_finished", self.language_code)
        _record_notification(
            "finished",
            title,
            format_finished_message(session, self.language_code),
            build_event_payload("render_finished", session),
            local_log.info,
        )

    def render_cancelled(self, session):
        title = t("render_cancelled", self.language_code)
        _record_notification(
            "cancelled",
            title,
            format_terminal_message("render_cancelled", session, self.language_code),
            build_event_payload("render_cancelled", session),
            local_log.warning,
        )

    def render_failed(self, session, reason):
        title = t("render_failed", self.language_code)
        _record_notification(
            "failed",
            title,
            format_terminal_message("render_failed", session, self.language_code, reason=reason),
            build_event_payload("render_failed", session, reason=reason),
            local_log.error,
        )


def current_notifier():
    return LocalMockNotifier()


def reset_notification_policy():
    _status_policy.reset()


def _build_frame_samples(frame_timings):
    if frame_timings is None:
        return None
    try:
        return build_frame_sample_batch(
            frame_timings.samples,
            dropped_samples=frame_timings.dropped_samples,
        )
    except Exception as exc:
        local_log.warning(f"Frame sample batch ignored: error={type(exc).__name__}")
        return None


def _record_notification(kind, title, body, payload, log_func):
    event = NotificationEvent(kind=kind, title=title, body=body, payload=payload)
    current_transport().send(event, log_func)


def _duration(seconds, language_code):
    return format_duration(seconds, calculating_text=t("calculating", language_code))


def format_started_message(session, language_code="en"):
    return format_status_message(session, language_code)


def format_status_message(session, language_code="en"):
    return "\n".join(
        (
            t("render_running", language_code),
            "",
            f"{t('project', language_code)}: {session.project_name}",
            format_progress_line(session, language_code),
            format_average_frame_time_line(session, language_code),
            f"{t('elapsed', language_code)}: {_duration(session.elapsed_seconds, language_code)}",
            f"{t('eta', language_code)}: {_duration(session.eta_seconds, language_code)}",
        )
    )


def format_finished_message(session, language_code="en"):
    return "\n".join(
        (
            t("render_finished", language_code),
            "",
            f"{t('project', language_code)}: {session.project_name}",
            format_progress_line(session, language_code),
            format_average_frame_time_line(session, language_code),
            f"{t('total_time', language_code)}: {_duration(session.elapsed_seconds, language_code)}",
        )
    )


def format_terminal_message(title_key, session, language_code="en", reason=""):
    title = t(title_key, language_code)
    if reason:
        title = f"{title}: {reason}"

    return "\n".join(
        (
            title,
            "",
            f"{t('project', language_code)}: {session.project_name}",
            format_progress_line(session, language_code),
            format_average_frame_time_line(session, language_code),
            f"{t('elapsed', language_code)}: {_duration(session.elapsed_seconds, language_code)}",
        )
    )


def format_progress_line(session, language_code="en"):
    return (
        f"{t('progress', language_code)}: "
        f"{session.completed_frames} / {session.total_frames} "
        f"{t('frames_word', language_code)} ({session.progress_percent:.1f}%)"
    )


def format_average_frame_time_line(session, language_code="en"):
    value = format_average_frame_time_value(session, language_code)
    return f"{t('average_frame_time', language_code)}: {value}"


def format_average_frame_time_value(session, language_code="en"):
    average_frame_time = session.average_frame_time
    if average_frame_time is None:
        return t("calculating", language_code)
    return f"{average_frame_time:.1f} {t('sec_per_frame', language_code)}"
