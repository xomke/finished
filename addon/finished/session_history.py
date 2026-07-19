"""Bounded, safe local history for future support reports."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time

if __package__:
    from . import state_paths
else:  # Supports the project's standalone pure-Python module tests.
    import importlib.util

    _state_paths_spec = importlib.util.spec_from_file_location(
        "finished_state_paths", Path(__file__).with_name("state_paths.py")
    )
    state_paths = importlib.util.module_from_spec(_state_paths_spec)
    _state_paths_spec.loader.exec_module(state_paths)


HISTORY_FILE_ENV = "FINISHED_ADDON_HISTORY_PATH"
HISTORY_SCHEMA_VERSION = 1
MAX_SESSIONS = 20
MAX_AGE_SECONDS = 30 * 24 * 60 * 60


def history_path():
    override = os.getenv(HISTORY_FILE_ENV)
    if override:
        return Path(override).expanduser()
    return state_paths.state_directory() / "render-history.json"


def recent_sessions(now_seconds=None):
    now = _now_seconds(now_seconds)
    sessions = _read_sessions(history_path())
    return _prune(sessions, now)


def record_terminal_session(session, now_seconds=None):
    """Persist a safe terminal-session summary without affecting rendering on failure."""
    now = _now_seconds(now_seconds)
    path = history_path()
    sessions = _prune(_read_sessions(path), now)
    sessions.append(_summary(session, now))
    sessions = sessions[-MAX_SESSIONS:]
    _write_sessions(path, sessions)
    return sessions[-1]


def _summary(session, now):
    return {
        "session_id": str(session.session_id),
        "completed_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "completed_at_unix": now,
        "status": str(session.status),
        "frame_start": int(session.frame_start),
        "frame_end": int(session.frame_end),
        "frame_step": int(session.frame_step),
        "total_frames": int(session.total_frames),
        "completed_frames": int(session.completed_frames),
        "elapsed_seconds": float(session.elapsed_seconds),
        "average_frame_time": _optional_float(session.average_frame_time),
        "render_engine": str(getattr(session, "render_engine", "") or ""),
        "file_format": str(getattr(session, "file_format", "") or ""),
    }


def _read_sessions(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []

    if not isinstance(data, dict) or data.get("schema_version") != HISTORY_SCHEMA_VERSION:
        return []
    sessions = data.get("sessions")
    if not isinstance(sessions, list):
        return []
    return [session for session in sessions if _is_valid_session(session)]


def _write_sessions(path, sessions):
    payload = {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "sessions": sessions,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2, sort_keys=True)
            file.write("\n")
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _prune(sessions, now):
    cutoff = now - MAX_AGE_SECONDS
    return [
        session
        for session in sessions
        if float(session["completed_at_unix"]) >= cutoff
    ][-MAX_SESSIONS:]


def _is_valid_session(session):
    if not isinstance(session, dict):
        return False
    required = {
        "session_id",
        "completed_at",
        "completed_at_unix",
        "status",
        "frame_start",
        "frame_end",
        "frame_step",
        "total_frames",
        "completed_frames",
        "elapsed_seconds",
        "average_frame_time",
        "render_engine",
        "file_format",
    }
    return required.issubset(session) and _valid_timestamp(session["completed_at_unix"])


def _valid_timestamp(value):
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def _optional_float(value):
    return None if value is None else float(value)


def _now_seconds(now_seconds):
    return float(time.time() if now_seconds is None else now_seconds)
