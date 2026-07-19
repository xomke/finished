UNPAIRED_RENDER_MESSAGE = (
    "Finished? is not connected to Telegram yet. "
    "Open the Finished? bot in Telegram, press Connect device, "
    "then enter the pairing code in the add-on preferences."
)

INVALID_TOKEN_RENDER_MESSAGE = (
    "Finished? Telegram connection is no longer valid. "
    "Open the Finished? bot in Telegram, press Connect device, "
    "then enter a new pairing code in the add-on preferences."
)

UNVERIFIED_RENDER_MESSAGE = (
    "Finished? cannot verify the Telegram connection right now. "
    "Check your internet connection or API URL, then try again."
)

CHECKING_RENDER_MESSAGE = (
    "Finished? is checking Telegram connection. Try again in a moment."
)

UNPAIRED_TOKEN_RENDER_MESSAGE = (
    "Finished? has a Device Token, but it is not connected to Telegram. "
    "Open the Finished? bot in Telegram and connect Blender again."
)


UNPAIRED_RENDER_LINES = (
    "Finished? is not connected to Telegram yet.",
    "Open the Finished? bot in Telegram.",
    "Press Connect device.",
    "Enter the pairing code in the Finished? add-on preferences.",
)

INVALID_TOKEN_RENDER_LINES = (
    "Finished? Telegram connection is no longer valid.",
    "Open the Finished? bot in Telegram.",
    "Press Connect device.",
    "Enter a new pairing code in the add-on preferences.",
)

UNVERIFIED_RENDER_LINES = (
    "Finished? cannot verify the Telegram connection right now.",
    "Check your internet connection or API URL.",
    "Then try starting the render again.",
)

CHECKING_RENDER_LINES = (
    "Finished? is checking Telegram connection.",
    "Try starting the render again in a moment.",
)

UNPAIRED_TOKEN_RENDER_LINES = (
    "Finished? has a Device Token,",
    "but it is not connected to Telegram.",
    "Connect Blender through the Finished? bot again.",
)


def is_device_token_configured(device_token):
    return bool((device_token or "").strip())


def render_connection_status(device_token, device_check_result):
    if not is_device_token_configured(device_token):
        return {
            "ok": False,
            "clear_token": False,
            "message": UNPAIRED_RENDER_MESSAGE,
            "lines": UNPAIRED_RENDER_LINES,
        }

    if device_check_result.ok:
        data = device_check_result.data or {}
        if data.get("telegram_chat_id"):
            return {
                "ok": True,
                "clear_token": False,
                "message": "",
                "lines": (),
            }

        return {
            "ok": False,
            "clear_token": True,
            "message": UNPAIRED_TOKEN_RENDER_MESSAGE,
            "lines": UNPAIRED_TOKEN_RENDER_LINES,
        }

    if device_check_result.status_code == 401:
        return {
            "ok": False,
            "clear_token": True,
            "message": INVALID_TOKEN_RENDER_MESSAGE,
            "lines": INVALID_TOKEN_RENDER_LINES,
        }

    return {
        "ok": False,
        "clear_token": False,
        "message": UNVERIFIED_RENDER_MESSAGE,
        "lines": UNVERIFIED_RENDER_LINES,
    }
