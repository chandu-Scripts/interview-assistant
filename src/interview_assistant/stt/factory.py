from interview_assistant.config.settings import Settings
from interview_assistant.stt.base import STTProvider


def build_provider(settings: Settings) -> STTProvider:
    if settings.stt_provider == "openai":
        if settings.openai_api_key is None:
            raise ValueError("STT_PROVIDER=openai requires OPENAI_API_KEY")
        from interview_assistant.stt.openai_stt import OpenAISTTProvider

        return OpenAISTTProvider(
            settings.openai_api_key.get_secret_value(),
            language=settings.stt_language,
            prompt=settings.stt_vocabulary,
        )
    from interview_assistant.stt.local_whisper import FasterWhisperProvider

    return FasterWhisperProvider(
        settings.whisper_model,
        language=settings.stt_language,
        initial_prompt=settings.stt_vocabulary,
    )
