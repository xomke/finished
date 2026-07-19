from collections import deque
from dataclasses import dataclass
import threading

from . import api_client
from . import device_connection_monitor
from .addon_preferences import current_preferences
from . import local_log


COMMAND_POLL_INTERVAL_SECONDS = 5.0
MAX_PENDING_POLLS = 1
MAX_CLAIMED_COMMANDS = 1
MAX_PENDING_RESULTS = 1
KNOWN_COMMAND_TYPES = {
    "request_preview",
    "stop_render",
    "request_log",
}
_command_polling_running = False
_polling_generation = 0


@dataclass(frozen=True)
class PendingCommandPoll:
    api_base_url: str
    session_id: str
    device_token: str


@dataclass(frozen=True)
class PendingCommandResult:
    api_base_url: str
    session_id: str
    command_id: str
    status: str
    result: dict
    error: str
    device_token: str


class CommandPollingWorker:
    def __init__(
        self,
        get_next_command=None,
        start_thread=None,
        max_pending_polls=MAX_PENDING_POLLS,
        max_claimed_commands=MAX_CLAIMED_COMMANDS,
        http_client=None,
    ):
        self.get_next_command = get_next_command
        self.start_thread = start_thread or self._start_thread
        self.max_pending_polls = max_pending_polls
        self.max_claimed_commands = max_claimed_commands
        self.http_client = http_client or api_client.ReusableHttpClient()
        self._pending_polls = deque()
        self._claimed_commands = deque()
        self._lock = threading.Lock()
        self._running = False
        self._generation = 0

    def enqueue(self, pending):
        with self._lock:
            if self._claimed_commands or len(self._pending_polls) >= self.max_pending_polls:
                return False
            self._pending_polls.append((self._generation, pending))
            if not self._ensure_running_locked():
                self._pending_polls.pop()
                return False
        return True

    def flush_once(self):
        queued = self._take_next_poll()
        if queued is None:
            return 0
        generation, pending = queued
        self._poll(pending, generation)
        return 1

    def take_claimed_command(self):
        with self._lock:
            if not self._claimed_commands:
                return None
            return self._claimed_commands.popleft()

    def pending_count(self):
        with self._lock:
            return len(self._pending_polls) + len(self._claimed_commands)

    def clear(self):
        with self._lock:
            self._generation += 1
            self._pending_polls.clear()
            self._claimed_commands.clear()
        self.http_client.close()

    def _ensure_running_locked(self):
        if self._running:
            return True
        self._running = True
        try:
            self.start_thread(self._worker_loop)
            return True
        except Exception as exc:
            self._running = False
            local_log.warning(f"Command polling worker start failed: error={exc}")
            return False

    def _worker_loop(self):
        while True:
            queued = self._take_next_poll()
            if queued is None:
                return
            generation, pending = queued
            self._poll(pending, generation)

    def _take_next_poll(self):
        with self._lock:
            if self._pending_polls:
                return self._pending_polls.popleft()
            self._running = False
            return None

    def _poll(self, pending, generation=None):
        if generation is None:
            with self._lock:
                generation = self._generation
        try:
            if self.get_next_command is not None:
                response = self.get_next_command(
                    pending.api_base_url,
                    device_token=pending.device_token,
                    timeout=api_client.DEFAULT_TIMEOUT_SECONDS,
                )
            else:
                response = self.http_client.get_next_command(
                    pending.api_base_url,
                    device_token=pending.device_token,
                    timeout=api_client.DEFAULT_TIMEOUT_SECONDS,
                )
        except Exception as exc:
            self.http_client.close()
            response = api_client.ApiResult(ok=False, error=str(exc))

        if not response.ok:
            _report_poll_failure_to_connection_monitor(response)
            local_log.warning(
                f"Command poll failed: session={pending.session_id} "
                f"error={_result_error(response)}"
            )
            return False

        device_connection_monitor.report_transport_success()
        response_data = response.data if isinstance(response.data, dict) else {}
        command = response_data.get("command")
        if command is None:
            return True
        if not isinstance(command, dict):
            local_log.warning(
                f"Ignoring malformed command response: session={pending.session_id}"
            )
            return False
        if command.get("session_id") != pending.session_id:
            local_log.warning(
                "Ignoring command for another render session: "
                f"active_session={pending.session_id} command_session={command.get('session_id')}"
            )
            return False

        with self._lock:
            if generation != self._generation:
                return False
            if len(self._claimed_commands) >= self.max_claimed_commands:
                local_log.warning(
                    f"Dropping claimed command because handoff buffer is full: "
                    f"command={command.get('command_id')}"
                )
                return False
            self._claimed_commands.append(command)

        local_log.info(
            f"Render command claimed: session={pending.session_id} "
            f"command={command.get('command_id')} type={command.get('command_type')}"
        )
        return True

    @staticmethod
    def _start_thread(target):
        thread = threading.Thread(
            target=target,
            name="FinishedCommandPolling",
            daemon=True,
        )
        thread.start()


