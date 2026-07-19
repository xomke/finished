from collections import deque
from dataclasses import dataclass
import threading
import time

from . import api_client
from . import device_connection_monitor
from . import durable_event_queue
from . import local_log
from .addon_preferences import current_preferences


MAX_NOTIFICATION_EVENTS = 20
MAX_PENDING_SERVER_EVENTS = 32
MAX_PENDING_SERVER_STATUS_EVENTS = 4
PENDING_EVENT_MAX_AGE_SECONDS = 10 * 60.0
PENDING_EVENT_MAX_ATTEMPTS = 60
PENDING_STATUS_MAX_AGE_SECONDS = 2 * 60.0
PENDING_STATUS_MAX_ATTEMPTS = 1
IMPORTANT_EVENT_TYPES = {
    "render_started",
    "render_finished",
    "render_cancelled",
    "render_failed",
}
IMPORTANT_EVENT_RETRY_DELAYS_SECONDS = (0.5, 1.0, 2.0, 5.0, 10.0)
STATUS_EVENT_RETRY_DELAYS_SECONDS = (0.5, 1.0)
_notification_events = deque(maxlen=MAX_NOTIFICATION_EVENTS)


@dataclass(frozen=True)
class NotificationEvent:
    kind: str
    title: str
    body: str
    payload: dict

    def preview_line(self):
        return f"{self.kind}: {self.title}"


@dataclass
class PendingServerEvent:
    event: NotificationEvent
    api_base_url: str
    device_token: str
    created_at: float
    attempts: int = 0
    generation: int = 0
    durable_id: str = ""


