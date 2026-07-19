from collections import deque
from datetime import datetime
import os
from pathlib import Path

if __package__:
    from . import state_paths
else:  # Supports the project's standalone pure-Python module tests.
    import importlib.util

    _state_paths_spec = importlib.util.spec_from_file_location(
        "finished_state_paths", Path(__file__).with_name("state_paths.py")
    )
    state_paths = importlib.util.module_from_spec(_state_paths_spec)
    _state_paths_spec.loader.exec_module(state_paths)


MAX_ENTRIES = 500
MAX_LOG_FILE_BYTES = 512 * 1024
LOG_FILE_ENV = "FINISHED_ADDON_LOG_PATH"
_entries = deque(maxlen=MAX_ENTRIES)


def _add(level, message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    entry = f"[{timestamp}] {level}: {message}"
    _entries.append(entry)
    print(f"Finished? {entry}")
    _append_to_file(entry)
    return entry


def info(message):
    return _add("INFO", message)


def warning(message):
    return _add("WARN", message)


def error(message):
    return _add("ERROR", message)


def entries():
    return list(_entries)


def recent(limit=8):
    if limit <= 0:
        return []
    return list(_entries)[-limit:]


def recent_compact(limit=8, max_length=150):
    compact_entries = []
    for entry in recent(limit):
        line = " ".join(entry.splitlines())
        if len(line) > max_length:
            line = line[: max_length - 3] + "..."
        compact_entries.append(line)
    return compact_entries


def latest(default="No Finished? events yet."):
    if not _entries:
        return default
    return _entries[-1]


def clear():
    _entries.clear()
    try:
        log_path().write_text("", encoding="utf-8")
    except OSError:
        pass


def log_path():
    override = os.getenv(LOG_FILE_ENV)
    if override:
        return Path(override).expanduser()
    return state_paths.state_directory() / "finished-addon.log"


def _append_to_file(entry):
    path = log_path()
    line = f"{datetime.now().isoformat(timespec='seconds')} {entry}\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _trim_log_file(path)
        with path.open("a", encoding="utf-8") as file:
            file.write(line)
    except OSError:
        pass


def _trim_log_file(path):
    try:
        if not path.exists() or path.stat().st_size <= MAX_LOG_FILE_BYTES:
            return
        data = path.read_bytes()[-MAX_LOG_FILE_BYTES // 2 :]
        path.write_bytes(data)
    except OSError:
        pass
