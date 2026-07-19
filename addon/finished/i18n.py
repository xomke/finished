DEFAULT_LANGUAGE = "en"


TRANSLATIONS = {
    "en": {
        "render_started": "Render started",
        "render_status": "Render status",
        "render_running": "Render is running",
        "render_finished": "Render finished",
        "render_cancelled": "Render cancelled",
        "render_failed": "Render failed",
        "project": "Project",
        "scene": "Scene",
        "frames": "Frames",
        "total_frames": "Total frames",
        "format": "Format",
        "preview": "Preview",
        "available": "available",
        "unavailable": "unavailable",
        "frame": "Frame",
        "progress": "Progress",
        "frames_word": "frames",
        "average_frame_time": "Average frame time",
        "sec_per_frame": "sec/frame",
        "elapsed": "Elapsed",
        "eta": "ETA",
        "calculating": "calculating...",
        "total_time": "Total time",
        "output": "Output",
    },
    "ru": {
        "render_started": "Рендер начался",
        "render_status": "Статус рендера",
        "render_running": "Рендер идёт",
        "render_finished": "Рендер завершён",
        "render_cancelled": "Рендер отменён",
        "render_failed": "Ошибка рендера",
        "project": "Проект",
        "scene": "Сцена",
        "frames": "Кадры",
        "total_frames": "Всего кадров",
        "format": "Формат",
        "preview": "Превью",
        "available": "доступно",
        "unavailable": "недоступно",
        "frame": "Кадр",
        "progress": "Прогресс",
        "frames_word": "кадров",
        "average_frame_time": "Среднее время кадра",
        "sec_per_frame": "сек/кадр",
        "elapsed": "Прошло",
        "eta": "Осталось",
        "calculating": "в расчёте...",
        "total_time": "Общее время",
        "output": "Путь вывода",
    },
}


def normalize_language(language_code):
    if not language_code:
        return DEFAULT_LANGUAGE
    if language_code.lower().split("-")[0] == "ru":
        return "ru"
    return DEFAULT_LANGUAGE


def t(key, language_code=DEFAULT_LANGUAGE):
    language = normalize_language(language_code)
    return TRANSLATIONS[language].get(key, TRANSLATIONS[DEFAULT_LANGUAGE][key])
