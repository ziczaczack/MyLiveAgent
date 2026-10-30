import math

import numpy as np

from app.avatar.lipsync import envelope


def test_silence_is_zero():
    out = envelope(np.zeros(16000, dtype=np.int16), 16000, 30, 1.0, 0.0)
    assert len(out) == math.ceil(16000 / (16000 / 30))
    assert all(v == 0.0 for v in out)


def test_loud_is_near_one():
    sq = np.tile(np.array([32767, -32768], dtype=np.int16), 8000)
    out = envelope(sq, 16000, 30, 1.0, 0.0)
    assert all(v >= 0.95 for v in out)


def test_length_matches_duration():
    out = envelope(np.zeros(36000, dtype=np.int16), 24000, 30, 1.0, 0.0)
    assert len(out) == 45


def test_stereo_accepted():
    mono = np.zeros(8000, dtype=np.int16)
    stereo = np.zeros((8000, 2), dtype=np.int16)
    assert len(envelope(stereo, 16000, 30, 1.0, 0.0)) == len(envelope(mono, 16000, 30, 1.0, 0.0))


def test_smoothing_rises_gradually():
    sq = np.tile(np.array([20000, -20000], dtype=np.int16), 8000)
    samples = np.concatenate([np.zeros(16000, dtype=np.int16), sq])
    out = envelope(samples, 16000, 30, 1.0, 0.5)
    first_loud = math.ceil(16000 / (16000 / 30))
    assert out[first_loud] < out[first_loud + 1]


def test_clipped_to_one():
    sig = (np.sin(np.arange(16000) / 5) * 8000).astype(np.int16)
    out = envelope(sig, 16000, 30, 100.0, 0.0)
    assert max(out) == 1.0
