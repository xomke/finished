from collections import deque
from dataclasses import dataclass
import threading
import time

from . import api_client
from . import device_connection_monitor
from .addon_preferences import current_preferences
from . import local_log


HEARTBEAT_INTERVAL_SECONDS = 30.0
MAX_PENDING_HEARTBEATS = 4
_heartbeat_running = False


@dataclass(frozen=True)
class PendingHeartbeat:
    api_base_url: str
    session_id: str
    device_token: str


class HeartbeatDeliveryWorker:
    def __init__(
        self,
        post_heartbeat=None,
        start_thread=None,
        max_pending=MAX_PENDING_HEARTBEATS,
        http_client=None,
    ):
        self.post_heartbeat = post_heartbeat
        self.start_thread = start_thread or self._start_thread
        self.max_pending = max_pending
        self.http_client = http_client or api_client.ReusableHttpClient()
        self._pending = deque()
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._running = False

    def enqueue(self, pending):
        with self._wake:
            if len(self._pending) >= self.max_pending:
                local_log.warning(
                    f"Dropping heartbeat because delivery queue is full: "
                    f"session={pending.session_id}"
                )
                return False
            self._pending.append(pending)
            self._ensure_running_locked()
            self._wake.notify()
        return True

    def flush_once(self):
        pending = self._take_next_pending()
        if pending is None:
            return 0
        self._deliver_pending(pending)
        return 1

    def pending_count(self):
        with self._lock:
            return len(self._pending)

    def clear(self):
        with self._wake:
            self._pending.clear()
            self._running = False
            self._wake.notify_all()
        self.http_client.close()

    def _ensure_running_locked(self):
        if self._running:
            return
        self._running = True
        try:
            self.start_thread(self._worker_loop)
        except Exception as exc:
            self._running = False
            local_log.warning(f"Heartbeat worker start failed: error={exc}")

    def _worker_loop(self):
        while True:
            pending = self._take_next_pending()
            if pending is None:
                return
            self._deliver_pending(pending)

    def _take_next_pending(self):
        with self._wake:
            if self._pending:
                return self._pending.popleft()
            self._running = False
            return None

    def _deliver_pending(self, pending):
        try:
            if self.post_heartbeat is not None:
                result = self.post_heartbeat(
                    pending.api_base_url,
                    pending.session_id,
                    device_token=pending.device_token,
                    timeout=api_client.DEFAULT_TIMEOUT_SECONDS,
                )
            else:
                result = self.http_client.post_heartbeat(
                    pending.api_base_url,
                    pending.session_id,
                    device_token=pending.device_token,
                    timeout=api_client.DEFAULT_TIMEOUT_SECONDS,
                )
        except Exception as exc:
            self.http_client.close()
            result = api_client.ApiResult(ok=False, error=str(exc))

        if result.ok:
            device_connection_monitor.report_transport_success()
            local_log.info(f"Heartbeat sent: session={pending.session_id}")
            return True

        _report_heartbeat_failure_to_connection_monitor(result)
        local_log.warning(
            f"Heartbeat failed: session={pending.session_id} "
            f"error={_result_error(result)}"
        )
        return False

    @staticmethod
    def _start_thread(target):
        thread = threading.Thread(
            target=target,
            name="FinishedHeartbeatDelivery",
            daemon=True,
        )
        thread.start()


_heartbeat_worker = HeartbeatDeliveryWorker()


def start_heartbeat(session_provider):
    global _heartbeat_running
    _heartbeat_running = True
    _register_timer(session_provider)


def stop_heartbeat():
    global _heartbeat_running
    _heartbeat_running = False


def send_heartbeat_for_session(session):
    preferences = _addon_preferences()
    if preferences is None or not preferences.enable_server_transport:
        return False
    if not preferences.device_token:
        return False

    return _heartbeat_worker.enqueue(
        PendingHeartbeat(
            api_base_url=preferences.api_base_url,
            session_id=session.session_id,
            device_token=preferences.device_token,
        )
    )


def _heartbeat_tick(session_provider):
    if not _heartbeat_running:
        return None

    session = session_provider()
    if session is None:
        return None

    send_heartbeat_for_session(session)
    return HEARTBEAT_INTERVAL_SECONDS


def _register_timer(session_provider):
    try:
        import bpy

        bpy.app.timers.register(
            lambda: _heartbeat_tick(session_provider),
            first_interval=HEARTBEAT_INTERVAL_SECONDS,
        )
    except Exception:
        return


def _addon_preferences():
    return current_preferences()


def pending_heartbeat_count():
    return _heartbeat_worker.pending_count()


def clear_pending_heartbeats():
    _heartbeat_worker.clear()


def flush_pending_heartbeats(post_heartbeat=api_client.post_heartbeat):
    sent_count = 0
    original_post_heartbeat = _heartbeat_worker.post_heartbeat
    _heartbeat_worker.post_heartbeat = post_heartbeat
    try:
        while _heartbeat_worker.pending_count():
            sent_count += _heartbeat_worker.flush_once()
    finally:
        _heartbeat_worker.post_heartbeat = original_post_heartbeat
    return sent_count


def _report_heartbeat_failure_to_connection_monitor(result):
    if result.status_code == 401:
        device_connection_monitor.report_transport_auth_failure(_result_error(result))
        return

    if _is_server_or_network_failure(result):
        device_connection_monitor.report_transport_network_failure(_result_error(result))


def _is_server_or_network_failure(result):
    if result.status_code:
        return result.status_code >= 500
    return bool(result.error)


def _result_error(result):
    if result.error:
        return result.error
    if result.status_code:
        return f"HTTP {result.status_code}"
    return "unknown error"
