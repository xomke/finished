import bpy
import time

from . import api_client
from . import device_connection_monitor
from . import device_connection_state
from .device_name import local_device_name
from . import local_log
from . import render_handlers
from . import support_report
from . import update_monitor
from . import update_presentation
from . import update_download_monitor
from . import update_notification_overlay
from . import device_credentials
from .onboarding import (
    CHECKING_RENDER_LINES,
    CHECKING_RENDER_MESSAGE,
    is_device_token_configured,
    render_connection_status,
)
from .protocol import API_PROTOCOL_VERSION
from .preflight import run_preflight
from .render_session import RenderSession
from .version import ADDON_VERSION_STRING


TELEGRAM_BOT_URL = "tg://resolve?domain=render_finished_bot"


def _show_render_connection_popup(context, lines):
    if bpy.app.background:
        return

    def draw(self, _context):
        for line in lines:
            self.layout.label(text=line)

    context.window_manager.popup_menu(
        draw,
        title="Connect Finished? to Telegram",
        icon="INFO",
    )


def _cancel_unready_render(operator, context, *, show_popup=False):
    addon = context.preferences.addons.get(__package__)
    if addon is None:
        # Direct headless registration used by development scripts does not
        # create a Blender add-on preferences entry.
        return False

    preferences = addon.preferences
    now = time.time()
    device_token_configured = is_device_token_configured(preferences.device_token)
    if device_token_configured:
        state = device_connection_state.from_preferences(preferences)
        if device_connection_state.is_fresh_valid(state, now):
            local_log.info(
                "Finished? using fresh Telegram connection state before render: "
                "operation=device_check_before_render status=valid"
            )
            return False

        cached_device_check = _cached_blocking_device_check(preferences)
        if cached_device_check is not None:
            return _cancel_from_device_check(
                operator,
                context,
                preferences,
                cached_device_check,
                now=now,
                show_popup=show_popup,
            )

        if state.status == device_connection_state.STATUS_CHECKING:
            return _cancel_with_message(
                operator,
                context,
                CHECKING_RENDER_MESSAGE,
                CHECKING_RENDER_LINES,
                show_popup=show_popup,
            )

        local_log.info(
            "Finished? fast-checking Telegram connection before render: "
            "operation=device_check_before_render"
        )
        device_check = _check_device_before_render(
            preferences.api_base_url,
            preferences.device_token,
        )
        now = time.time()
        if _is_unverified_device_check(device_check):
            _cache_device_verification(preferences, device_check, now=now)
            if _has_prior_valid_connection(state):
                local_log.warning(
                    "Finished? allowing render with stale valid Telegram connection "
                    "after a short network check failed: "
                    f"operation=device_check_before_render error={_api_error(device_check)}"
                )
                return False
            return _cancel_with_message(
                operator,
                context,
                CHECKING_RENDER_MESSAGE,
                CHECKING_RENDER_LINES,
                show_popup=show_popup,
            )

        return _cancel_from_device_check(
            operator,
            context,
            preferences,
            device_check,
            now=now,
            show_popup=show_popup,
        )

    return _cancel_from_device_check(
        operator,
        context,
        preferences,
        api_client.ApiResult(ok=False),
        now=now,
        show_popup=show_popup,
    )


def _cancel_from_device_check(
    operator,
    context,
    preferences,
    device_check,
    *,
    now,
    show_popup,
):
    status = render_connection_status(preferences.device_token, device_check)
    _cache_device_verification(preferences, device_check, now=now)
    if status["ok"]:
        return False

    if status["clear_token"]:
        preferences.device_token = ""
        device_credentials.clear_device_token(device_credentials.credentials_path())
        preferences.enable_server_transport = False
        device_connection_monitor.stop()

    return _cancel_with_message(
        operator,
        context,
        status["message"],
        status["lines"],
        show_popup=show_popup,
    )


def _cancel_with_message(operator, context, message, lines, *, show_popup=False):
    local_log.warning(message)
    operator.report({"WARNING"}, message)
    if show_popup:
        _show_render_connection_popup(context, lines)
    return True


def _start_unmonitored_animation_render():
    """Run Blender's normal animation command without creating a Finished? session."""
    if bpy.app.background:
        return bpy.ops.render.render(animation=True)
    return bpy.ops.render.render("INVOKE_DEFAULT", animation=True)


