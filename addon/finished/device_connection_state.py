from dataclasses import dataclass


STATUS_UNKNOWN = "unknown"
STATUS_CHECKING = "checking"
STATUS_VALID = "valid"
STATUS_INVALID = "invalid"
STATUS_UNPAIRED = "unpaired"
STATUS_SERVER_UNREACHABLE = "server_unreachable"

STATUSES = {
    STATUS_UNKNOWN,
    STATUS_CHECKING,
    STATUS_VALID,
    STATUS_INVALID,
    STATUS_UNPAIRED,
    STATUS_SERVER_UNREACHABLE,
}

BLOCKING_STATUSES = {STATUS_INVALID, STATUS_UNPAIRED}
VALID_FRESH_SECONDS = 120.0


@dataclass(frozen=True)
class DeviceConnectionState:
    status: str = STATUS_UNKNOWN
    last_success_at: float = 0.0
    last_check_at: float = 0.0
    last_failure_at: float = 0.0
    last_error: str = ""
    failure_count: int = 0
    next_check_after: float = 0.0

    def normalized(self):
        if self.status in STATUSES:
            return self
        return DeviceConnectionState(
            status=STATUS_UNKNOWN,
            last_success_at=self.last_success_at,
            last_check_at=self.last_check_at,
            last_failure_at=self.last_failure_at,
            last_error=self.last_error,
            failure_count=max(0, int(self.failure_count or 0)),
            next_check_after=self.next_check_after,
        )


def from_preferences(preferences):
    legacy_status = getattr(preferences, "device_verification_status", STATUS_UNKNOWN)
    status = getattr(preferences, "device_connection_status", legacy_status)
    if status == STATUS_UNKNOWN and legacy_status != STATUS_UNKNOWN:
        status = legacy_status
    legacy_verified_at = float(getattr(preferences, "device_verified_at", 0.0) or 0.0)
    last_success_at = float(
        getattr(
            preferences,
            "device_connection_last_success_at",
            legacy_verified_at if status == STATUS_VALID else 0.0,
        )
        or 0.0
    )
    if last_success_at == 0.0 and status == STATUS_VALID:
        last_success_at = legacy_verified_at
    last_check_at = float(
        getattr(preferences, "device_connection_last_check_at", legacy_verified_at) or 0.0
    )
    if last_check_at == 0.0:
        last_check_at = legacy_verified_at
    return DeviceConnectionState(
        status=str(status or STATUS_UNKNOWN),
        last_success_at=last_success_at,
        last_check_at=last_check_at,
        last_failure_at=float(
            getattr(preferences, "device_connection_last_failure_at", 0.0) or 0.0
        ),
        last_error=str(getattr(preferences, "device_connection_last_error", "") or ""),
        failure_count=max(
            0, int(getattr(preferences, "device_connection_failure_count", 0) or 0)
        ),
        next_check_after=float(
            getattr(preferences, "device_connection_next_check_after", 0.0) or 0.0
        ),
    ).normalized()


def write_preferences(preferences, state):
    state = state.normalized()
    preferences.device_connection_status = state.status
    preferences.device_connection_last_success_at = state.last_success_at
    preferences.device_connection_last_check_at = state.last_check_at
    preferences.device_connection_last_failure_at = state.last_failure_at
    preferences.device_connection_last_error = state.last_error
    preferences.device_connection_failure_count = state.failure_count
    preferences.device_connection_next_check_after = state.next_check_after

    # Keep the previous fields populated while older tests and UI code migrate.
    preferences.device_verification_status = state.status
    preferences.device_verified_at = state.last_success_at or state.last_check_at


def is_fresh_valid(state, now):
    state = state.normalized()
    return (
        state.status == STATUS_VALID
        and state.last_success_at > 0.0
        and now - state.last_success_at <= VALID_FRESH_SECONDS
    )


def blocks_until_pairing(state):
    return state.normalized().status in BLOCKING_STATUSES


def mark_checking(state, now):
    state = state.normalized()
    return DeviceConnectionState(
        status=STATUS_CHECKING,
        last_success_at=state.last_success_at,
        last_check_at=now,
        last_failure_at=state.last_failure_at,
        last_error=state.last_error,
        failure_count=state.failure_count,
        next_check_after=state.next_check_after,
    )


def mark_valid(state, now, *, next_check_after=0.0):
    return DeviceConnectionState(
        status=STATUS_VALID,
        last_success_at=now,
        last_check_at=now,
        last_failure_at=0.0,
        last_error="",
        failure_count=0,
        next_check_after=next_check_after,
    )


def mark_invalid(state, now, error="Invalid device token"):
    state = state.normalized()
    return DeviceConnectionState(
        status=STATUS_INVALID,
        last_success_at=state.last_success_at,
        last_check_at=now,
        last_failure_at=now,
        last_error=error,
        failure_count=state.failure_count + 1,
        next_check_after=0.0,
    )


def mark_unpaired(state, now, error="Device is not paired with Telegram"):
    state = state.normalized()
    return DeviceConnectionState(
        status=STATUS_UNPAIRED,
        last_success_at=0.0,
        last_check_at=now,
        last_failure_at=now,
        last_error=error,
        failure_count=state.failure_count + 1,
        next_check_after=0.0,
    )


def mark_server_unreachable(state, now, error, *, next_check_after=0.0):
    state = state.normalized()
    status = STATUS_VALID if state.last_success_at > 0.0 else STATUS_SERVER_UNREACHABLE
    return DeviceConnectionState(
        status=status,
        last_success_at=state.last_success_at,
        last_check_at=now,
        last_failure_at=now,
        last_error=str(error or "Server unreachable"),
        failure_count=state.failure_count + 1,
        next_check_after=next_check_after,
    )


def apply_device_check_result(state, result, now, *, device_token_configured=True):
    if not device_token_configured:
        return mark_unpaired(state, now, error="Device token is missing")

    if result.ok:
        data = result.data or {}
        if data.get("telegram_chat_id"):
            return mark_valid(state, now)
        return mark_unpaired(state, now)

    if result.status_code == 401:
        return mark_invalid(state, now, error=result.error or "Invalid device token")

    return mark_server_unreachable(state, now, result.error or result.status_code)