class OrderedDeliveryWorker:
    def __init__(
        self,
        post_event=None,
        post_frame_samples=None,
        sleep=time.sleep,
        start_thread=None,
        now=time.monotonic,
        max_events=MAX_PENDING_SERVER_EVENTS,
        max_status_events=MAX_PENDING_SERVER_STATUS_EVENTS,
        durable_queue=None,
        http_client=None,
    ):
        self.post_event = post_event
        self.post_frame_samples = post_frame_samples
        self.sleep = sleep
        self.start_thread = start_thread or self._start_thread
        self.now = now
        self.max_events = max_events
        self.max_status_events = max_status_events
        self.durable_queue = durable_queue or durable_event_queue.DurableImportantEventQueue()
        self.http_client = http_client or api_client.ReusableHttpClient(now=now)
        self._events = deque()
        self._status_events = {}
        self._terminal_sessions = set()
        self._status_generations = {}
        self._loaded_durable_ids = set()
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._running = False

    def enqueue(self, event, api_base_url, device_token):
        durable_id = self._persist_important_event(event, api_base_url)
        pending = PendingServerEvent(
            event=event,
            api_base_url=api_base_url,
            device_token=device_token,
            created_at=self.now(),
            durable_id=durable_id,
        )

        with self._wake:
            if _is_terminal_event(event):
                session_id = event.payload.get("session_id")
                if session_id:
                    self._terminal_sessions.add(session_id)
                    self._status_events.pop(session_id, None)
                    self._status_generations[session_id] = self._status_generations.get(session_id, 0) + 1

            if _is_status_event(event):
                queued = self._enqueue_status_locked(pending)
            else:
                queued = self._enqueue_event_locked(pending)

            if queued:
                if durable_id:
                    self._loaded_durable_ids.add(durable_id)
                self._ensure_running_locked()
                self._wake.notify()

        return queued

    def enqueue_frame_samples(self, session_id, batch, api_base_url, device_token):
        pending = PendingServerEvent(
            event=NotificationEvent(
                kind="frame_samples",
                title="",
                body="",
                payload={
                    "delivery_type": "frame_samples",
                    "event_type": "frame_samples",
                    "session_id": session_id,
                    "frame_samples": batch,
                },
            ),
            api_base_url=api_base_url,
            device_token=device_token,
            created_at=self.now(),
        )
        with self._wake:
            queued = self._enqueue_event_locked(pending)
            if queued:
                self._ensure_running_locked()
                self._wake.notify()
        return queued

    def flush_once(self):
        pending = self._take_next_pending()
        if pending is None:
            return 0
        return 1 if self._deliver_pending(pending) else 0

    def restore_durable_events(self, api_base_url, device_token):
        restored = 0
        for record in self.durable_queue.load_pending():
            durable_id = record.get("id") or ""
            if not durable_id or durable_id in self._loaded_durable_ids:
                continue
            event_data = record.get("event") or {}
            payload = event_data.get("payload") or {}
            if not payload.get("event_type") or not payload.get("session_id"):
                self.durable_queue.delete(durable_id)
                continue
            pending = PendingServerEvent(
                event=NotificationEvent(
                    kind=event_data.get("kind") or payload.get("event_type", ""),
                    title=event_data.get("title") or payload.get("event_type", ""),
                    body=event_data.get("body") or "",
                    payload=payload,
                ),
                api_base_url=api_base_url or record.get("api_base_url") or "",
                device_token=device_token,
                created_at=self.now(),
                attempts=int(record.get("attempts") or 0),
                durable_id=durable_id,
            )
            with self._wake:
                if self._enqueue_event_locked(pending):
                    self._loaded_durable_ids.add(durable_id)
                    self._ensure_running_locked()
                    self._wake.notify()
                    restored += 1
        if restored:
            local_log.info(f"Restored durable render event deliveries: count={restored}")
        return restored

    def pending_event_count(self):
        with self._lock:
            return len(self._events)

    def pending_status_count(self):
        with self._lock:
            return len(self._status_events)

    def clear(self):
        with self._wake:
            self._events.clear()
            self._status_events.clear()
            self._terminal_sessions.clear()
            self._status_generations.clear()
            self._loaded_durable_ids.clear()
            self._running = False
            self._wake.notify_all()
        self.http_client.close()

    def _persist_important_event(self, event, api_base_url):
        if not _is_important_event(event):
            return ""
        try:
            durable_id = self.durable_queue.save(event, api_base_url)
            local_log.info(
                f"Persisted render event delivery: event={event.payload.get('event_type')} "
                f"session={event.payload.get('session_id')} durable_id={durable_id}"
            )
            return durable_id
        except Exception as exc:
            local_log.warning(
                f"Failed to persist render event delivery: event={event.payload.get('event_type')} "
                f"session={event.payload.get('session_id')} operation=render_event error={exc}"
            )
            return ""

    def _enqueue_event_locked(self, pending):
        if len(self._events) >= self.max_events:
            local_log.warning(
                f"Dropping render event because delivery queue is full: "
                f"event={pending.event.payload.get('event_type')} "
                f"session={pending.event.payload.get('session_id')} operation=render_event"
            )
            return False

        self._events.append(pending)
        local_log.info(
            f"Queued render event delivery: event={pending.event.payload.get('event_type')} "
            f"session={pending.event.payload.get('session_id')} "
            f"sequence={pending.event.payload.get('event_sequence')}"
        )
        return True

    def _enqueue_status_locked(self, pending):
        session_id = pending.event.payload.get("session_id")
        if not session_id:
            return False
        if session_id in self._terminal_sessions:
            local_log.info(
                f"Skipping stale render status after terminal event: session={session_id} "
                f"sequence={pending.event.payload.get('event_sequence')}"
            )
            return False
        if (
            session_id not in self._status_events
            and len(self._status_events) >= self.max_status_events
        ):
            dropped_session_id = next(iter(self._status_events))
            self._status_events.pop(dropped_session_id)
            local_log.warning(
                f"Dropping pending render status because status queue is full: "
                f"session={dropped_session_id} operation=render_event"
            )

        replaced = session_id in self._status_events
        self._status_generations[session_id] = self._status_generations.get(session_id, 0) + 1
        pending.generation = self._status_generations[session_id]
        self._status_events[session_id] = pending
        if replaced:
            local_log.info(
                f"Coalesced pending render status: session={session_id} "
                f"sequence={pending.event.payload.get('event_sequence')}"
            )
        else:
            local_log.info(
                f"Queued render status delivery: session={session_id} "
                f"sequence={pending.event.payload.get('event_sequence')}"
            )
        return True

    def _ensure_running_locked(self):
        if self._running:
            return
        self._running = True
        try:
            self.start_thread(self._worker_loop)
        except Exception as exc:
            self._running = False
            local_log.warning(f"Render event delivery worker start failed: operation=render_event error={exc}")

    def _worker_loop(self):
        while True:
            pending = self._take_next_pending()
            if pending is None:
                return
            self._deliver_pending(pending)

    def _take_next_pending(self):
        with self._wake:
            if self._events:
                return self._events.popleft()
            if self._status_events:
                session_id = next(iter(self._status_events))
                return self._status_events.pop(session_id)
            self._running = False
            return None

    def _deliver_pending(self, pending):
        if self._pending_expired(pending):
            self._delete_durable_pending(pending)
            self._log_drop(pending, "pending delivery expired")
            return False
        if _is_status_event(pending.event) and self._is_session_terminal(pending.event):
            local_log.info(
                f"Skipping stale render status before delivery: "
                f"session={pending.event.payload.get('session_id')} "
                f"sequence={pending.event.payload.get('event_sequence')}"
            )
            return False

        result = self._post_once(pending)
        pending.attempts += 1
        if _is_frame_sample_flush(pending.event):
            self.http_client.close()
        if result.ok:
            self._delete_durable_pending(pending)
            device_connection_monitor.report_transport_success()
            local_log.info(
                f"Server event sent: {pending.event.payload.get('event_type')} "
                f"session={pending.event.payload.get('session_id')} "
                f"sequence={pending.event.payload.get('event_sequence')}"
            )
            return True

        _report_delivery_failure_to_connection_monitor(result)
        local_log.warning(
            f"Render event delivery failed: event={pending.event.payload.get('event_type')} "
            f"session={pending.event.payload.get('session_id')} "
            f"sequence={pending.event.payload.get('event_sequence')} "
            f"operation=render_event error={_result_error(result)}"
        )
        if result.status_code == 401:
            self._delete_durable_pending(pending)
            self._log_drop(pending, "device token invalid")
            return False
        return self._retry_or_drop(pending)

    def _retry_or_drop(self, pending):
        delays = (
            IMPORTANT_EVENT_RETRY_DELAYS_SECONDS
            if _is_important_event(pending.event)
            else STATUS_EVENT_RETRY_DELAYS_SECONDS
        )
        max_attempts = (
            PENDING_EVENT_MAX_ATTEMPTS
            if _is_important_event(pending.event)
            else PENDING_STATUS_MAX_ATTEMPTS
        )
        if pending.attempts >= max_attempts or self._pending_expired(pending):
            self._delete_durable_pending(pending)
            self._log_drop(pending, "pending delivery retry limit reached")
            return False
        self._update_durable_attempts(pending)

        delay = delays[min(pending.attempts - 1, len(delays) - 1)]
        local_log.info(
            f"Retrying render event delivery: event={pending.event.payload.get('event_type')} "
            f"session={pending.event.payload.get('session_id')} "
            f"sequence={pending.event.payload.get('event_sequence')} delay_seconds={delay}"
        )
        self.sleep(delay)

        if _is_status_event(pending.event):
            with self._wake:
                if self._is_session_terminal(pending.event) or self._status_was_replaced(pending):
                    local_log.info(
                        f"Skipping stale render status retry: "
                        f"session={pending.event.payload.get('session_id')} "
                        f"sequence={pending.event.payload.get('event_sequence')}"
                    )
                    return False
                self._status_events[pending.event.payload.get("session_id")] = pending
                self._wake.notify()
            return False

        with self._wake:
            self._events.appendleft(pending)
            self._wake.notify()
        return False

    def _delete_durable_pending(self, pending):
        if not pending.durable_id:
            return
        self.durable_queue.delete(pending.durable_id)
        self._loaded_durable_ids.discard(pending.durable_id)

    def _update_durable_attempts(self, pending):
        if not pending.durable_id:
            return
        self.durable_queue.update_attempts(pending.durable_id, pending.attempts)

    def _post_once(self, pending):
        timeout = (
            api_client.IMPORTANT_EVENT_TIMEOUT_SECONDS
            if _is_important_event(pending.event)
            else api_client.DEFAULT_TIMEOUT_SECONDS
        )
        try:
            if _is_frame_sample_flush(pending.event):
                if self.post_frame_samples is not None:
                    return self.post_frame_samples(
                        pending.api_base_url,
                        pending.event.payload.get("session_id"),
                        pending.event.payload.get("frame_samples"),
                        device_token=pending.device_token,
                        timeout=timeout,
                    )
                return self.http_client.post_frame_samples(
                    pending.api_base_url,
                    pending.event.payload.get("session_id"),
                    pending.event.payload.get("frame_samples"),
                    device_token=pending.device_token,
                    timeout=timeout,
                )
            if self.post_event is not None:
                return self.post_event(
                    pending.api_base_url,
                    pending.event.payload,
                    device_token=pending.device_token,
                    timeout=timeout,
                )
            return self.http_client.post_render_event(
                pending.api_base_url,
                pending.event.payload,
                device_token=pending.device_token,
                timeout=timeout,
            )
        except Exception as exc:
            self.http_client.close()
            return api_client.ApiResult(ok=False, error=str(exc))

    def _pending_expired(self, pending):
        max_age = (
            PENDING_EVENT_MAX_AGE_SECONDS
            if _is_important_event(pending.event)
            else PENDING_STATUS_MAX_AGE_SECONDS
        )
        return self.now() - pending.created_at > max_age

    def _is_session_terminal(self, event):
        session_id = event.payload.get("session_id")
        return bool(session_id and session_id in self._terminal_sessions)

    def _status_was_replaced(self, pending):
        session_id = pending.event.payload.get("session_id")
        if not session_id:
            return False
        return self._status_generations.get(session_id, 0) != pending.generation

    def _log_drop(self, pending, reason):
        local_log.warning(
            f"Dropping pending render event: event={pending.event.payload.get('event_type')} "
            f"session={pending.event.payload.get('session_id')} "
            f"sequence={pending.event.payload.get('event_sequence')} "
            f"operation=render_event error={reason}"
        )

    @staticmethod
    def _start_thread(target):
        thread = threading.Thread(
            target=target,
            name="FinishedRenderEventDelivery",
            daemon=True,
        )
        thread.start()