class FINISHED_OT_render_animation(bpy.types.Operator):
    bl_idname = "finished.render_animation"
    bl_label = "Render Animation with Finished?"
    bl_description = "Start a Finished? monitored animation render"
    bl_options = {"REGISTER"}

    dry_run: bpy.props.BoolProperty(
        name="Dry Run",
        description="Prepare the Finished? session without starting Blender rendering",
        default=False,
        options={"HIDDEN"},
    )

    def invoke(self, context, _event):
        return self.execute(context)

    def execute(self, context):
        # Let Blender show its own missing-camera warning.  A session would otherwise
        # turn this local setup issue into a misleading Telegram render failure.
        if context.scene.camera is None:
            return _start_unmonitored_animation_render()

        if _cancel_unready_render(self, context, show_popup=True):
            return {"CANCELLED"}

        result = run_preflight(context.scene)

        for warning in result.warnings:
            local_log.warning(warning)

        if not result.ok:
            message = result.summary()
            local_log.error(message)
            self.report({"ERROR"}, message)
            return {"CANCELLED"}

        message = result.summary()
        local_log.info(message)
        session = RenderSession.from_scene(
            context.scene,
            result,
            project_name=bpy.path.basename(bpy.data.filepath) if bpy.data.filepath else "",
        ).start()
        render_handlers.prepare_session(session)

        if self.dry_run:
            self.report({"INFO"}, message)
            return {"FINISHED"}

        try:
            if bpy.app.background:
                render_result = bpy.ops.render.render(animation=True)
            else:
                render_result = bpy.ops.render.render("INVOKE_DEFAULT", animation=True)
        except Exception as exc:
            render_handlers.fail_active_session("Render failed before completion")
            self.report({"ERROR"}, f"Finished? render failed: {exc}")
            return {"CANCELLED"}

        if "CANCELLED" in render_result:
            self.report({"WARNING"}, "Finished? render was cancelled.")
            return {"CANCELLED"}

        self.report({"INFO"}, message)
        return {"FINISHED"}


class FINISHED_OT_check_settings(bpy.types.Operator):
    bl_idname = "finished.check_settings"
    bl_label = "Check Finished? Render Settings"
    bl_description = "Run Finished? preflight checks without starting a render"
    bl_options = {"REGISTER"}

    def execute(self, context):
        result = run_preflight(context.scene)

        for warning in result.warnings:
            local_log.warning(warning)

        if not result.ok:
            message = result.summary()
            local_log.error(message)
            self.report({"ERROR"}, message)
            return {"CANCELLED"}

        message = result.summary()
        local_log.info(message)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class FINISHED_OT_test_connection(bpy.types.Operator):
    bl_idname = "finished.test_connection"
    bl_label = "Test Finished? Connection"
    bl_description = "Check the Finished? API URL and Device Token"
    bl_options = {"REGISTER"}

    def execute(self, context):
        preferences = context.preferences.addons[__package__].preferences
        health = _check_health_with_retry(
            preferences.api_base_url,
            operation_label="health_check",
        )

        if not health.ok:
            message = f"Finished? API health check failed: operation=health_check error={_api_error(health)}"
            local_log.warning(message)
            self.report({"WARNING"}, message)
            return {"CANCELLED"}

        if not preferences.device_token:
            message = "Finished? API is available, but Device Token is missing."
            _mark_connection_unpaired(preferences)
            local_log.warning(message)
            self.report({"WARNING"}, message)
            return {"CANCELLED"}

        device = _check_device_with_retry(
            preferences.api_base_url,
            preferences.device_token,
            operation_label="device_check",
        )
        if device.ok:
            data = device.data or {}
            device_name = data.get("device_name", "device")
            if not data.get("telegram_chat_id"):
                _mark_connection_unpaired(preferences)
                message = (
                    f"Finished? API connection is available for {device_name}, "
                    "but this Device Token is not paired with Telegram. "
                    "Use Pair Device with a Telegram pairing code."
                )
                local_log.warning(message)
                self.report({"WARNING"}, message)
                return {"CANCELLED"}

            _mark_connection_valid(preferences)
            message = f"Finished? API connection is available for {device_name}."
            local_log.info(message)
            self.report({"INFO"}, message)
            return {"FINISHED"}

        _cache_device_verification(
            preferences,
            device,
        )
        if device.status_code == 401:
            preferences.enable_server_transport = False
            device_connection_monitor.stop()
        message = f"Finished? Device Token check failed: operation=device_check error={_api_error(device)}"
        local_log.warning(message)
        self.report({"WARNING"}, message)
        return {"CANCELLED"}


