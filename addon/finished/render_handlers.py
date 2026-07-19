import time

from . import heartbeat
from . import command_polling
from . import local_log
from . import session_history
from .event_sequence import reset_event_sequence
from .frame_sample_batch import build_frame_sample_batch
from .frame_timing import FrameTimingAccumulator
from .notifier import current_notifier
from .notifier import reset_notification_policy
from . import transports


_active_session = None
_last_session = None
_started_at = None
_frame_timings = None
_last_frame_timings = None


def prepare_session(session):
    global _active_session, _last_session, _started_at, _frame_timings, _last_frame_timings
    _ensure_handlers_registered()
    _active_session = session
    _last_session = session
    _started_at = None
    _frame_timings = FrameTimingAccumulator(
        frame_start=session.frame_start,
        frame_end=session.frame_end,
        frame_step=session.frame_step,
    )
    _last_frame_timings = _frame_timings
    reset_notification_policy()
    reset_event_sequence(session.session_id)
    local_log.info(f"Prepared render session: session={session.session_id} {session.status_line()}")


def current_session():
    return _active_session


def last_session():
    return _last_session


def current_frame_timings():
    return _frame_timings


def last_frame_timings():
    return _last_frame_timings


def clear_session():
    global _active_session, _started_at, _frame_timings
    heartbeat.stop_heartbeat()
    command_polling.stop_command_polling()
    _active_session = None
    _started_at = None
    _frame_timings = None


def fail_active_session(message):
    global _active_session, _last_session
    if _active_session is None:
        return

    _mark_failed(message, _elapsed_seconds())
    clear_session()


def _elapsed_seconds():
    if _started_at is None:
        return 0.0
    return time.monotonic() - _started_at


def _on_render_init(*_args):
    global _active_session, _last_session, _started_at
    if _active_session is None:
        return

    _started_at = time.monotonic()
    _active_session = _active_session.start()
    _last_session = _active_session
    local_log.info(f"Render started: session={_active_session.session_id} {_active_session.status_line()}")
    current_notifier().render_started(_active_session)
    heartbeat.start_heartbeat(current_session)
    command_polling.start_command_polling(current_session)


def _on_render_pre(scene, *_args):
    if _active_session is None or _frame_timings is None:
        return

    try:
        _frame_timings.begin_frame(scene.frame_current, time.monotonic())
    except Exception as exc:
        local_log.warning(f"Frame timing start ignored: error={type(exc).__name__}")


def _on_render_write(scene, *_args):
    global _active_session, _last_session
    if _active_session is None:
        return

    now = time.monotonic()
    if _frame_timings is not None:
        try:
            _frame_timings.complete_frame(scene.frame_current, now)
        except Exception as exc:
            local_log.warning(f"Frame timing completion ignored: error={type(exc).__name__}")

    _active_session = _active_session.complete_frame(
        frame=scene.frame_current,
        elapsed_seconds=_elapsed_seconds(),
    )
    _last_session = _active_session
    local_log.info(f"Frame written: session={_active_session.session_id} {_active_session.status_line()}")
    current_notifier().status_updated(_active_session, now, _frame_timings)


def _on_render_complete(*_args):
    global _active_session, _last_session
    if _active_session is None:
        return

    if _frame_timings is not None:
        _frame_timings.discard_open_frame()
    _mark_finished(_elapsed_seconds())
    clear_session()


def _on_render_cancel(*_args):
    if _active_session is None:
        return

    if _frame_timings is not None:
        _frame_timings.discard_open_frame()
    _mark_cancelled(_elapsed_seconds())
    clear_session()


def _mark_finished(elapsed_seconds):
    global _active_session, _last_session
    _active_session = _active_session.finish(elapsed_seconds)
    _last_session = _active_session
    local_log.info(f"Render finished: session={_active_session.session_id} {_active_session.status_line()}")
    current_notifier().render_finished(_active_session)
    _enqueue_final_frame_samples(_active_session.session_id)
    _record_terminal_session(_active_session)


def _mark_cancelled(elapsed_seconds):
    global _active_session, _last_session
    _active_session = _active_session.cancel(elapsed_seconds)
    _last_session = _active_session
    local_log.warning(f"Render cancelled: session={_active_session.session_id} {_active_session.status_line()}")
    current_notifier().render_cancelled(_active_session)
    _enqueue_final_frame_samples(_active_session.session_id)
    _record_terminal_session(_active_session)


def _mark_failed(message, elapsed_seconds):
    global _active_session, _last_session
    if _frame_timings is not None:
        _frame_timings.discard_open_frame()
    _active_session = _active_session.fail(elapsed_seconds)
    _last_session = _active_session
    local_log.error(f"{message}: session={_active_session.session_id} {_active_session.status_line()}")
    current_notifier().render_failed(_active_session, message)
    _enqueue_final_frame_samples(_active_session.session_id)
    _record_terminal_session(_active_session)


def _record_terminal_session(session):
    try:
        session_history.record_terminal_session(session)
    except Exception as exc:
        local_log.warning(
            "Local render history update ignored: "
            f"session={session.session_id} error={type(exc).__name__}"
        )


def _enqueue_final_frame_samples(session_id):
    if _frame_timings is None:
        return False
    try:
        batch = build_frame_sample_batch(
            _frame_timings.samples,
            dropped_samples=_frame_timings.dropped_samples,
        )
        return transports.enqueue_final_frame_samples(session_id, batch)
    except Exception as exc:
        local_log.warning(f"Final frame sample batch ignored: error={type(exc).__name__}")
        return False


HANDLERS = (
    ("render_init", _on_render_init),
    ("render_pre", _on_render_pre),
    ("render_write", _on_render_write),
    ("render_complete", _on_render_complete),
    ("render_cancel", _on_render_cancel),
)


def _add_handler(handler_list, callback):
    if callback not in handler_list:
        handler_list.append(callback)


def _remove_handler(handler_list, callback):
    while callback in handler_list:
        handler_list.remove(callback)


def register():
    _ensure_handlers_registered()


def _ensure_handlers_registered():
    try:
        import bpy
    except ImportError:
        return
    handlers = getattr(getattr(bpy, "app", None), "handlers", None)
    if handlers is None:
        return

    for handler_name, callback in HANDLERS:
        _add_handler(getattr(handlers, handler_name), callback)


def unregister():
    import bpy

    for handler_name, callback in HANDLERS:
        _remove_handler(getattr(bpy.app.handlers, handler_name), callback)
    clear_session()
