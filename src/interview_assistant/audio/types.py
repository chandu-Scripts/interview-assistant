from dataclasses import dataclass
from enum import StrEnum

TARGET_RATE = 16_000  # Hz, mono int16 - what downstream STT consumes


class DeviceKind(StrEnum):
    MIC = "mic"
    LOOPBACK = "loopback"  # system audio (what the speakers play)


@dataclass(frozen=True)
class AudioDevice:
    index: int
    name: str
    kind: DeviceKind
    channels: int
    sample_rate: int
    is_default: bool = False

    @property
    def label(self) -> str:
        tag = "System audio" if self.kind is DeviceKind.LOOPBACK else "Microphone"
        return f"{tag}: {self.name}"
