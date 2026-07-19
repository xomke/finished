"""Pure validation for the small Finished? add-on release metadata contract."""

from dataclasses import dataclass
import json
import re
from typing import Any, Mapping


CURRENT_RELEASE_CHANNEL = "public"
METADATA_SCHEMA_VERSION = 1
MAX_METADATA_BYTES = 16 * 1024
MAX_DOWNLOAD_URL_LENGTH = 2048
MAX_NOTE_LENGTH = 1000

_VERSION_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CHANNEL_STATUS = {
    "development": "prerelease",
    "public": "stable",
}
_ALLOWED_FIELDS = frozenset(
    {
        "schema_version",
        "channel",
        "status",
        "version",
        "min_blender_version",
        "download_url",
        "sha256",
        "notes_ru",
        "notes_en",
    }
)


class ReleaseMetadataError(ValueError):
    """Raised when release metadata is unsafe or does not match this add-on channel."""


@dataclass(frozen=True)
class ReleaseMetadata:
    channel: str
    status: str
    version: str
    min_blender_version: str | None
    download_url: str
    sha256: str
    notes_ru: str | None
    notes_en: str | None


def parse_release_metadata(raw: bytes | str, *, expected_channel: str = CURRENT_RELEASE_CHANNEL) -> ReleaseMetadata:
    """Parse a bounded ``release.json`` document for the expected release channel."""

    data = _decode_json_object(raw)
    _validate_fields(data)

    schema_version = _required_int(data, "schema_version")
    if schema_version != METADATA_SCHEMA_VERSION:
        raise ReleaseMetadataError("unsupported metadata schema_version")

    channel = _required_string(data, "channel")
    if channel not in _CHANNEL_STATUS:
        raise ReleaseMetadataError("unsupported release channel")
    if channel != expected_channel:
        raise ReleaseMetadataError("release metadata is for a different channel")

    status = _required_string(data, "status")
    if status != _CHANNEL_STATUS[channel]:
        raise ReleaseMetadataError("release status does not match its channel")

    version = _required_version(data, "version")
    min_blender_version = _optional_version(data, "min_blender_version")
    download_url = _required_string(data, "download_url")
    if len(download_url) > MAX_DOWNLOAD_URL_LENGTH or not download_url.startswith("https://"):
        raise ReleaseMetadataError("download_url must be a short HTTPS URL")

    sha256 = _required_string(data, "sha256")
    if not _SHA256_PATTERN.fullmatch(sha256):
        raise ReleaseMetadataError("sha256 must be a lowercase 64-character SHA-256 digest")

    notes_ru = _optional_note(data, "notes_ru")
    notes_en = _optional_note(data, "notes_en")
    if notes_ru is None and notes_en is None:
        raise ReleaseMetadataError("at least one release note is required")

    return ReleaseMetadata(
        channel=channel,
        status=status,
        version=version,
        min_blender_version=min_blender_version,
        download_url=download_url,
        sha256=sha256,
        notes_ru=notes_ru,
        notes_en=notes_en,
    )


def compare_versions(left: str, right: str) -> int:
    """Return -1, 0, or 1 for strict three-part numeric add-on versions."""

    left_parts = _parse_version(left)
    right_parts = _parse_version(right)
    return (left_parts > right_parts) - (left_parts < right_parts)


def is_blender_compatible(metadata: ReleaseMetadata, blender_version: str) -> bool:
    """Return whether Blender meets the optional release minimum."""

    if metadata.min_blender_version is None:
        return True
    return compare_versions(blender_version, metadata.min_blender_version) >= 0


def _decode_json_object(raw: bytes | str) -> Mapping[str, Any]:
    if isinstance(raw, bytes):
        if len(raw) > MAX_METADATA_BYTES:
            raise ReleaseMetadataError("release metadata is too large")
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReleaseMetadataError("release metadata must be UTF-8") from exc
    elif isinstance(raw, str):
        if len(raw.encode("utf-8")) > MAX_METADATA_BYTES:
            raise ReleaseMetadataError("release metadata is too large")
    else:
        raise ReleaseMetadataError("release metadata must be text")

    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ReleaseMetadataError("release metadata must be valid JSON") from exc
    if not isinstance(data, dict):
        raise ReleaseMetadataError("release metadata must be a JSON object")
    return data


def _validate_fields(data: Mapping[str, Any]) -> None:
    unknown = set(data) - _ALLOWED_FIELDS
    if unknown:
        raise ReleaseMetadataError("release metadata contains unknown fields")


def _required_string(data: Mapping[str, Any], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value or value != value.strip():
        raise ReleaseMetadataError(f"{name} must be a non-empty trimmed string")
    return value


def _required_int(data: Mapping[str, Any], name: str) -> int:
    value = data.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ReleaseMetadataError(f"{name} must be an integer")
    return value


def _required_version(data: Mapping[str, Any], name: str) -> str:
    value = _required_string(data, name)
    _parse_version(value)
    return value


def _optional_version(data: Mapping[str, Any], name: str) -> str | None:
    if name not in data:
        return None
    value = _required_string(data, name)
    _parse_version(value)
    return value


def _optional_note(data: Mapping[str, Any], name: str) -> str | None:
    if name not in data:
        return None
    value = _required_string(data, name)
    if len(value) > MAX_NOTE_LENGTH:
        raise ReleaseMetadataError(f"{name} is too long")
    return value


def _parse_version(value: str) -> tuple[int, int, int]:
    match = _VERSION_PATTERN.fullmatch(value)
    if not match:
        raise ReleaseMetadataError("version must use MAJOR.MINOR.PATCH numeric format")
    return tuple(int(part) for part in match.groups())
