INTERNAL_STATUS_INTERVAL_SECONDS = 5.0


class StatusUpdatePolicy:
    def __init__(self, min_interval_seconds=INTERNAL_STATUS_INTERVAL_SECONDS):
        self.min_interval_seconds = min_interval_seconds
        self._last_status_at = None

    def should_send_status(self, now_seconds):
        if self._last_status_at is None:
            self._last_status_at = now_seconds
            return True

        if now_seconds - self._last_status_at >= self.min_interval_seconds:
            self._last_status_at = now_seconds
            return True

        return False

    def reset(self):
        self._last_status_at = None