_delivery_worker = OrderedDeliveryWorker()


class LocalMockTransport:
    def send(self, event, log_func):
        _notification_events.append(event)
        log_func(f"Mock notify {event.kind}:\n{event.body}")


class ServerTransport:
    def __init__(
        self,
        api_base_url,
        device_token="",
        post_event=None,
        sleep=time.sleep,
        delivery_worker=None,
    ):
        self.api_base_url = api_base_url
        self.device_token = device_token
        if delivery_worker is None:
            self.delivery_worker = _delivery_worker
            self.delivery_worker.post_event = post_event
            self.delivery_worker.sleep = sleep
        else:
            self.delivery_worker = delivery_worker
        self.delivery_worker.restore_durable_events(self.api_base_url, self.device_token)

    def send(self, event, _log_func):
        self.delivery_worker.enqueue(
            event,
            api_base_url=self.api_base_url,
            device_token=self.device_token,
        )

    def _send_blocking(self, event):
        return self.delivery_worker.enqueue(
            event,
            api_base_url=self.api_base_url,
            device_token=self.device_token,
        )

    def send_frame_samples(self, session_id, batch):
        return self.delivery_worker.enqueue_frame_samples(
            session_id,
            batch,
            api_base_url=self.api_base_url,
            device_token=self.device_token,
        )


