import numpy as np

from interview_assistant.audio.types import TARGET_RATE


def to_mono_16k(raw: bytes, channels: int, rate: int) -> np.ndarray:
    """Convert interleaved int16 PCM at any rate/channel count to mono int16 at 16 kHz."""
    samples = np.frombuffer(raw, dtype=np.int16)
    x: np.ndarray = samples.astype(np.float32)
    if channels > 1:
        usable = len(x) - len(x) % channels
        x = x[:usable].reshape(-1, channels).mean(axis=1)
    if rate != TARGET_RATE and len(x):
        if rate % TARGET_RATE == 0:
            f = rate // TARGET_RATE
            usable = len(x) - len(x) % f
            x = x[:usable].reshape(-1, f).mean(axis=1)  # box filter = cheap anti-alias
        else:
            n_out = int(len(x) * TARGET_RATE / rate)
            x = np.interp(np.linspace(0, len(x) - 1, n_out), np.arange(len(x)), x)
    return np.clip(x, -32768, 32767).astype(np.int16)


def rms_level(pcm: np.ndarray) -> float:
    """RMS normalised to 0..1."""
    if len(pcm) == 0:
        return 0.0
    return float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2)) / 32768.0)