class CommandResultWorker:
    def __init__(
        self,
        post_command_result=None,
        start_thread=None,
        max_pending=MAX_PENDING_RESULTS,
        http_client=None,
    ):
        self.post_command_result = post_command_result
        self.start_thread = start_thread or self._start_thread
        self.max_pending = max_pending
        self.http_client = http_client or api_client.ReusableHttpClient()
        self._pending = deque()
        self._lock = threading.Lock()
        self._running = False

    def enqueue(self, pending):
        with self._lock:
            if len(self._pending) >= self.max_pending or self._running:
                return False
            self._pending.append(pending)
            if not self._ensure_running_locked():
                self._pending.pop()
                return False
        return True

    def flush_once(self):
        pending = self._take_next()
        if pending is None:
            return 0
        self._deliver(pending)
        return 1

    def pending_count(self):
        with self._lock:
            return max(len(self._pending), 1 if self._running else 0)

    def clear(self):
        with self._lock:
            self._pending.clear()
        self.http_client.close()

    def _ensure_running_locked(self):
        if self._running:
            return True
        self._running = True
        try:
            self.start_thread(self._worker_loop)
            return True
        except Exception as exc:
            self._running = False
            local_log.warning(f"Command result worker start failed: error={exc}")
            return False

    def _worker_loop(self):
        while True:
            pending = self._take_next()
            if pending is None:
                return
            self._deliver(pending)

    def _take_next(self):
        with self._lock:
            if self._pending:
                return self._pending.popleft()
            self._running = False
            return None

    def _deliver(self, pending):
        try:
            if self.post_command_result is not None:
                response = self.post_command_result(
                    pending.api_base_url,
                    pending.command_id,
                    pending.status,
                    result=pending.result,
                    error=pending.error,
                    device_token=pending.device_token,
                    timeout=api_client.DEFAULT_TIMEOUT_SECONDS,
                )
            else:
                response = self.http_client.post_command_result(
                    pending.api_base_url,
                    pending.command_id,
                    pending.status,
                    result=pending.result,
                    error=pending.error,
                    device_token=pending.device_token,
                    timeout=api_client.DEFAULT_TIMEOUT_SECONDS,
                )
        except Exception as exc:
            self.http_client.close()
            response = api_client.ApiResult(ok=False, error=str(exc))

        if response.ok:
            device_connection_monitor.report_transport_success()
            local_log.info(
                f"Render command result sent: session={pending.session_id} "
                f"command={pending.command_id} status={pending.status}"
            )
            return True

        _report_poll_failure_to_connection_monitor(response)
        local_log.warning(
            f"Render command result failed: session={pending.session_id} "
            f"command={pending.command_id} error={_result_error(response)}"
        )
        return False

    @staticmethod
    def _start_thread(target):
        thread = threading.Thread(
            target=target,
            name="FinishedCommandResult",
            daemon=True,
        )
        thread.start()


_command_polling_worker = CommandPollingWorker()
_command_result_worker = CommandResultWorker()


def start_command_polling(session_provider):
    global _command_polling_running, _polling_generation
    _polling_generation += 1
    _command_polling_running = True
    _register_timer(session_provider, _polling_generation)


def stop_command_polling():
    global _command_polling_running, _polling_generation
    _polling_generation += 1
    _command_polling_running = False
    _command_polling_worker.clear()


def request_command_for_session(session):
    preferences = current_preferences()
    if preferences is None or not preferences.enable_server_transport:
        return False
    if not preferences.device_token:
        return False
    if _command_result_worker.pending_count():
        return False
    return _command_polling_worker.enqueue(
        PendingCommandPoll(
            api_base_url=preferences.api_base_url,
            session_id=session.session_id,
            device_token=preferences.device_token,
        )
    )


def _command_poll_tick(session_provider, generation=None):
    if not _command_polling_running:
        return None
    if generation is not None and generation != _polling_generation:
        return None
    session = session_provider()
    if session is None:
        return None
    dispatch_claimed_command(session)
    request_command_for_session(session)
    return COMMAND_POLL_INTERVAL_SECONDS


def _register_timer(session_provider, generation):
    try:
        import bpy

        bpy.app.timers.register(
            lambda: _command_poll_tick(session_provider, generation),
            first_interval=COMMAND_POLL_INTERVAL_SECONDS,
        )
    except Exception:
        return


def take_claimed_command():
    return _command_polling_worker.take_claimed_command()


def dispatch_claimed_command(session):
    command = take_claimed_command()
    if command is None:
        return False

    preferences = current_preferences()
    if preferences is None or not preferences.enable_server_transport:
        local_log.warning(
            f"Render command result deferred without server transport: "
            f"command={command.get('command_id')}"
        )
        return False
    if not preferences.device_token:
        local_log.warning(
            f"Render command result deferred without device token: "
            f"command={command.get('command_id')}"
        )
        return False

    command_id = str(command.get("command_id") or "")
    command_session_id = str(command.get("session_id") or "")
    command_type = str(command.get("command_type") or "")
    if not command_id:
        local_log.warning("Ignoring claimed render command without command id.")
        return False

    if command_session_id != session.session_id:
        error = "active_render_session_changed"
    elif command_type not in KNOWN_COMMAND_TYPES:
        error = "unsupported_command_type"
    else:
        error = f"{command_type}_not_implemented"

    pending = PendingCommandResult(
        api_base_url=preferences.api_base_url,
        session_id=command_session_id or session.session_id,
        command_id=command_id,
        status="failed",
        result={},
        error=error,
        device_token=preferences.device_token,
    )
    if not _command_result_worker.enqueue(pending):
        local_log.warning(
            f"Render command result queue is busy: command={command_id}"
        )
        return False

    local_log.info(
        f"Render command dispatched: session={pending.session_id} "
        f"command={command_id} type={command_type or 'unknown'} result={error}"
    )
    return True


def pending_command_count():
    return (
        _command_polling_worker.pending_count()
        + _command_result_worker.pending_count()
    )


def clear_pending_commands():
    _command_polling_worker.clear()
    _command_result_worker.clear()


def _report_poll_failure_to_connection_monitor(result):
    if result.status_code == 401:
        device_connection_monitor.report_transport_auth_failure(_result_error(result))
        return
    if result.status_code >= 500 or (not result.status_code and result.error):
        device_connection_monitor.report_transport_network_failure(_result_error(result))


def _result_error(result):
    if result.error:
        return result.error
    if result.status_code:
        return f"HTTP {result.status_code}"
    return "unknown error"