class FINISHED_OT_create_dev_device_token(bpy.types.Operator):
    bl_idname = "finished.create_dev_device_token"
    bl_label = "Create Local Device Token"
    bl_description = "Create a local development device token. This will be replaced by Telegram pairing later"
    bl_options = {"REGISTER"}

    def execute(self, context):
        preferences = context.preferences.addons[__package__].preferences
        result = api_client.dev_register_device(
            preferences.api_base_url,
            device_name=local_device_name(),
        )

        if not result.ok:
            message = f"Finished? local device registration failed: {result.error or result.status_code}"
            local_log.warning(message)
            self.report({"WARNING"}, message)
            return {"CANCELLED"}

        token = (result.data or {}).get("device_token", "")
        if not token:
            message = "Finished? local device registration did not return a token."
            local_log.warning(message)
            self.report({"WARNING"}, message)
            return {"CANCELLED"}

        preferences.device_token = token
        device_credentials.save_device_token(token, device_credentials.credentials_path())
        preferences.enable_server_transport = True
        device_connection_monitor.schedule_soon()
        message = "Finished? local Device Token created. Server events enabled."
        local_log.info(message)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class FINISHED_OT_create_dev_pairing_code(bpy.types.Operator):
    bl_idname = "finished.create_dev_pairing_code"
    bl_label = "Create Local Pairing Code"
    bl_description = "Create a local development pairing code. Later this code will come from Telegram"
    bl_options = {"REGISTER"}

    def execute(self, context):
        preferences = context.preferences.addons[__package__].preferences
        result = api_client.dev_create_pairing_code(preferences.api_base_url)

        if not result.ok:
            message = f"Finished? local pairing code failed: {result.error or result.status_code}"
            local_log.warning(message)
            self.report({"WARNING"}, message)
            return {"CANCELLED"}

        code = (result.data or {}).get("pairing_code", "")
        if not code:
            message = "Finished? local pairing did not return a code."
            local_log.warning(message)
            self.report({"WARNING"}, message)
            return {"CANCELLED"}

        preferences.pairing_code = code
        message = f"Finished? local Pairing Code created: {code}"
        local_log.info(message)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class FINISHED_OT_complete_pairing(bpy.types.Operator):
    bl_idname = "finished.complete_pairing"
    bl_label = "Pair Device"
    bl_description = "Exchange a pairing code for a Device Token"
    bl_options = {"REGISTER"}

    def execute(self, context):
        preferences = context.preferences.addons[__package__].preferences
        pairing_code = _normalize_pairing_code(preferences.pairing_code)
        if not pairing_code:
            message = "Finished? Pairing Code is missing."
            local_log.warning(message)
            self.report({"WARNING"}, message)
            return {"CANCELLED"}

        local_log.info(
            "Finished? pairing request prepared: "
            f"api_base_url={preferences.api_base_url} code_digits={len(pairing_code)}"
        )
        result = _complete_pairing_with_retry(
            preferences.api_base_url,
            pairing_code,
        )

        if not result.ok:
            message = f"Finished? pairing failed: {_pairing_error_message(result)}"
            local_log.warning(message)
            self.report({"WARNING"}, message)
            return {"CANCELLED"}

        token = (result.data or {}).get("device_token", "")
        if not token:
            message = "Finished? pairing did not return a Device Token."
            local_log.warning(message)
            self.report({"WARNING"}, message)
            return {"CANCELLED"}

        preferences.device_token = token
        device_credentials.save_device_token(token, device_credentials.credentials_path())
        preferences.pairing_code = ""
        preferences.enable_server_transport = True
        _mark_connection_valid(preferences)
        message = "Finished? device paired. Server events enabled."
        local_log.info(message)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class FINISHED_OT_disconnect_telegram(bpy.types.Operator):
    bl_idname = "finished.disconnect_telegram"
    bl_label = "Disconnect Telegram"
    bl_description = "Disconnect this Blender from Finished? Telegram notifications"
    bl_options = {"REGISTER"}

    def execute(self, context):
        preferences = context.preferences.addons[__package__].preferences
        token = preferences.device_token.strip()
        if not token:
            message = "Finished? Telegram is already disconnected."
            local_log.info(message)
            self.report({"INFO"}, message)
            return {"FINISHED"}

        result = api_client.disconnect_device(
            preferences.api_base_url,
            token,
        )

        if not result.ok:
            if result.status_code == 401:
                preferences.device_token = ""
                device_credentials.clear_device_token(device_credentials.credentials_path())
                preferences.enable_server_transport = False
                _mark_connection_unpaired(preferences)
                message = "Finished? token is no longer valid. Telegram was disconnected locally."
                local_log.warning(message)
                self.report({"WARNING"}, message)
                return {"FINISHED"}

            message = f"Finished? Telegram disconnect failed: {result.error or result.status_code}"
            local_log.warning(message)
            self.report({"WARNING"}, message)
            return {"CANCELLED"}

        preferences.device_token = ""
        device_credentials.clear_device_token(device_credentials.credentials_path())
        preferences.pairing_code = ""
        preferences.enable_server_transport = False
        _mark_connection_unpaired(preferences)
        message = "Finished? Telegram disconnected."
        local_log.info(message)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class FINISHED_OT_clear_log(bpy.types.Operator):
    bl_idname = "finished.clear_log"
    bl_label = "Clear Finished? Log"
    bl_description = "Clear the local Finished? diagnostic log"
    bl_options = {"REGISTER"}

    def execute(self, _context):
        local_log.clear()
        self.report({"INFO"}, "Finished? local log cleared.")
        return {"FINISHED"}


