import json
import os
from pathlib import Path
import time
import uuid

from . import local_log
from . import state_paths


QUEUE_DIR_ENV = "FINISHED_ADDON_EVENT_QUEUE_DIR"
DEFAULT_QUEUE_TTL_SECONDS = 24 * 60 * 60
DEFAULT_MAX_QUEUE_FILES = 100
DEFAULT_MAX_ATTEMPTS = 60


class DurableImportantEventQueue:
    def __init__(
        self,
        queue_dir=None,
        now=time.time,
        ttl_seconds=DEFAULT_QUEUE_TTL_SECONDS,
        max_files=DEFAULT_MAX_QUEUE_FILES,
        max_attempts=DEFAULT_MAX_ATTEMPTS,
    ):
        self.queue_dir = Path(queue_dir) if queue_dir is not None else default_queue_dir()
        self.now = now
        self.ttl_seconds = ttl_seconds
        self.max_files = max_files
        self.max_attempts = max_attempts

    def save(self, event, api_base_url, created_at=None, attempts=0):
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        self._prune_to_limit()
        event_id = uuid.uuid4().hex
        created = self.now() if created_at is None else created_at
        record = {
            "id": event_id,
            "created_at": created,
            "attempts": int(attempts),
            "api_base_url": api_base_url,
            "event": {
                "kind": event.kind,
                "title": event.title,
                "body": event.body,
                "payload": event.payload,
            },
        }
        self._path(event_id).write_text(
            json.dumps(record, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        return event_id

    def load_pending(self):
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        records = []
        for path in sorted(self.queue_dir.glob("*.json")):
            record = self._read_record(path)
            if record is None:
                continue
            if self._expired(record):
                local_log.warning(
                    f"Dropping expired durable render event: durable_id={record.get('id')}"
                )
                path.unlink(missing_ok=True)
                continue
            if int(record.get("attempts") or 0) >= self.max_attempts:
                local_log.warning(
                    f"Dropping over-attempt durable render event: durable_id={record.get('id')}"
                )
                path.unlink(missing_ok=True)
                continue
            records.append(record)
        return records

    def delete(self, event_id):
        if not event_id:
            return
        self._path(event_id).unlink(missing_ok=True)

    def update_attempts(self, event_id, attempts):
        if not event_id:
            return
        path = self._path(event_id)
        record = self._read_record(path)
        if record is None:
            return
        record["attempts"] = int(attempts)
        path.write_text(
            json.dumps(record, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )

    def _read_record(self, path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            local_log.warning(f"Dropping corrupt durable render event: path={path}")
            path.unlink(missing_ok=True)
            return None

    def _expired(self, record):
        created_at = float(record.get("created_at") or 0)
        return self.now() - created_at > self.ttl_seconds

    def _prune_to_limit(self):
        files = sorted(self.queue_dir.glob("*.json"), key=lambda item: item.stat().st_mtime)
        while len(files) >= self.max_files:
            files.pop(0).unlink(missing_ok=True)

    def _path(self, event_id):
        return self.queue_dir / f"{event_id}.json"


def default_queue_dir():
    override = os.environ.get(QUEUE_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return state_paths.state_directory() / "render-event-queue"
