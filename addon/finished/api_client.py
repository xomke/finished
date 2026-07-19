import json
import http.client
import time
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.request import Request
from urllib.request import urlopen
from urllib.parse import urlparse


DEFAULT_TIMEOUT_SECONDS = 3.0
CONNECTION_CHECK_TIMEOUT_SECONDS = 6.0
CONNECTION_CHECK_RETRY_DELAYS_SECONDS = (0.5,)
PAIRING_TIMEOUT_SECONDS = 6.0
PAIRING_RETRY_DELAY_SECONDS = 0.5
RENDER_START_DEVICE_CHECK_TIMEOUT_SECONDS = 0.75
IMPORTANT_EVENT_TIMEOUT_SECONDS = 4.0
REUSABLE_CONNECTION_IDLE_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class ApiResult:
    ok: bool
    status_code: int = 0
    error: str = ""
    data: dict = None


def post_render_event(api_base_url, payload, device_token="", timeout=DEFAULT_TIMEOUT_SECONDS):
    url = _join_url(api_base_url, "/api/render-events")
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    if device_token:
        headers["Authorization"] = f"Bearer {device_token}"

    request = Request(url, data=data, headers=headers, method="POST")

    try:
        with urlopen(request, timeout=timeout) as response:
            status_code = response.getcode()
            return ApiResult(ok=200 <= status_code < 300, status_code=status_code)
    except HTTPError as exc:
        return ApiResult(ok=False, status_code=exc.code, error=_http_error_message(exc))
    except URLError as exc:
        return ApiResult(ok=False, error=str(exc.reason))
    except UnicodeError as exc:
        return ApiResult(ok=False, error=str(exc))
    except TimeoutError as exc:
        return ApiResult(ok=False, error=str(exc))
    except OSError as exc:
        return ApiResult(ok=False, error=str(exc))


