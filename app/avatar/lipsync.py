from __future__ import annotations

import math

import numpy as np


def envelope(
    samples: np.ndarray, sample_rate: int, fps: float, gain: float, smoothing: float
) -> list[float]:
    """Mouth-open value per 1/fps frame: RMS / 32768 * gain, clipped to [0, 1], smoothed."""
    data = np.asarray(samples, dtype=np.float64)
    if data.ndim > 1:
        data = data.mean(axis=1)
    frame = sample_rate / fps
    out: list[float] = []
    prev = 0.0
    for i in range(math.ceil(len(data) / frame)):
        chunk = data[int(round(i * frame)) : int(round((i + 1) * frame))]
        rms = float(np.sqrt(np.mean(chunk**2))) if len(chunk) else 0.0
        raw = min(1.0, max(0.0, rms / 32768 * gain))
        prev = smoothing * prev + (1 - smoothing) * raw
        out.append(prev)
    return out
