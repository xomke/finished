def format_duration(seconds, calculating_text="calculating..."):
    if seconds is None:
        return calculating_text

    seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def calculate_progress(completed_frames, total_frames):
    if total_frames <= 0:
        return 0.0
    return min(100.0, max(0.0, (completed_frames / total_frames) * 100.0))


def calculate_average_frame_time(elapsed_seconds, completed_frames):
    if completed_frames <= 0:
        return None
    return elapsed_seconds / completed_frames


def calculate_eta_seconds(elapsed_seconds, completed_frames, total_frames):
    if completed_frames < 2 or total_frames <= 0:
        return None

    remaining_frames = max(0, total_frames - completed_frames)
    return calculate_average_frame_time(elapsed_seconds, completed_frames) * remaining_frames
