import bpy

from . import diagnostics
from .operators import FINISHED_OT_complete_pairing
from .operators import FINISHED_OT_create_support_report
from .operators import FINISHED_OT_disconnect_telegram
from .operators import FINISHED_OT_check_updates
from .operators import FINISHED_OT_download_update
from .operators import FINISHED_OT_open_telegram_bot
from . import update_presentation
from . import render_handlers
from .version import ADDON_VERSION_STRING


def _auto_check_updates_changed(preferences, _context):
    from . import update_monitor

    update_monitor.auto_check_setting_changed(preferences)


class FINISHED_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    api_base_url: bpy.props.StringProperty(
        name="API URL",
        description="Finished? API URL",
        default="https://api.finished.xomke.art",
    )

    device_token: bpy.props.StringProperty(
        name="Device Token",
        description="Device token received after Telegram pairing. Placeholder for now",
        default="",
        subtype="PASSWORD",
    )

    pairing_code: bpy.props.StringProperty(
        name="Pairing Code",
        description="Short-lived pairing code from Telegram. Local development can create one here for now",
        default="",
    )

    enable_server_transport: bpy.props.BoolProperty(
        name="Send Events to Finished? API",
        description="Send render events to the configured Finished? API. Local mock notifications remain enabled",
        default=False,
    )

    device_verification_status: bpy.props.StringProperty(
        name="Device Verification Status",
        description="Internal cached result of the last Finished? device verification",
        default="unknown",
        options={"HIDDEN"},
    )

    device_verified_at: bpy.props.FloatProperty(
        name="Device Verified At",
        description="Internal timestamp of the last Finished? device verification",
        default=0.0,
        options={"HIDDEN"},
    )

    device_connection_status: bpy.props.StringProperty(
        name="Device Connection Status",
        description="Internal Finished? device connection state",
        default="unknown",
        options={"HIDDEN"},
    )

    device_connection_last_success_at: bpy.props.FloatProperty(
        name="Device Connection Last Success At",
        description="Internal timestamp of the last successful Finished? device check",
        default=0.0,
        options={"HIDDEN"},
    )

    device_connection_last_check_at: bpy.props.FloatProperty(
        name="Device Connection Last Check At",
        description="Internal timestamp of the last Finished? device check",
        default=0.0,
        options={"HIDDEN"},
    )

    device_connection_last_failure_at: bpy.props.FloatProperty(
        name="Device Connection Last Failure At",
        description="Internal timestamp of the last failed Finished? device check",
        default=0.0,
        options={"HIDDEN"},
    )

    device_connection_last_error: bpy.props.StringProperty(
        name="Device Connection Last Error",
        description="Internal last Finished? device connection error",
        default="",
        options={"HIDDEN"},
    )

    device_connection_failure_count: bpy.props.IntProperty(
        name="Device Connection Failure Count",
        description="Internal count of recent Finished? device connection failures",
        default=0,
        min=0,
        options={"HIDDEN"},
    )

    device_connection_next_check_after: bpy.props.FloatProperty(
        name="Device Connection Next Check After",
        description="Internal timestamp before which background device checks should wait",
        default=0.0,
        options={"HIDDEN"},
    )

    update_last_attempt_at: bpy.props.FloatProperty(
        name="Update Last Attempt At",
        description="Internal timestamp of the latest automatic update check attempt",
        default=0.0,
        options={"HIDDEN"},
    )

    auto_check_updates: bpy.props.BoolProperty(
        name="Automatically check for updates",
        description="Check once after Blender starts, then every hour. Checks wait for the current render to finish.",
        default=True,
        update=_auto_check_updates_changed,
    )

    update_last_success_at: bpy.props.FloatProperty(
        name="Update Last Success At",
        description="Internal timestamp of the latest successful update check",
        default=0.0,
        options={"HIDDEN"},
    )

    update_check_state: bpy.props.StringProperty(
        name="Update Check State",
        description="Internal update check result state",
        default="not_checked",
        options={"HIDDEN"},
    )

    update_last_error: bpy.props.StringProperty(
        name="Update Last Error",
        description="Internal safe code for the latest update check failure",
        default="",
        options={"HIDDEN"},
    )

    update_latest_version: bpy.props.StringProperty(
        name="Latest Update Version",
        description="Internal latest validated add-on version",
        default="",
        options={"HIDDEN"},
    )

    update_latest_channel: bpy.props.StringProperty(
        name="Latest Update Channel",
        description="Internal latest validated update channel",
        default="",
        options={"HIDDEN"},
    )

    update_latest_status: bpy.props.StringProperty(
        name="Latest Update Status",
        description="Internal latest validated release status",
        default="",
        options={"HIDDEN"},
    )

    update_latest_min_blender_version: bpy.props.StringProperty(
        name="Latest Update Blender Minimum",
        description="Internal validated Blender minimum version",
        default="",
        options={"HIDDEN"},
    )

    update_latest_download_url: bpy.props.StringProperty(
        name="Latest Update Download URL",
        description="Internal validated update package URL",
        default="",
        options={"HIDDEN"},
    )

    update_latest_sha256: bpy.props.StringProperty(
        name="Latest Update SHA-256",
        description="Internal validated update package SHA-256",
        default="",
        options={"HIDDEN"},
    )

    update_latest_notes_ru: bpy.props.StringProperty(
        name="Latest Update Notes RU",
        description="Internal validated Russian release notes",
        default="",
        options={"HIDDEN"},
    )

    update_latest_notes_en: bpy.props.StringProperty(
        name="Latest Update Notes EN",
        description="Internal validated English release notes",
        default="",
        options={"HIDDEN"},
    )

    show_release_notes: bpy.props.BoolProperty(
        name="Release Notes",
        description="Show notes for the available update",
        default=False,
    )

    update_notified_version: bpy.props.StringProperty(
        name="Update Notified Version",
        description="Internal latest update version already shown in a Blender notification",
        default="",
        options={"HIDDEN"},
    )

    update_download_state: bpy.props.StringProperty(
        name="Update Download State",
        description="Internal state of the explicit update package download",
        default="not_downloaded",
        options={"HIDDEN"},
    )

    update_prepared_package_path: bpy.props.StringProperty(
        name="Prepared Update Package Path",
        description="Internal verified update package path; installation has not happened",
        default="",
        options={"HIDDEN"},
    )

    update_install_result_path: bpy.props.StringProperty(
        name="Update Install Result Path",
        description="Internal post-exit installer receipt path",
        default="",
        options={"HIDDEN"},
    )

    update_install_helper_pid: bpy.props.IntProperty(
        name="Update Install Helper PID",
        description="Internal PID for the post-exit installer helper",
        default=0,
        min=0,
        options={"HIDDEN"},
    )

    update_download_error: bpy.props.StringProperty(
        name="Update Download Error",
        description="Internal safe code for the latest update package download failure",
        default="",
        options={"HIDDEN"},
    )

    show_help_support: bpy.props.BoolProperty(
        name="Help & Support",
        description="Show Finished? support options",
        default=False,
    )

    def draw(self, _context):
        layout = self.layout
        layout.use_property_split = False
        layout.use_property_decorate = False

        connection_label = diagnostics.public_connection_status_label(self)

        box = layout.box()
        box.label(text="Telegram Connection", icon="URL")
        if connection_label == "Connected":
            box.label(text="Connected to Telegram", icon="CHECKMARK")
            box.label(text="Render notifications will be sent to this Telegram account.")
            action = box.row()
            action.alignment = "LEFT"
            action.operator(FINISHED_OT_disconnect_telegram.bl_idname, text="Disconnect")
        elif connection_label == "Checking connection":
            box.label(text="Checking Telegram connection…", icon="TIME")
            box.label(text="Your saved connection is being verified.")
        else:
            if connection_label == "Reconnect needed":
                box.label(text="Reconnect needed", icon="ERROR")
                box.label(text="Reconnect this device with a new pairing code.")
            else:
                box.label(text="Not connected", icon="ERROR")

            box.separator()
            box.label(text="1. Open the Finished? bot in Telegram.")
            action = box.row()
            action.alignment = "LEFT"
            action.operator(FINISHED_OT_open_telegram_bot.bl_idname, text="Open Finished? Bot", icon="URL")
            box.label(text="2. Tap Connect device, then enter the code below.")
            box.prop(self, "pairing_code")
            action = box.row()
            action.alignment = "LEFT"
            action.operator(FINISHED_OT_complete_pairing.bl_idname, text="Connect Telegram", icon="CHECKMARK")

        layout.separator()
        update_box = layout.box()
        language = update_presentation.current_language()
        update_box.label(text=update_presentation.text("updates", language), icon="FILE_REFRESH")
        update_box.prop(self, "auto_check_updates", text=update_presentation.text("automatic_check", language))
        update_box.label(text=update_presentation.text("automatic_check_on", language), icon="INFO")

        actions = update_box.row()
        actions.scale_y = 1.5
        check = actions.column()
        check.enabled = self.update_check_state != "checking"
        check.operator(
            FINISHED_OT_check_updates.bl_idname,
            text=update_presentation.text("check_now", language),
            icon="FILE_REFRESH",
        )
        actions.separator(factor=0.35)
        download = actions.column()
        can_download = (
            self.update_check_state == "update_available"
            and self.update_download_state not in {"queued", "downloading", "install_pending_exit"}
            and render_handlers.current_session() is None
        )
        download.enabled = can_download
        download.operator(
            FINISHED_OT_download_update.bl_idname,
            text=update_presentation.text("download", language),
            icon="IMPORT",
        )

        update_box.label(text=update_presentation.last_check_text(self, language))
        update_box.label(text=update_presentation.text("current_version", language, version=ADDON_VERSION_STRING))

        if self.update_check_state == "checking":
            update_box.label(text=update_presentation.state_text(self, language), icon="FILE_REFRESH")
        elif self.update_check_state == "check_failed":
            update_box.label(text=update_presentation.state_text(self, language), icon="ERROR")
        elif self.update_check_state == "up_to_date":
            update_box.label(text=update_presentation.state_text(self, language), icon="CHECKMARK")
        elif self.update_check_state == "update_available" and self.update_latest_version:
            update_box.label(text=update_presentation.state_text(self, language), icon="IMPORT")
            notes = update_presentation.release_notes(self, language)
            if notes:
                notes_row = update_box.row()
                notes_row.prop(
                    self,
                    "show_release_notes",
                    text=update_presentation.text("notes", language),
                    icon="TRIA_DOWN" if self.show_release_notes else "TRIA_RIGHT",
                    emboss=False,
                )
                if self.show_release_notes:
                    notes_box = update_box.box()
                    for line in notes.splitlines():
                        notes_box.label(text=line)
            if self.update_download_state == "install_pending_exit":
                restart_box = update_box.box()
                restart_box.alert = True
                restart_box.label(text=update_presentation.text("restart_to_update", language), icon="ERROR")
            elif self.update_download_state == "downloading" or self.update_download_state == "queued":
                update_box.label(text=update_presentation.text("downloading", language))
            elif self.update_download_state == "download_failed":
                update_box.label(text=update_presentation.text("download_failed", language), icon="ERROR")

        layout.separator()
        help_box = layout.box()
        icon = "TRIA_DOWN" if self.show_help_support else "TRIA_RIGHT"
        help_box.prop(
            self,
            "show_help_support",
            text="Help & Support",
            icon=icon,
            emboss=False,
        )
        if self.show_help_support:
            help_box.separator()
            help_box.label(text="Create a report if something does not work as expected.")
            help_box.label(text="It includes safe diagnostics and recent render history.")
            action = help_box.row()
            action.alignment = "LEFT"
            action.operator(FINISHED_OT_create_support_report.bl_idname, text="Create Support Report", icon="FILE_TEXT")


CLASSES = (
    FINISHED_AddonPreferences,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
