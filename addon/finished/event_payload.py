SCHEMA_VERSION = 1

from .event_sequence import next_event_sequence


def build_event_payload(event_type, session, message="", reason="", frame_samples=None):
    payload = {
        "schema_version": SCHEMA_VERSION,
        "event_sequence": next_event_sequence(session.session_id),
        "event_type": event_type,
        "session_id": session.session_id,
        "status": session.status,
        "project_name": session.project_name,
        "scene_name": session.scene_name,
        "frame_start": session.frame_start,
        "frame_end": session.frame_end,
        "frame_step": session.frame_step,
        "total_frames": session.total_frames,
        "current_frame": session.current_frame,
        "completed_frames": session.completed_frames,
        "progress_percent": round(session.progress_percent, 2),
        "elapsed_seconds": round(session.elapsed_seconds, 3),
        "eta_seconds": None if session.eta_seconds is None else round(session.eta_seconds, 3),
        "average_frame_time_seconds": (
            None if session.average_frame_time is None else round(session.average_frame_time, 3)
        ),
        "file_format": session.file_format,
        "preview_supported": session.preview_supported,
        "output_path": session.output_path,
        "render_engine": getattr(session, "render_engine", None),
        "render_samples": getattr(session, "render_samples", None),
        "resolution_x": getattr(session, "resolution_x", None),
        "resolution_y": getattr(session, "resolution_y", None),
    }

    if message:
        payload["message"] = message

    if reason:
        payload["reason"] = reason

    if frame_samples is not None:
        payload["frame_samples"] = frame_samples

    return payload