class ReusableHttpClient:
    def __init__(
        self,
        connection_factory=None,
        now=time.monotonic,
        idle_timeout_seconds=REUSABLE_CONNECTION_IDLE_TIMEOUT_SECONDS,
    ):
        self.connection_factory = connection_factory or _default_connection_factory
        self.now = now
        self.idle_timeout_seconds = idle_timeout_seconds
        self._connection = None
        self._origin = None
        self._last_used_at = None

    def post_render_event(self, api_base_url, payload, device_token="", timeout=DEFAULT_TIMEOUT_SECONDS):
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if device_token:
            headers["Authorization"] = f"Bearer {device_token}"
        return self.request(
            "POST",
            api_base_url,
            "/api/render-events",
            body=body,
            headers=headers,
            timeout=timeout,
        )

    def post_heartbeat(self, api_base_url, session_id, device_token="", timeout=DEFAULT_TIMEOUT_SECONDS):
        headers = {"Accept": "application/json"}
        if device_token:
            headers["Authorization"] = f"Bearer {device_token}"
        return self.request(
            "POST",
            api_base_url,
            f"/api/render-sessions/{session_id}/heartbeat",
            headers=headers,
            timeout=timeout,
        )

    def post_frame_samples(
        self,
        api_base_url,
        session_id,
        batch,
        device_token="",
        timeout=DEFAULT_TIMEOUT_SECONDS,
    ):
        body = json.dumps(batch).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if device_token:
            headers["Authorization"] = f"Bearer {device_token}"
        return self.request(
            "POST",
            api_base_url,
            f"/api/render-sessions/{session_id}/frame-samples",
            body=body,
            headers=headers,
            timeout=timeout,
        )

    def get_next_command(self, api_base_url, device_token="", timeout=DEFAULT_TIMEOUT_SECONDS):
        headers = {"Accept": "application/json"}
        if device_token:
            headers["Authorization"] = f"Bearer {device_token}"
        return self.request(
            "GET",
            api_base_url,
            "/api/commands/next",
            headers=headers,
            timeout=timeout,
        )

    def post_command_result(
        self,
        api_base_url,
        command_id,
        status,
        result=None,
        error="",
        device_token="",
        timeout=DEFAULT_TIMEOUT_SECONDS,
    ):
        body = json.dumps(
            {
                "status": status,
                "result": result or {},
                "error": error or "",
            }
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if device_token:
            headers["Authorization"] = f"Bearer {device_token}"
        return self.request(
            "POST",
            api_base_url,
            f"/api/commands/{command_id}/result",
            body=body,
            headers=headers,
            timeout=timeout,
        )

    def request(self, method, api_base_url, path, body=None, headers=None, timeout=DEFAULT_TIMEOUT_SECONDS):
        headers = headers or {}
        try:
            parsed = urlparse(api_base_url)
            origin = _origin_from_parsed_url(parsed)
            request_path = _request_path(parsed, path)
            connection = self._connection_for(origin, timeout)
            connection.request(method, request_path, body=body, headers=headers)
            response = connection.getresponse()
            response_body = response.read()
            status_code = response.status
            self._last_used_at = self.now()
            if status_code == 401 or status_code >= 500:
                self.close()
            return ApiResult(
                ok=200 <= status_code < 300,
                status_code=status_code,
                error="" if 200 <= status_code < 300 else _http_response_error_message(status_code, response_body),
                data=_json_response_data(response_body),
            )
        except (HTTPError, URLError, UnicodeError, TimeoutError, OSError, ValueError) as exc:
            self.close()
            return _exception_result(exc)

    def close_if_idle(self):
        if self._connection is None or self._last_used_at is None:
            return False
        if self.now() - self._last_used_at <= self.idle_timeout_seconds:
            return False
        self.close()
        return True

    def close(self):
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:
                pass
        self._connection = None
        self._origin = None
        self._last_used_at = None

    def _connection_for(self, origin, timeout):
        self.close_if_idle()
        if self._connection is not None and self._origin == origin:
            _apply_connection_timeout(self._connection, timeout)
            return self._connection
        self.close()
        self._connection = self.connection_factory(origin, timeout)
        self._origin = origin
        return self._connection


def post_heartbeat(api_base_url, session_id, device_token="", timeout=DEFAULT_TIMEOUT_SECONDS):
    url = _join_url(api_base_url, f"/api/render-sessions/{session_id}/heartbeat")
    headers = {
        "Accept": "application/json",
    }

    if device_token:
        headers["Authorization"] = f"Bearer {device_token}"

    request = Request(url, headers=headers, method="POST")

    try:
        with urlopen(request, timeout=timeout) as response:
            status_code = response.getcode()
            return ApiResult(ok=200 <= status_code < 300, status_code=status_code)
    except HTTPError as exc:
        return ApiResult(ok=False, status_code=exc.code, error=_http_error_message(exc))
    except URLError as exc:
        return ApiResult(ok=False, error=str(exc.reason))
    except UnicodeError as exc:
        return ApiResult(ok=False, error=str(exc))
    except TimeoutError as exc:
        return ApiResult(ok=False, error=str(exc))
    except OSError as exc:
        return ApiResult(ok=False, error=str(exc))


def post_frame_samples(
    api_base_url,
    session_id,
    batch,
    device_token="",
    timeout=DEFAULT_TIMEOUT_SECONDS,
):
    url = _join_url(api_base_url, f"/api/render-sessions/{session_id}/frame-samples")
    data = json.dumps(batch).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if device_token:
        headers["Authorization"] = f"Bearer {device_token}"
    request = Request(url, data=data, headers=headers, method="POST")
    return _open_json_request(request, timeout)


def get_next_command(api_base_url, device_token="", timeout=DEFAULT_TIMEOUT_SECONDS):
    url = _join_url(api_base_url, "/api/commands/next")
    headers = {"Accept": "application/json"}
    if device_token:
        headers["Authorization"] = f"Bearer {device_token}"
    request = Request(url, headers=headers, method="GET")
    return _open_json_request(request, timeout)


def post_command_result(
    api_base_url,
    command_id,
    status,
    result=None,
    error="",
    device_token="",
    timeout=DEFAULT_TIMEOUT_SECONDS,
):
    url = _join_url(api_base_url, f"/api/commands/{command_id}/result")
    data = json.dumps(
        {
            "status": status,
            "result": result or {},
            "error": error or "",
        }
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if device_token:
        headers["Authorization"] = f"Bearer {device_token}"
    request = Request(url, data=data, headers=headers, method="POST")
    return _open_json_request(request, timeout)


def check_health(api_base_url, timeout=DEFAULT_TIMEOUT_SECONDS):
    url = _join_url(api_base_url, "/health")
    request = Request(url, headers={"Accept": "application/json"}, method="GET")

    try:
        with urlopen(request, timeout=timeout) as response:
            status_code = response.getcode()
            return ApiResult(ok=200 <= status_code < 300, status_code=status_code)
    except HTTPError as exc:
        return ApiResult(ok=False, status_code=exc.code, error=_http_error_message(exc))
    except URLError as exc:
        return ApiResult(ok=False, error=str(exc.reason))
    except UnicodeError as exc:
        return ApiResult(ok=False, error=str(exc))
    except TimeoutError as exc:
        return ApiResult(ok=False, error=str(exc))
    except OSError as exc:
        return ApiResult(ok=False, error=str(exc))


def check_device(api_base_url, device_token, timeout=DEFAULT_TIMEOUT_SECONDS):
    url = _join_url(api_base_url, "/api/devices/me")
    headers = {"Accept": "application/json"}
    if device_token:
        headers["Authorization"] = f"Bearer {device_token}"

    request = Request(url, headers=headers, method="GET")

    try:
        with urlopen(request, timeout=timeout) as response:
            status_code = response.getcode()
            body = response.read().decode("utf-8")
            data = json.loads(body) if body else {}
            return ApiResult(ok=200 <= status_code < 300, status_code=status_code, data=data)
    except HTTPError as exc:
        return ApiResult(ok=False, status_code=exc.code, error=_http_error_message(exc))
    except URLError as exc:
        return ApiResult(ok=False, error=str(exc.reason))
    except UnicodeError as exc:
        return ApiResult(ok=False, error=str(exc))
    except TimeoutError as exc:
        return ApiResult(ok=False, error=str(exc))
    except OSError as exc:
        return ApiResult(ok=False, error=str(exc))


def check_in_device(
    api_base_url,
    device_token,
    device_name="",
    blender_version="",
    addon_version="",
    timeout=DEFAULT_TIMEOUT_SECONDS,
):
    url = _join_url(api_base_url, "/api/devices/me/check-in")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if device_token:
        headers["Authorization"] = f"Bearer {device_token}"
    body = json.dumps(
        {
            "device_name": str(device_name or "") or None,
            "blender_version": str(blender_version or "") or None,
            "addon_version": str(addon_version or "") or None,
        }
    ).encode("utf-8")
    result = _open_json_request(
        Request(url, data=body, headers=headers, method="POST"),
        timeout,
    )
    if result.status_code in {404, 405}:
        return check_device(api_base_url, device_token, timeout=timeout)
    return result


def disconnect_device(api_base_url, device_token, timeout=DEFAULT_TIMEOUT_SECONDS):
    url = _join_url(api_base_url, "/api/devices/me/disconnect")
    headers = {"Accept": "application/json"}
    if device_token:
        headers["Authorization"] = f"Bearer {device_token}"

    request = Request(url, headers=headers, method="POST")

    try:
        with urlopen(request, timeout=timeout) as response:
            status_code = response.getcode()
            body = response.read().decode("utf-8")
            payload = json.loads(body) if body else {}
            return ApiResult(ok=200 <= status_code < 300, status_code=status_code, data=payload)
    except HTTPError as exc:
        return ApiResult(ok=False, status_code=exc.code, error=_http_error_message(exc))
    except URLError as exc:
        return ApiResult(ok=False, error=str(exc.reason))
    except UnicodeError as exc:
        return ApiResult(ok=False, error=str(exc))
    except TimeoutError as exc:
        return ApiResult(ok=False, error=str(exc))
    except OSError as exc:
        return ApiResult(ok=False, error=str(exc))


def dev_register_device(api_base_url, device_name="Local Blender", timeout=DEFAULT_TIMEOUT_SECONDS):
    url = _join_url(api_base_url, "/api/devices/dev-register")
    data = json.dumps({"device_name": device_name}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    request = Request(url, data=data, headers=headers, method="POST")

    try:
        with urlopen(request, timeout=timeout) as response:
            status_code = response.getcode()
            body = response.read().decode("utf-8")
            payload = json.loads(body) if body else {}
            return ApiResult(ok=200 <= status_code < 300, status_code=status_code, data=payload)
    except HTTPError as exc:
        return ApiResult(ok=False, status_code=exc.code, error=_http_error_message(exc))
    except URLError as exc:
        return ApiResult(ok=False, error=str(exc.reason))
    except UnicodeError as exc:
        return ApiResult(ok=False, error=str(exc))
    except TimeoutError as exc:
        return ApiResult(ok=False, error=str(exc))
    except OSError as exc:
        return ApiResult(ok=False, error=str(exc))


def dev_create_pairing_code(api_base_url, timeout=DEFAULT_TIMEOUT_SECONDS):
    url = _join_url(api_base_url, "/api/pairing/dev-code")
    data = json.dumps({"telegram_chat_id": "local-dev"}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    request = Request(url, data=data, headers=headers, method="POST")

    try:
        with urlopen(request, timeout=timeout) as response:
            status_code = response.getcode()
            body = response.read().decode("utf-8")
            payload = json.loads(body) if body else {}
            return ApiResult(ok=200 <= status_code < 300, status_code=status_code, data=payload)
    except HTTPError as exc:
        return ApiResult(ok=False, status_code=exc.code, error=_http_error_message(exc))
    except URLError as exc:
        return ApiResult(ok=False, error=str(exc.reason))
    except UnicodeError as exc:
        return ApiResult(ok=False, error=str(exc))
    except TimeoutError as exc:
        return ApiResult(ok=False, error=str(exc))
    except OSError as exc:
        return ApiResult(ok=False, error=str(exc))


def complete_pairing(
    api_base_url,
    pairing_code,
    device_name="Local Blender",
    blender_version="",
    addon_version="",
    timeout=DEFAULT_TIMEOUT_SECONDS,
):
    url = _join_url(api_base_url, "/api/pairing/complete")
    data = json.dumps(
        {
            "pairing_code": pairing_code,
            "device_name": device_name,
            "blender_version": str(blender_version or "") or None,
            "addon_version": str(addon_version or "") or None,
        }
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    request = Request(url, data=data, headers=headers, method="POST")

    try:
        with urlopen(request, timeout=timeout) as response:
            status_code = response.getcode()
            body = response.read().decode("utf-8")
            payload = json.loads(body) if body else {}
            return ApiResult(ok=200 <= status_code < 300, status_code=status_code, data=payload)
    except HTTPError as exc:
        return ApiResult(ok=False, status_code=exc.code, error=_http_error_message(exc))
    except URLError as exc:
        return ApiResult(ok=False, error=str(exc.reason))
    except UnicodeError as exc:
        return ApiResult(ok=False, error=str(exc))
    except TimeoutError as exc:
        return ApiResult(ok=False, error=str(exc))
    except OSError as exc:
        return ApiResult(ok=False, error=str(exc))


def _join_url(api_base_url, path):
    return api_base_url.rstrip("/") + "/" + path.lstrip("/")


def _origin_from_parsed_url(parsed):
    scheme = parsed.scheme or "https"
    host = parsed.hostname
    if not host:
        raise ValueError("Missing API host")
    port = parsed.port
    return (scheme, host, port)


def _request_path(parsed, path):
    base_path = parsed.path.rstrip("/")
    request_path = "/" + path.lstrip("/")
    if base_path:
        request_path = base_path + request_path
    if parsed.query:
        request_path = request_path + "?" + parsed.query
    return request_path


def _default_connection_factory(origin, timeout):
    scheme, host, port = origin
    connection_type = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    return connection_type(host, port=port, timeout=timeout)


def _apply_connection_timeout(connection, timeout):
    try:
        connection.timeout = timeout
        if getattr(connection, "sock", None) is not None:
            connection.sock.settimeout(timeout)
    except Exception:
        pass


def _exception_result(exc):
    if isinstance(exc, HTTPError):
        return ApiResult(ok=False, status_code=exc.code, error=_http_error_message(exc))
    if isinstance(exc, URLError):
        return ApiResult(ok=False, error=str(exc.reason))
    return ApiResult(ok=False, error=str(exc))


def _http_response_error_message(status_code, body):
    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
        detail = payload.get("detail")
        if detail:
            return str(detail)
    except (UnicodeError, json.JSONDecodeError):
        pass
    return f"HTTP {status_code}"


def _json_response_data(body):
    try:
        return json.loads(body.decode("utf-8")) if body else {}
    except (UnicodeError, json.JSONDecodeError):
        return None


def _open_json_request(request, timeout):
    try:
        with urlopen(request, timeout=timeout) as response:
            status_code = response.getcode()
            body = response.read()
            return ApiResult(
                ok=200 <= status_code < 300,
                status_code=status_code,
                error="" if 200 <= status_code < 300 else _http_response_error_message(status_code, body),
                data=_json_response_data(body),
            )
    except HTTPError as exc:
        return ApiResult(ok=False, status_code=exc.code, error=_http_error_message(exc))
    except URLError as exc:
        return ApiResult(ok=False, error=str(exc.reason))
    except UnicodeError as exc:
        return ApiResult(ok=False, error=str(exc))
    except TimeoutError as exc:
        return ApiResult(ok=False, error=str(exc))
    except OSError as exc:
        return ApiResult(ok=False, error=str(exc))


def _http_error_message(exc):
    try:
        body = exc.read().decode("utf-8")
        payload = json.loads(body) if body else {}
        detail = payload.get("detail")
        if detail:
            return str(detail)
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    return str(exc)