class FINISHED_OT_open_telegram_bot(bpy.types.Operator):
    bl_idname = "finished.open_telegram_bot"
    bl_label = "Open Finished? Bot"
    bl_description = "Open the Finished? bot in Telegram"
    bl_options = {"INTERNAL"}

    def execute(self, _context):
        bpy.ops.wm.url_open(url=TELEGRAM_BOT_URL)
        return {"FINISHED"}


class FINISHED_OT_create_support_report(bpy.types.Operator):
    bl_idname = "finished.create_support_report"
    bl_label = "Create Support Report"
    bl_description = "Save a Finished? support report without project files or credentials"
    bl_options = {"REGISTER"}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")

    def invoke(self, context, _event):
        self.filepath = support_report.default_filename(ADDON_VERSION_STRING)
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        preferences = context.preferences.addons[__package__].preferences
        blender_version = ".".join(str(part) for part in getattr(bpy.app, "version", ())[:3])
        try:
            destination = support_report.create_report(
                self.filepath,
                preferences,
                addon_version=ADDON_VERSION_STRING,
                blender_version=blender_version,
            )
        except OSError as exc:
            local_log.warning(f"Support report creation failed: error={type(exc).__name__}")
            self.report({"ERROR"}, "Finished? could not save the support report.")
            return {"CANCELLED"}

        local_log.info("Finished? support report created.")
        self.report({"INFO"}, f"Finished? support report saved: {destination.name}")
        return {"FINISHED"}


class FINISHED_OT_check_updates(bpy.types.Operator):
    bl_idname = "finished.check_updates"
    bl_label = "Check Finished? Updates"
    bl_description = "Check for a Finished? add-on update in the background"
    bl_options = {"INTERNAL"}

    def execute(self, _context):
        if update_monitor.request_manual_check():
            self.report({"INFO"}, update_presentation.text("checking", update_presentation.current_language()))
        else:
            self.report(
                {"INFO"}, update_presentation.text("already_checking", update_presentation.current_language())
            )
        return {"FINISHED"}


