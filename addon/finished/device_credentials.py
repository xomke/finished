"""Durable local storage for the secret that proves a Blender device pairing."""

import json
import os
import tempfile

if __package__:
    from . import state_paths
else:  # Supports the project's standalone pure-Python module tests.
    import importlib.util
    from pathlib import Path

    _state_paths_spec = importlib.util.spec_from_file_location(
        "finished_state_paths", Path(__file__).with_name("state_paths.py")
    )
    state_paths = importlib.util.module_from_spec(_state_paths_spec)
    _state_paths_spec.loader.exec_module(state_paths)

FILE_NAME = "finished-device-credentials.json"
TOKEN_KEY = "device_token"


def credentials_path(bpy_module=None):
    if bpy_module is None:
        import bpy as bpy_module

    directory = bpy_module.utils.user_resource(
        "CONFIG", path=state_paths.blender_config_subdirectory(), create=True
    )
    if not directory:
        return None
    path = os.path.join(directory, FILE_NAME)
    _migrate_legacy_development_credentials(bpy_module, path)
    return path


def _migrate_legacy_development_credentials(bpy_module, destination):
    if state_paths.local_state_namespace() != state_paths.DEVELOPMENT_NAMESPACE or os.path.exists(destination):
        return
    legacy_directory = bpy_module.utils.user_resource("CONFIG", path="finished", create=False)
    if not legacy_directory:
        return
    try:
        os.replace(os.path.join(legacy_directory, FILE_NAME), destination)
    except OSError:
        pass


def load_device_token(path):
    if not path:
        return ""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, TypeError):
        return ""
    token = data.get(TOKEN_KEY, "") if isinstance(data, dict) else ""
    return token.strip() if isinstance(token, str) else ""


def save_device_token(token, path):
    token = (token or "").strip()
    if not token or not path:
        return False

    directory = os.path.dirname(path)
    temporary_path = None
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        descriptor, temporary_path = tempfile.mkstemp(prefix=".finished-", dir=directory)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.chmod(temporary_path, 0o600)
            json.dump({TOKEN_KEY: token}, handle, separators=(",", ":"))
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
        return True
    except OSError:
        return False
    finally:
        if temporary_path and os.path.exists(temporary_path):
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


def clear_device_token(path):
    if not path:
        return False
    try:
        os.unlink(path)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def restore_or_persist_device_token(preferences, path):
    token = getattr(preferences, "device_token", "").strip()
    if token:
        save_device_token(token, path)
    else:
        token = load_device_token(path)
        if token:
            preferences.device_token = token

    if token:
        # This flag is internal, not a user preference. A preserved valid credential
        # must resume normal server event delivery after the extension is re-enabled.
        preferences.enable_server_transport = True
    return token
