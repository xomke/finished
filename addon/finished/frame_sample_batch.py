FRAME_SAMPLE_BATCH_VERSION = 1
MAX_FRAME_SAMPLES_PER_BATCH = 10_000


def build_frame_sample_batch(
    samples,
    dropped_samples=0,
    max_samples=MAX_FRAME_SAMPLES_PER_BATCH,
):
    if max_samples <= 0:
        raise ValueError("max_samples must be positive")
    if dropped_samples < 0:
        raise ValueError("dropped_samples must not be negative")

    samples = tuple(samples)
    selected = samples[-max_samples:]
    truncated = dropped_samples > 0 or len(samples) > max_samples

    return {
        "version": FRAME_SAMPLE_BATCH_VERSION,
        "encoding": "frame_duration_ms",
        "truncated": truncated,
        "samples": [
            [sample.frame, _duration_milliseconds(sample.duration_seconds)]
            for sample in selected
        ],
    }


def _duration_milliseconds(duration_seconds):
    if duration_seconds < 0:
        raise ValueError("frame duration must not be negative")
    return int(round(duration_seconds * 1000))