class FINISHED_OT_show_update_available(bpy.types.Operator):
    bl_idname = "finished.show_update_available"
    bl_label = "Finished? Update Available"
    bl_options = {"INTERNAL"}

    version: bpy.props.StringProperty(options={"HIDDEN"})

    def invoke(self, context, _event):
        if update_notification_overlay.show(context, self.version):
            context.window_manager.modal_handler_add(self)
            return {"RUNNING_MODAL"}
        return {"CANCELLED"}

    def execute(self, context):
        return self.invoke(context, None)

    def modal(self, context, event):
        if update_notification_overlay.close_from_event(context, event):
            return {"FINISHED"}
        update_notification_overlay.update_hover(context, event)
        if not update_notification_overlay.is_visible():
            return {"FINISHED"}
        return {"PASS_THROUGH"}


class FINISHED_OT_download_update(bpy.types.Operator):
    bl_idname = "finished.download_update"
    bl_label = "Download Finished? Update"
    bl_description = "Download, verify, and install the latest Finished? update"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        language = update_presentation.current_language()
        if render_handlers.current_session() is not None:
            self.report({"WARNING"}, update_presentation.text("download_render_active", language))
            return {"CANCELLED"}
        preferences = context.preferences.addons[__package__].preferences
        if update_download_monitor.request_download(preferences):
            self.report({"INFO"}, update_presentation.text("download_queued", language))
        else:
            self.report({"INFO"}, update_presentation.text("download_busy", language))
        return {"FINISHED"}


