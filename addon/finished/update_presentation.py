"""Small RU/EN presentation helpers for add-on update state."""

from datetime import datetime

from .i18n import normalize_language
from . import update_checker


_COPY = {
    "en": {
        "updates": "Updates",
        "current_version": "Current version: {version}",
        "last_check": "Last update check: {timestamp}",
        "last_check_never": "Last update check: never",
        "automatic_check": "Auto-check for updates every hour",
        "automatic_check_on": "Checks once after Blender starts, then every hour. Checks wait for the current render to finish.",
        "automatic_check_off": "Automatic checks are off. You can still check manually.",
        "not_checked": "Ready to check for updates.",
        "checking": "Checking for updates…",
        "up_to_date": "You're up to date.",
        "update_available": "Update {version} is available",
        "check_failed": "Couldn't check for updates. Try again when you're online.",
        "check_now": "Check now for Finished? update",
        "checking": "Checking for updates…",
        "already_checking": "Finished? is already checking for updates.",
        "notice": "Finished? update {version} is available. Open add-on Preferences for details.",
        "notes": "Release notes",
        "download": "Download the latest version",
        "download_queued": "Finished? is downloading and verifying the update in the background.",
        "download_busy": "Finished? is already preparing an update package.",
        "download_render_active": "Finish or cancel the current Finished? render before downloading an update.",
        "downloading": "Downloading and verifying update…",
        "download_failed": "Couldn't prepare the update. Please try downloading it again.",
        "restart_to_update": "Restart Blender to finish updating",
    },
    "ru": {
        "updates": "Обновления",
        "current_version": "Текущая версия: {version}",
        "last_check": "Последняя проверка обновлений: {timestamp}",
        "last_check_never": "Обновления ещё не проверялись",
        "automatic_check": "Проверять обновления каждый час",
        "automatic_check_on": "Проверка выполняется после запуска Blender, затем каждый час. Во время рендера она ждёт его завершения.",
        "automatic_check_off": "Автоматическая проверка выключена. Проверить вручную можно в любой момент.",
        "not_checked": "Готово к проверке обновлений.",
        "checking": "Проверяем обновления…",
        "up_to_date": "Установлена актуальная версия.",
        "update_available": "Доступно обновление Finished? {version}",
        "check_failed": "Не удалось проверить обновления. Повторите попытку, когда появится интернет.",
        "check_now": "Проверить обновления Finished?",
        "checking": "Проверяем обновления…",
        "already_checking": "Finished? уже проверяет обновления.",
        "notice": "Доступно обновление Finished? {version}. Откройте Preferences аддона для деталей.",
        "notes": "Что нового",
        "download": "Скачать последнюю версию",
        "download_queued": "Finished? скачивает и проверяет обновление в фоне.",
        "download_busy": "Finished? уже подготавливает пакет обновления.",
        "download_render_active": "Завершите или отмените текущий рендер Finished? перед скачиванием обновления.",
        "downloading": "Скачивание и проверка обновления…",
        "download_failed": "Не удалось подготовить обновление. Попробуйте скачать его ещё раз.",
        "restart_to_update": "Перезапустите Blender, чтобы завершить обновление",
    },
}


def text(key, language="en", **values):
    return _COPY[normalize_language(language)][key].format(**values)


def current_language():
    try:
        import bpy

        return normalize_language(getattr(bpy.app.translations, "locale", ""))
    except (AttributeError, ImportError):
        return "en"


def state_text(preferences, language="en"):
    state = getattr(preferences, "update_check_state", update_checker.CHECK_NOT_CHECKED)
    version = getattr(preferences, "update_latest_version", "")
    if state == update_checker.CHECK_CHECKING:
        return text("checking", language)
    if state == update_checker.CHECK_UPDATE_AVAILABLE and version:
        return text("update_available", language, version=version)
    if state == update_checker.CHECK_UP_TO_DATE:
        return text("up_to_date", language)
    if state == update_checker.CHECK_FAILED:
        return text("check_failed", language)
    return text("not_checked", language)


def release_notes(preferences, language="en"):
    if normalize_language(language) == "ru":
        return getattr(preferences, "update_latest_notes_ru", "") or getattr(
            preferences, "update_latest_notes_en", ""
        )
    return getattr(preferences, "update_latest_notes_en", "") or getattr(
        preferences, "update_latest_notes_ru", ""
    )


def last_check_text(preferences, language="en"):
    try:
        timestamp = float(getattr(preferences, "update_last_attempt_at", 0.0))
    except (TypeError, ValueError):
        timestamp = 0.0
    if timestamp <= 0:
        return text("last_check_never", language)
    try:
        formatted = datetime.fromtimestamp(timestamp).astimezone().strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return text("last_check_never", language)
    return text("last_check", language, timestamp=formatted)
