"""Audio format conversion utilities."""

import numpy as np

WHISPERLIVE_RATE = 16000


def pcm_to_float32(pcm_bytes: bytes) -> np.ndarray:
    """Convert 16-bit PCM bytes to float32 array normalized to [-1, 1]."""
    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    audio /= 32768.0
    return audio


def resample(audio: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    """Simple linear interpolation resampling."""
    if from_rate == to_rate:
        return audio
    ratio = to_rate / from_rate
    new_len = int(len(audio) * ratio)
    return np.interp(
        np.linspace(0, len(audio), new_len),
        np.arange(len(audio)),
        audio,
    ).astype(np.float32)