CLASSES = (
    FINISHED_OT_render_animation,
    FINISHED_OT_check_settings,
    FINISHED_OT_test_connection,
    FINISHED_OT_create_dev_device_token,
    FINISHED_OT_create_dev_pairing_code,
    FINISHED_OT_complete_pairing,
    FINISHED_OT_disconnect_telegram,
    FINISHED_OT_clear_log,
    FINISHED_OT_open_telegram_bot,
    FINISHED_OT_create_support_report,
    FINISHED_OT_check_updates,
    FINISHED_OT_show_update_available,
    FINISHED_OT_download_update,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    update_notification_overlay.close()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


def _check_health_with_retry(api_base_url, operation_label):
    return _with_connection_retry(
        operation_label,
        lambda: api_client.check_health(
            api_base_url,
            timeout=api_client.CONNECTION_CHECK_TIMEOUT_SECONDS,
        ),
    )


def _complete_pairing_with_retry(api_base_url, pairing_code):
    device_name = local_device_name()
    blender_version = ".".join(str(part) for part in getattr(bpy.app, "version", ())[:3])
    result = api_client.complete_pairing(
        api_base_url,
        pairing_code,
        device_name=device_name,
        blender_version=blender_version,
        addon_version=ADDON_VERSION_STRING,
        api_version=API_PROTOCOL_VERSION,
        timeout=api_client.PAIRING_TIMEOUT_SECONDS,
    )
    if result.ok or not _is_tls_handshake_timeout(result):
        return result

    local_log.warning(
        "Finished? pairing retry after TLS handshake timeout: "
        f"delay={api_client.PAIRING_RETRY_DELAY_SECONDS:.1f}s"
    )
    time.sleep(api_client.PAIRING_RETRY_DELAY_SECONDS)
    return api_client.complete_pairing(
        api_base_url,
        pairing_code,
        device_name=device_name,
        blender_version=blender_version,
        addon_version=ADDON_VERSION_STRING,
        api_version=API_PROTOCOL_VERSION,
        timeout=api_client.PAIRING_TIMEOUT_SECONDS,
    )


def _is_tls_handshake_timeout(result):
    if result.status_code:
        return False
    return "handshake operation timed out" in result.error.lower()


def _check_device_with_retry(api_base_url, device_token, operation_label):
    return _with_connection_retry(
        operation_label,
        lambda: api_client.check_device(
            api_base_url,
            device_token,
            timeout=api_client.CONNECTION_CHECK_TIMEOUT_SECONDS,
        ),
    )


def _check_device_before_render(api_base_url, device_token):
    return api_client.check_device(
        api_base_url,
        device_token,
        timeout=api_client.RENDER_START_DEVICE_CHECK_TIMEOUT_SECONDS,
    )


def _with_connection_retry(operation_label, request_func):
    result = request_func()
    if result.ok or not _should_retry_connection_check(result):
        return result

    for delay_seconds in api_client.CONNECTION_CHECK_RETRY_DELAYS_SECONDS:
        local_log.warning(
            f"Finished? connection check retry: operation={operation_label} "
            f"delay={delay_seconds:.1f}s error={_api_error(result)}"
        )
        time.sleep(delay_seconds)
        result = request_func()
        if result.ok or not _should_retry_connection_check(result):
            return result

    return result


def _should_retry_connection_check(result):
    if result.status_code:
        return 500 <= result.status_code < 600

    error = result.error.lower()
    retry_markers = (
        "timed out",
        "timeout",
        "temporarily unavailable",
        "connection reset",
        "connection refused",
        "network is unreachable",
    )
    return any(marker in error for marker in retry_markers)


def _api_error(result):
    if result.error:
        return result.error
    if result.status_code:
        return f"HTTP {result.status_code}"
    return "unknown error"


def _normalize_pairing_code(value):
    return "".join(character for character in str(value or "") if character.isdigit())


def _pairing_error_message(result):
    messages = {
        "invalid_or_expired_pairing_code": (
            "Pairing code is invalid or expired. Request a new code in Telegram and try again."
        ),
        "invalid_pairing_code": "Pairing code is invalid. Request a new code in Telegram and try again.",
        "pairing_code_expired": "Pairing code expired. Request a new code in Telegram and try again.",
        "pairing_code_already_used": "Pairing code was already used. Request a new code in Telegram.",
        "pairing_code_revoked": "Pairing code was revoked. Request a new code in Telegram.",
        "device_limit_reached": "A device is already connected. Disconnect it in Telegram before pairing again.",
    }
    if result.error in messages:
        return messages[result.error]
    return _api_error(result)


def _cache_device_verification(preferences, device_check, now=None):
    state = device_connection_state.from_preferences(preferences)
    state = device_connection_state.apply_device_check_result(
        state,
        device_check,
        time.time() if now is None else now,
        device_token_configured=is_device_token_configured(preferences.device_token),
    )
    device_connection_state.write_preferences(preferences, state)


def _set_device_verification_cache(preferences, verification_status):
    now = time.time()
    state = device_connection_state.from_preferences(preferences)
    if verification_status == device_connection_state.STATUS_VALID:
        state = device_connection_state.mark_valid(state, now)
    elif verification_status == device_connection_state.STATUS_INVALID:
        state = device_connection_state.mark_invalid(state, now)
    elif verification_status == device_connection_state.STATUS_UNPAIRED:
        state = device_connection_state.mark_unpaired(state, now)
    elif verification_status == device_connection_state.STATUS_CHECKING:
        state = device_connection_state.mark_checking(state, now)
    elif verification_status == device_connection_state.STATUS_SERVER_UNREACHABLE:
        state = device_connection_state.mark_server_unreachable(state, now, "")
    else:
        state = device_connection_state.DeviceConnectionState(status=verification_status)
    device_connection_state.write_preferences(preferences, state)


def _mark_connection_valid(preferences):
    _set_device_verification_cache(preferences, device_connection_state.STATUS_VALID)
    device_connection_monitor.schedule_soon()


def _mark_connection_unpaired(preferences):
    _set_device_verification_cache(preferences, device_connection_state.STATUS_UNPAIRED)
    device_connection_monitor.stop()


def _cached_blocking_device_check(preferences):
    state = device_connection_state.from_preferences(preferences)
    if not device_connection_state.blocks_until_pairing(state):
        return None

    local_log.info(
        "Finished? using cached Telegram connection block before render: "
        f"operation=device_check_before_render status={state.status}"
    )
    if state.status == device_connection_state.STATUS_INVALID:
        return api_client.ApiResult(ok=False, status_code=401, error="Invalid device token")

    if state.status == device_connection_state.STATUS_UPDATE_REQUIRED:
        return api_client.ApiResult(
            ok=True,
            status_code=200,
            data={
                "telegram_chat_id": "connected",
                "update_required": True,
                "update_reason": state.last_error,
            },
        )

    return api_client.ApiResult(ok=True, status_code=200, data={"telegram_chat_id": None})


def _is_unverified_device_check(device_check):
    return not device_check.ok and device_check.status_code != 401


def _has_prior_valid_connection(state):
    return state.status == device_connection_state.STATUS_VALID and state.last_success_at > 0.0
