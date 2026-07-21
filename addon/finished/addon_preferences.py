def current_preferences():
    """Return Preferences for this extension package only, never a sibling profile."""

    try:
        import bpy

        addons = bpy.context.preferences.addons
        addon = addons.get(__package__)
        if addon is not None:
            return addon.preferences

        extension_id = __package__.rsplit(".", 1)[-1]
        matches = []
        for key, value in addons.items():
            if key.rsplit(".", 1)[-1] == extension_id:
                matches.append(value.preferences)
        if len(matches) == 1:
            return matches[0]
    except Exception:
        return None

    return None


def clear_matching_legacy_preferences(active_preferences, device_token, bpy_module):
    """Clear owner prerelease Preferences after their credential moved to the Dev profile.

    The token match makes this a migration of one known owner installation, not a way for Dev to
    take credentials from an unrelated public installation.
    """

    token = (device_token or "").strip()
    if not token:
        return False
    try:
        addons = bpy_module.context.preferences.addons
        matches = [
            addon.preferences
            for key, addon in addons.items()
            if key.rsplit(".", 1)[-1] == "finished"
        ]
    except Exception:
        return False
    if len(matches) != 1:
        return False

    legacy_preferences = matches[0]
    if legacy_preferences is active_preferences or getattr(legacy_preferences, "device_token", "") != token:
        return False

    for name, value in _LEGACY_RESET_VALUES.items():
        if hasattr(legacy_preferences, name):
            setattr(legacy_preferences, name, value)
    return True


_LEGACY_RESET_VALUES = {
    "device_token": "",
    "pairing_code": "",
    "enable_server_transport": False,
    "device_verification_status": "unknown",
    "device_verified_at": 0.0,
    "device_connection_status": "unknown",
    "device_connection_last_success_at": 0.0,
    "device_connection_last_check_at": 0.0,
    "device_connection_last_failure_at": 0.0,
    "device_connection_last_error": "",
    "device_connection_failure_count": 0,
    "device_connection_next_check_after": 0.0,
    "update_last_attempt_at": 0.0,
    "update_last_success_at": 0.0,
    "update_check_state": "not_checked",
    "update_last_error": "",
    "update_latest_version": "",
    "update_latest_channel": "",
    "update_latest_status": "",
    "update_latest_min_blender_version": "",
    "update_latest_download_url": "",
    "update_latest_sha256": "",
    "update_latest_notes_ru": "",
    "update_latest_notes_en": "",
    "show_release_notes": False,
    "update_notified_version": "",
    "update_download_state": "not_downloaded",
    "update_prepared_package_path": "",
    "update_download_error": "",
}
