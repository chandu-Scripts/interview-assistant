"""Speechmatics WebSocket transport. No audio or credentials are logged."""

import json
import logging
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import numpy as np
from websockets.sync.client import connect

from interview_assistant.stt.base import STTError, STTProvider

log = logging.getLogger(__name__)


class SpeechmaticsProvider:
    name = "speechmatics"
    model = "standard"
    metered = True
    realtime = True

    def __init__(
        self,
        key: str,
        fallback: STTProvider,
        language: str | None = "en",
        connector: Callable[..., Any] = connect,
    ) -> None:
        self._key = key
        self.fallback = fallback
        self._language = language or "en"
        self._connect = connector

    def warmup(self) -> None:
        # Starting the application must not open a billed audio session.
        pass

    def transcribe(self, pcm: np.ndarray) -> str:
        return self.fallback.transcribe(pcm)

    def stream(self, chunks: Iterator[np.ndarray], on_partial: Callable[[str], None]) -> str:
        # A private disabled logger also prevents DEBUG WebSocket headers leaking the key.
        logger = logging.Logger("speechmatics-transport")
        logger.disabled = True
        finished = threading.Event()
        sending_done = threading.Event()
        sender: threading.Thread | None = None
        send_errors: list[Exception] = []
        phase = "connection"
        try:
            with self._connect(
                "wss://eu.rt.speechmatics.com/v2/",
                additional_headers={"Authorization": "Bearer " + self._key},
                open_timeout=5,
                close_timeout=1,
                max_size=1024 * 1024,
                logger=logger,
            ) as ws:
                phase = "recognition startup"
                ws.send(
                    json.dumps(
                        {
                            "message": "StartRecognition",
                            "audio_format": {
                                "type": "raw",
                                "encoding": "pcm_s16le",
                                "sample_rate": 16000,
                            },
                            "transcription_config": {
                                "language": self._language,
                                "enable_partials": True,
                                "max_delay": 0.7,
                                "max_delay_mode": "fixed",
                                "model": self.model,
                            },
                        }
                    )
                )
                deadline = time.monotonic() + 5
                while True:
                    ready = json.loads(ws.recv(timeout=max(0.01, deadline - time.monotonic())))
                    if ready.get("message") == "RecognitionStarted":
                        break
                    if ready.get("message") == "Error" or time.monotonic() >= deadline:
                        raise STTError("Speechmatics could not start recognition.")

                def send() -> None:
                    sequence = 0
                    try:
                        for chunk in chunks:
                            if finished.is_set():
                                return
                            # Bound each message even when connecting after audio has accumulated.
                            for offset in range(0, len(chunk), 3200):
                                ws.send(chunk[offset : offset + 3200].astype("<i2").tobytes())
                                sequence += 1
                        ws.send(json.dumps({"message": "EndOfStream", "last_seq_no": sequence}))
                    except Exception as exc:
                        send_errors.append(exc)
                        ws.close()
                    finally:
                        sending_done.set()

                sender = threading.Thread(target=send, name="speechmatics-send", daemon=True)
                sender.start()
                phase = "live recognition"
                confirmed = ""
                while True:
                    data = json.loads(ws.recv(timeout=8))
                    kind = data.get("message")
                    if kind == "Error":
                        raise STTError("Speechmatics recognition failed.")
                    if kind in {"AddTranscript", "AddPartialTranscript"}:
                        text = data.get("metadata", {}).get("transcript", "")
                        if kind == "AddTranscript":
                            confirmed = " ".join((confirmed.strip(), text.strip())).strip()
                            on_partial(confirmed.strip())
                        else:
                            on_partial(" ".join((confirmed.strip(), text.strip())).strip())
                    elif kind == "EndOfTranscript":
                        if not sending_done.wait(1) or send_errors:
                            raise STTError("Speechmatics audio upload did not finish.")
                        return confirmed.strip()
        except Exception as exc:
            # Remote errors can contain request details. Expose only our own safe message.
            reason = "timeout" if isinstance(exc, TimeoutError) else "failure"
            log.warning("Speechmatics %s: %s; fallback requested", phase, reason)
            raise STTError(
                f"Speechmatics {phase} {reason}; using fallback transcription."
            ) from None
        finally:
            finished.set()
            # The chunk iterator polls finished via its owning worker's cancellation signal.
            # On remote failure it is closed by that worker after this call returns.
            if sender is not None:
                sender.join(0.1)
