from dataclasses import dataclass, field
from pathlib import Path


VIDEO_FORMATS = frozenset({"FFMPEG"})
PREVIEW_FORMATS = frozenset({"PNG", "JPEG", "TIFF", "WEBP"})
EXR_FORMATS = frozenset({"OPEN_EXR", "OPEN_EXR_MULTILAYER"})


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    total_frames: int = 0
    file_format: str = ""
    preview_supported: bool = False
    errors: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def summary(self):
        if self.ok:
            details = f"{self.total_frames} frames, {self.file_format}"
            if self.warnings:
                return f"Finished? preflight passed with warnings: {details}."
            return f"Finished? preflight passed: {details}."
        return "Finished? preflight failed: " + "; ".join(self.errors)


def calculate_total_frames(frame_start, frame_end, frame_step):
    if frame_step <= 0 or frame_end < frame_start:
        return 0
    return ((frame_end - frame_start) // frame_step) + 1


def _is_probably_directory(path_text):
    return path_text.endswith(("/", "\\"))


def _output_parent(path_text):
    path = Path(path_text)
    if _is_probably_directory(path_text):
        return path
    return path.parent


def validate_render_settings(
    *,
    frame_start,
    frame_end,
    frame_step,
    file_format,
    filepath,
    resolved_filepath=None,
):
    errors = []
    warnings = []

    total_frames = calculate_total_frames(frame_start, frame_end, frame_step)

    if frame_step <= 0:
        errors.append("Frame step must be greater than 0.")

    if frame_end < frame_start:
        errors.append("Frame end must be greater than or equal to frame start.")

    if total_frames < 2:
        errors.append("Finished? monitors animation sequences. Use at least 2 frames.")

    if file_format in VIDEO_FORMATS:
        errors.append(
            "Finished? works with image sequences. Switch Output Format from FFmpeg Video to an image format."
        )

    if not filepath or not filepath.strip():
        errors.append("Output path is empty. Set an output path before rendering.")
    elif resolved_filepath:
        parent = _output_parent(resolved_filepath)
        if not parent.exists():
            warnings.append(f"Output folder does not exist yet: {parent}")
        elif not parent.is_dir():
            errors.append(f"Output path parent is not a folder: {parent}")

    preview_supported = file_format in PREVIEW_FORMATS

    if file_format in EXR_FORMATS:
        warnings.append("EXR sequence can be monitored, but preview sending is unavailable for now.")
    elif file_format not in PREVIEW_FORMATS and file_format not in VIDEO_FORMATS:
        warnings.append(f"{file_format} sequence can be monitored, but preview sending is unavailable for now.")

    return PreflightResult(
        ok=not errors,
        total_frames=total_frames,
        file_format=file_format,
        preview_supported=preview_supported,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def run_preflight(scene):
    if scene is None:
        return PreflightResult(
            ok=False,
            errors=("No active Blender scene found.",),
        )

    render = scene.render
    filepath = render.filepath
    resolved_filepath = filepath

    try:
        import bpy

        resolved_filepath = bpy.path.abspath(filepath)
    except Exception:
        resolved_filepath = filepath

    return validate_render_settings(
        frame_start=scene.frame_start,
        frame_end=scene.frame_end,
        frame_step=scene.frame_step,
        file_format=render.image_settings.file_format,
        filepath=filepath,
        resolved_filepath=resolved_filepath,
    )
