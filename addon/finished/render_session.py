from dataclasses import dataclass
from typing import Optional
from uuid import uuid4

from .eta import (
    calculate_average_frame_time,
    calculate_eta_seconds,
    calculate_progress,
    format_duration,
)


STATE_IDLE = "idle"
STATE_RENDERING = "rendering"
STATE_FINISHED = "finished"
STATE_FAILED = "failed"
STATE_CANCELLED = "cancelled"


@dataclass(frozen=True)
class RenderSession:
    session_id: str
    project_name: str
    scene_name: str
    frame_start: int
    frame_end: int
    frame_step: int
    total_frames: int
    file_format: str
    preview_supported: bool
    output_path: str
    render_engine: str = ""
    render_samples: Optional[int] = None
    resolution_x: Optional[int] = None
    resolution_y: Optional[int] = None
    status: str = STATE_IDLE
    completed_frames: int = 0
    current_frame: Optional[int] = None
    elapsed_seconds: float = 0.0

    @classmethod
    def from_scene(cls, scene, preflight_result, project_name="", output_path=""):
        return cls(
            session_id=make_session_id(),
            project_name=project_name or "Unsaved Blender file",
            scene_name=scene.name,
            frame_start=scene.frame_start,
            frame_end=scene.frame_end,
            frame_step=scene.frame_step,
            total_frames=preflight_result.total_frames,
            file_format=preflight_result.file_format,
            preview_supported=preflight_result.preview_supported,
            output_path=output_path or scene.render.filepath,
            render_engine=scene.render.engine,
            render_samples=_render_samples(scene),
            resolution_x=_scaled_resolution(scene.render.resolution_x, scene.render.resolution_percentage),
            resolution_y=_scaled_resolution(scene.render.resolution_y, scene.render.resolution_percentage),
        )

    @property
    def progress_percent(self):
        return calculate_progress(self.completed_frames, self.total_frames)

    @property
    def average_frame_time(self):
        return calculate_average_frame_time(self.elapsed_seconds, self.completed_frames)

    @property
    def eta_seconds(self):
        return calculate_eta_seconds(
            self.elapsed_seconds,
            self.completed_frames,
            self.total_frames,
        )

    def start(self):
        return self._replace(status=STATE_RENDERING, current_frame=self.frame_start)

    def complete_frame(self, frame, elapsed_seconds):
        completed = self._completed_count_for_frame(frame)
        return self._replace(
            status=STATE_RENDERING,
            current_frame=frame,
            completed_frames=completed,
            elapsed_seconds=elapsed_seconds,
        )

    def finish(self, elapsed_seconds):
        return self._replace(
            status=STATE_FINISHED,
            current_frame=self.frame_end,
            completed_frames=self.total_frames,
            elapsed_seconds=elapsed_seconds,
        )

    def fail(self, elapsed_seconds):
        return self._replace(status=STATE_FAILED, elapsed_seconds=elapsed_seconds)

    def cancel(self, elapsed_seconds):
        return self._replace(status=STATE_CANCELLED, elapsed_seconds=elapsed_seconds)

    def status_line(self):
        eta = format_duration(self.eta_seconds)
        elapsed = format_duration(self.elapsed_seconds)
        return (
            f"{self.status}: frame {self.current_frame or '-'} | "
            f"{self.completed_frames}/{self.total_frames} "
            f"({self.progress_percent:.1f}%) | elapsed {elapsed} | ETA {eta}"
        )

    def _completed_count_for_frame(self, frame):
        if frame < self.frame_start:
            return 0
        return min(
            self.total_frames,
            ((frame - self.frame_start) // self.frame_step) + 1,
        )

    def _replace(self, **changes):
        data = self.__dict__.copy()
        data.update(changes)
        return type(self)(**data)


def make_session_id():
    return uuid4().hex


def _render_samples(scene):
    if scene.render.engine == "CYCLES":
        return _positive_int(getattr(getattr(scene, "cycles", None), "samples", None))
    if scene.render.engine == "BLENDER_EEVEE":
        return _positive_int(getattr(getattr(scene, "eevee", None), "taa_render_samples", None))
    return None


def _scaled_resolution(value, percentage):
    return max(1, (int(value) * int(percentage)) // 100)


def _positive_int(value):
    if value is None:
        return None
    value = int(value)
    return value if value > 0 else None
