"""Bounded, non-blocking-compatible release metadata checks without Blender dependencies."""

from dataclasses import dataclass
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .release_metadata import (
    CURRENT_RELEASE_CHANNEL,
    MAX_METADATA_BYTES,
    ReleaseMetadata,
    ReleaseMetadataError,
    compare_versions,
    is_blender_compatible,
    parse_release_metadata,
)


CHECK_NOT_CHECKED = "not_checked"
CHECK_CHECKING = "checking"
CHECK_UP_TO_DATE = "up_to_date"
CHECK_UPDATE_AVAILABLE = "update_available"
CHECK_FAILED = "check_failed"

CHECK_TIMEOUT_SECONDS = 3.0
DEFAULT_RELEASE_METADATA_URL = "https://raw.githubusercontent.com/xomke/finished/main/release.json"

_RELEASE_METADATA_HOST = "raw.githubusercontent.com"
_RELEASE_METADATA_PATH = "/xomke/finished/main/release.json"
_PACKAGE_DOWNLOAD_HOST = "github.com"
_PACKAGE_DOWNLOAD_PATH_PATTERN = re.compile('^/xomke/finished/releases/download/v[0-9]+\\.[0-9]+\\.[0-9]+/finished-[0-9]+\\.[0-9]+\\.[0-9]+\\.zip$')


@dataclass(frozen=True)
class UpdateCheckResult:
    state: str = CHECK_NOT_CHECKED
    metadata: ReleaseMetadata | None = None
    error: str = ""


def check_for_update(
    current_version: str,
    blender_version: str,
    *,
    metadata_url: str = DEFAULT_RELEASE_METADATA_URL,
    timeout: float = CHECK_TIMEOUT_SECONDS,
    opener=None,
) -> UpdateCheckResult:
    """Safely check one release document once; all failures become a result state."""

    if not is_allowed_metadata_url(metadata_url):
        return UpdateCheckResult(state=CHECK_FAILED, error="release_url_not_allowed")

    request = Request(
        metadata_url,
        headers={"Accept": "application/json", "User-Agent": "Finished?-addon-update-checker"},
        method="GET",
    )
    default_opener = opener is None
    opener = opener or build_opener(_RejectRedirectHandler())

    try:
        raw = _read_metadata(opener, request, timeout)
    except (socket.timeout, TimeoutError):
        return UpdateCheckResult(state=CHECK_FAILED, error="request_timeout")
    except HTTPError:
        return UpdateCheckResult(state=CHECK_FAILED, error="http_error")
    except (URLError, OSError):
        if not default_opener:
            return UpdateCheckResult(state=CHECK_FAILED, error="network_error")
        try:
            raw = _read_metadata(
                build_opener(ProxyHandler({}), _RejectRedirectHandler()), request, timeout
            )
        except (socket.timeout, TimeoutError):
            return UpdateCheckResult(state=CHECK_FAILED, error="request_timeout")
        except HTTPError:
            return UpdateCheckResult(state=CHECK_FAILED, error="http_error")
        except (URLError, OSError):
            return UpdateCheckResult(state=CHECK_FAILED, error="network_error")
        except Exception:
            return UpdateCheckResult(state=CHECK_FAILED, error="request_failed")
    except Exception:
        return UpdateCheckResult(state=CHECK_FAILED, error="request_failed")

    if len(raw) > MAX_METADATA_BYTES:
        return UpdateCheckResult(state=CHECK_FAILED, error="response_too_large")

    try:
        metadata = parse_release_metadata(raw, expected_channel=CURRENT_RELEASE_CHANNEL)
        comparison = compare_versions(metadata.version, current_version)
    except ReleaseMetadataError:
        return UpdateCheckResult(state=CHECK_FAILED, error="invalid_metadata")

    if comparison <= 0:
        return UpdateCheckResult(state=CHECK_UP_TO_DATE, metadata=metadata)
    if not is_blender_compatible(metadata, blender_version):
        return UpdateCheckResult(
            state=CHECK_UP_TO_DATE,
            metadata=metadata,
            error="blender_incompatible",
        )
    return UpdateCheckResult(state=CHECK_UPDATE_AVAILABLE, metadata=metadata)


def _read_metadata(opener, request, timeout):
    with opener.open(request, timeout=timeout) as response:
        status_code = response.getcode()
        if not 200 <= status_code < 300:
            raise HTTPError(
                request.full_url, status_code, "metadata HTTP status", getattr(response, "headers", None), None
            )
        return response.read(MAX_METADATA_BYTES + 1)


def is_allowed_metadata_url(url: str) -> bool:
    """Allow only this package's fixed Finished? release document."""

    if not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return False
    if port not in (None, 443):
        return False
    return parsed.hostname == _RELEASE_METADATA_HOST and parsed.path == _RELEASE_METADATA_PATH


def is_allowed_package_url(url: str) -> bool:
    """Allow only versioned ZIPs selected by this package's release profile."""

    if not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and not parsed.username
        and not parsed.password
        and port in (None, 443)
        and parsed.hostname == _PACKAGE_DOWNLOAD_HOST
        and _PACKAGE_DOWNLOAD_PATH_PATTERN.fullmatch(parsed.path) is not None
    )


class _RejectRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise URLError("release metadata redirects are not allowed")
