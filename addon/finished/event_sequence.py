import threading


_lock = threading.Lock()
_session_id = None
_next_sequence = 1


def reset_event_sequence(session_id=None):
    global _session_id, _next_sequence
    with _lock:
        _session_id = session_id
        _next_sequence = 1


def next_event_sequence(session_id):
    global _session_id, _next_sequence
    with _lock:
        if session_id != _session_id:
            _session_id = session_id
            _next_sequence = 1

        sequence = _next_sequence
        _next_sequence += 1
        return sequence
