from collections import deque
from dataclasses import dataclass


DEFAULT_MAX_SAMPLES = 10_000


@dataclass(frozen=True)
class FrameSample:
    frame: int
    duration_seconds: float


class FrameTimingAccumulator:
    def __init__(
        self,
        frame_start,
        frame_end,
        frame_step,
        max_samples=DEFAULT_MAX_SAMPLES,
    ):
        if frame_step <= 0:
            raise ValueError("frame_step must be positive")
        if frame_end < frame_start:
            raise ValueError("frame_end must not precede frame_start")
        if max_samples <= 0:
            raise ValueError("max_samples must be positive")

        self._frame_start = frame_start
        self._frame_end = frame_end
        self._frame_step = frame_step
        self._samples = deque(maxlen=max_samples)
        self._dropped_samples = 0
        self._open_frame = None
        self._open_started_at = None

    @property
    def samples(self):
        return tuple(self._samples)

    @property
    def open_frame(self):
        return self._open_frame

    @property
    def dropped_samples(self):
        return self._dropped_samples

    def begin_frame(self, frame, started_at):
        if not self._is_expected_frame(frame) or not self._is_forward_frame(frame):
            return False
        if self._open_frame == frame:
            return False

        self._open_frame = frame
        self._open_started_at = started_at
        return True

    def complete_frame(self, frame, completed_at):
        if frame != self._open_frame or self._open_started_at is None:
            return None

        duration = completed_at - self._open_started_at
        self.discard_open_frame()
        if duration < 0:
            return None

        sample = FrameSample(frame=frame, duration_seconds=duration)
        if len(self._samples) == self._samples.maxlen:
            self._dropped_samples += 1
        self._samples.append(sample)
        return sample

    def discard_open_frame(self):
        self._open_frame = None
        self._open_started_at = None

    def _is_expected_frame(self, frame):
        return (
            self._frame_start <= frame <= self._frame_end
            and (frame - self._frame_start) % self._frame_step == 0
        )

    def _is_forward_frame(self, frame):
        if self._samples and frame <= self._samples[-1].frame:
            return False
        if self._open_frame is not None and frame < self._open_frame:
            return False
        return True
