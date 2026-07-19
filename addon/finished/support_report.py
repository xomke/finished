"""Create compact, offline support reports without exporting private project data."""

from datetime import datetime, timezone
import json
import platform
from pathlib import Path
import zipfile

from . import local_log
from . import session_history


REPORT_SCHEMA_VERSION = 1
REPORT_FILE_NAME = "finished-support-report.json"
LOG_FILE_NAME = "finished-addon.log"


def default_filename(addon_version, now=None):
    timestamp = (now or datetime.now()).strftime("%Y-%m-%d")
    return f"finished-support-report-{addon_version}-{timestamp}.zip"


def create_report(destination, preferences, *, addon_version, blender_version, now=None):
    destination = Path(destination).expanduser()
    if destination.suffix.lower() != ".zip":
        destination = destination.with_suffix(".zip")

    report = _report_payload(
        preferences,
        addon_version=addon_version,
        blender_version=blender_version,
        now=now,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            REPORT_FILE_NAME,
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        archive.writestr(LOG_FILE_NAME, _safe_log_text(preferences))
    return destination


def _report_payload(preferences, *, addon_version, blender_version, now):
    generated_at = now or datetime.now(timezone.utc)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "environment": {
            "addon_version": str(addon_version),
            "blender_version": str(blender_version),
            "operating_system": platform.system(),
            "operating_system_release": platform.release(),
        },
        "connection": {
            "telegram_connected": bool(getattr(preferences, "device_token", "").strip()),
            "status": str(getattr(preferences, "device_connection_status", "unknown") or "unknown"),
            "last_success_at": float(
                getattr(preferences, "device_connection_last_success_at", 0.0) or 0.0
            ),
            "last_failure_at": float(
                getattr(preferences, "device_connection_last_failure_at", 0.0) or 0.0
            ),
            "failure_count": int(
                getattr(preferences, "device_connection_failure_count", 0) or 0
            ),
            "last_error": str(getattr(preferences, "device_connection_last_error", "") or ""),
        },
        "render_history": session_history.recent_sessions(),
    }


def _safe_log_text(preferences):
    try:
        text = local_log.log_path().read_text(encoding="utf-8")
    except OSError:
        text = "\n".join(local_log.entries())

    for value in (
        getattr(preferences, "device_token", ""),
        getattr(preferences, "pairing_code", ""),
    ):
        if value:
            text = text.replace(value, "[redacted]")
    return text
