from interview_assistant.config.settings import Settings
from interview_assistant.stt.base import STTProvider


def build_preview_provider(settings: Settings) -> STTProvider:
    """Local previews keep cloud requests limited to one final upload per utterance."""
    from interview_assistant.stt.local_whisper import FasterWhisperProvider

    return FasterWhisperProvider(
        "tiny.en" if settings.stt_language == "en" else "tiny",
        cpu_threads=2,
        language=settings.stt_language,
        initial_prompt=settings.stt_vocabulary,
    )


def build_provider(settings: Settings) -> STTProvider:
    if settings.stt_provider == "speechmatics":
        if settings.speechmatics_api_key is None:
            raise ValueError("STT_PROVIDER=speechmatics requires SPEECHMATICS_API_KEY")
        from interview_assistant.stt.speechmatics import SpeechmaticsProvider

        fallback_settings = settings.model_copy(
            update={
                "stt_provider": "openai" if settings.openai_api_key else "local",
            }
        )
        return SpeechmaticsProvider(
            settings.speechmatics_api_key.get_secret_value(),
            fallback=build_provider(fallback_settings),
            language=settings.stt_language,
        )
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
