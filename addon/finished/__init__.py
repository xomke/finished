import bpy

from .version import ADDON_VERSION


bl_info = {
    "name": "Finished?",
    "author": "Finished? contributors",
    "version": ADDON_VERSION,
    "blender": (5, 0, 0),
    "location": "Render > Render Animation with Finished?",
    "description": "Monitor Blender animation sequence renders and send status updates.",
    "category": "Render",
}

from . import local_log
from . import addon_preferences
from . import device_connection_monitor
from . import device_credentials
from . import menu
from . import operators
from . import preferences
from . import render_handlers
from . import update_monitor
from . import update_download_monitor
from . import update_install_handoff
from . import state_paths


MODULES = (
    preferences,
    operators,
    render_handlers,
    update_monitor,
    update_download_monitor,
    device_connection_monitor,
    menu,
)


def register():
    for module in MODULES:
        module.register()
    preferences_value = addon_preferences.current_preferences()
    if preferences_value is not None:
        device_token = device_credentials.restore_or_persist_device_token(
            preferences_value,
            device_credentials.credentials_path(bpy),
        )
        if state_paths.local_state_namespace() == state_paths.DEVELOPMENT_NAMESPACE:
            addon_preferences.clear_matching_legacy_preferences(preferences_value, device_token, bpy)
        current_version = ".".join(str(part) for part in ADDON_VERSION)
        result = update_install_handoff.reconcile_after_start(preferences_value, current_version)
        if result == "" and getattr(preferences_value, "update_download_state", "") == "install_pending_exit":
            update_install_handoff.schedule_reconciliation(preferences_value, current_version, bpy)
    local_log.info("Finished? add-on registered.")


def unregister():
    update_install_handoff.cancel_reconciliation()
    for module in reversed(MODULES):
        module.unregister()
    local_log.info("Finished? add-on unregistered.")
