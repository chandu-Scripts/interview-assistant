import httpx
import numpy as np

from interview_assistant.stt.base import STTError, is_mostly_latin_script, pcm_to_wav

URL = "https://api.openai.com/v1/audio/transcriptions"


class OpenAISTTProvider:
    """Cloud transcription via OpenAI. Audio is sent to OpenAI - only used when configured."""

    name = "openai"
    metered = True  # every call is billed, so partial re-transcription is off by default

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini-transcribe",
        language: str | None = "en",
        prompt: str | None = None,
        client: httpx.Client | None = None,
        timeout_s: float = 20.0,
    ) -> None:
        self._key = api_key
        self._model = model
        self.model = model
        self._language = language
        self._prompt = prompt or None  # vocabulary hint (see settings), same as local Whisper
        self._client = client or httpx.Client(timeout=timeout_s)

    def warmup(self) -> None:
        return None

    def transcribe(self, pcm: np.ndarray) -> str:
        if len(pcm) == 0:
            return ""
        data = {"model": self._model, "response_format": "json"}
        if self._language:
            data["language"] = self._language
        if self._prompt:
            data["prompt"] = self._prompt
        try:
            r = self._client.post(
                URL,
                headers={"Authorization": f"Bearer {self._key}"},
                data=data,
                files={"file": ("audio.wav", pcm_to_wav(pcm), "audio/wav")},
            )
            r.raise_for_status()
            text = str(r.json().get("text", "")).strip()
            if self._language == "en" and not is_mostly_latin_script(text):
                return ""  # the model drifted into another script despite the English lock
            return text
        except httpx.HTTPStatusError as e:
            raise STTError(f"openai stt HTTP {e.response.status_code}") from e  # no body: no leaks
        except (httpx.HTTPError, ValueError) as e:
            raise STTError(f"openai stt failed: {type(e).__name__}") from e
