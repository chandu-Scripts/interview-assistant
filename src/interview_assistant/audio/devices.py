import logging

from interview_assistant.audio.types import AudioDevice, DeviceKind

log = logging.getLogger(__name__)


def list_devices() -> list[AudioDevice]:
    """Enumerate WASAPI microphones and loopback (system audio) devices."""
    try:
        import pyaudiowpatch as pa
    except ImportError:  # pragma: no cover
        log.error("pyaudiowpatch not installed")
        return []
    p = pa.PyAudio()
    try:
        wasapi = p.get_host_api_info_by_type(pa.paWASAPI)
        default_in = wasapi.get("defaultInputDevice", -1)
        try:
            default_loop = p.get_default_wasapi_loopback()["index"]
        except Exception:  # no output device
            default_loop = -1
        out: list[AudioDevice] = []
        for i in range(p.get_device_count()):
            d = p.get_device_info_by_index(i)
            if d["hostApi"] != wasapi["index"] or d["maxInputChannels"] < 1:
                continue
            kind = DeviceKind.LOOPBACK if d.get("isLoopbackDevice") else DeviceKind.MIC
            default = i == (default_loop if kind is DeviceKind.LOOPBACK else default_in)
            out.append(
                AudioDevice(
                    index=i,
                    name=d["name"],
                    kind=kind,
                    channels=int(d["maxInputChannels"]),
                    sample_rate=int(d["defaultSampleRate"]),
                    is_default=default,
                )
            )
        return out
    finally:
        p.terminate()


def default_device(
    kind: DeviceKind, devices: list[AudioDevice] | None = None
) -> AudioDevice | None:
    devices = list_devices() if devices is None else devices
    of_kind = [d for d in devices if d.kind is kind]
    return next((d for d in of_kind if d.is_default), of_kind[0] if of_kind else None)