class CompositeTransport:
    def __init__(self, transports):
        self.transports = tuple(transports)

    def send(self, event, log_func):
        for transport in self.transports:
            transport.send(event, log_func)


def current_transport():
    preferences = _addon_preferences()
    if preferences is None or not preferences.enable_server_transport:
        return LocalMockTransport()

    return CompositeTransport(
        (
            LocalMockTransport(),
            ServerTransport(
                api_base_url=preferences.api_base_url,
                device_token=preferences.device_token,
            ),
        )
    )


def recent_notifications(limit=5):
    if limit <= 0:
        return []
    return list(_notification_events)[-limit:]


def recent_notifications_for_session(session_id, limit=5):
    if not session_id or limit <= 0:
        return []

    events = [
        event
        for event in _notification_events
        if event.payload.get("session_id") == session_id
    ]
    return events[-limit:]


def clear_notifications():
    _notification_events.clear()


def pending_server_event_count():
    return _delivery_worker.pending_event_count()


def pending_server_status_count():
    return _delivery_worker.pending_status_count()


def clear_pending_server_events():
    _delivery_worker.clear()


def enqueue_final_frame_samples(session_id, batch):
    if not batch or not batch.get("samples"):
        return False
    preferences = _addon_preferences()
    if preferences is None or not preferences.enable_server_transport:
        return False
    if not preferences.device_token:
        return False
    return _delivery_worker.enqueue_frame_samples(
        session_id,
        batch,
        api_base_url=preferences.api_base_url,
        device_token=preferences.device_token,
    )


def flush_pending_server_events(post_event=api_client.post_render_event, now_seconds=None):
    sent_count = 0
    original_post_event = _delivery_worker.post_event
    original_now = _delivery_worker.now
    _delivery_worker.post_event = post_event
    if now_seconds is not None:
        _delivery_worker.now = lambda: now_seconds
    try:
        while True:
            before = (
                _delivery_worker.pending_event_count(),
                _delivery_worker.pending_status_count(),
            )
            if before == (0, 0):
                break
            sent_count += _delivery_worker.flush_once()
            after = (
                _delivery_worker.pending_event_count(),
                _delivery_worker.pending_status_count(),
            )
            if after == before:
                break
    finally:
        _delivery_worker.post_event = original_post_event
        _delivery_worker.now = original_now
    return sent_count


def _addon_preferences():
    return current_preferences()


def _is_important_event(event):
    return event.payload.get("event_type") in IMPORTANT_EVENT_TYPES


def _is_status_event(event):
    return event.payload.get("event_type") == "render_status"


def _is_terminal_event(event):
    return event.payload.get("event_type") in {
        "render_finished",
        "render_cancelled",
        "render_failed",
    }


def _is_frame_sample_flush(event):
    return event.payload.get("delivery_type") == "frame_samples"


def _result_error(result):
    if result.error:
        return result.error
    if result.status_code:
        return f"HTTP {result.status_code}"
    return "unknown error"


def _report_delivery_failure_to_connection_monitor(result):
    if result.status_code == 401:
        device_connection_monitor.report_transport_auth_failure(_result_error(result))
        return

    if _is_server_or_network_failure(result):
        device_connection_monitor.report_transport_network_failure(_result_error(result))


def _is_server_or_network_failure(result):
    if result.status_code:
        return result.status_code >= 500
    return bool(result.error)
