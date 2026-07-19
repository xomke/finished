"""Profile-specific local paths for Finished? state that must not cross environments."""

import json
import os
from pathlib import Path


PROFILE_DOCUMENT_NAME = "package_profile.json"
PUBLIC_NAMESPACE = "finished"
DEVELOPMENT_NAMESPACE = "finished-dev"
_VALID_NAMESPACES = frozenset({PUBLIC_NAMESPACE, DEVELOPMENT_NAMESPACE})
_LEGACY_STATE_ENTRIES = (
    "finished-addon.log",
    "render-history.json",
    "render-event-queue",
    "updates",
)


def local_state_namespace(profile_document_path=None):
    """Return the package-selected state namespace, safely defaulting to legacy behavior."""

    path = Path(profile_document_path) if profile_document_path else Path(__file__).with_name(PROFILE_DOCUMENT_NAME)
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return PUBLIC_NAMESPACE
    namespace = profile.get("local_state_namespace") if isinstance(profile, dict) else None
    return namespace if namespace in _VALID_NAMESPACES else PUBLIC_NAMESPACE


def state_directory(namespace=None, home=None):
    """Return this package profile's root state directory and migrate legacy Dev state once."""

    namespace = namespace or local_state_namespace()
    home = Path(home) if home is not None else Path.home()
    if namespace == DEVELOPMENT_NAMESPACE:
        _migrate_legacy_development_state(home)
    return _state_directory(namespace, home)


def blender_config_subdirectory():
    """Return the Blender CONFIG directory name for this package profile."""

    return local_state_namespace()


def _state_directory(namespace, home):
    if namespace not in _VALID_NAMESPACES:
        raise ValueError("unknown Finished? local-state namespace")
    return home / f".{namespace}"


def _migrate_legacy_development_state(home):
    """Move owner prerelease files out of the future public directory when safe to do so."""

    legacy_directory = _state_directory(PUBLIC_NAMESPACE, home)
    development_directory = _state_directory(DEVELOPMENT_NAMESPACE, home)
    for name in _LEGACY_STATE_ENTRIES:
        source = legacy_directory / name
        destination = development_directory / name
        if not source.exists() or destination.exists():
            continue
        try:
            development_directory.mkdir(parents=True, exist_ok=True)
            os.replace(source, destination)
        except OSError:
            continue
