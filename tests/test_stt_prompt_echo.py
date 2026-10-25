from unittest.mock import MagicMock

import httpx
import numpy as np
import pytest

from interview_assistant.app.aggregator import UtteranceAggregator
from interview_assistant.config.settings import DEFAULT_VOCABULARY
from interview_assistant.stt.base import is_prompt_echo
from interview_assistant.stt.local_whisper import FasterWhisperProvider
from interview_assistant.stt.openai_stt import OpenAISTTProvider


@pytest.mark.parametrize(
    "text",
    [
        DEFAULT_VOCABULARY,
        DEFAULT_VOCABULARY.upper().replace(",", " "),
        DEFAULT_VOCABULARY.split("Terms: ")[1],
        DEFAULT_VOCABULARY.replace("GraphQL, ", ""),
    ],
)
def test_long_hint_echo_rejected(text):
    assert is_prompt_echo(text, DEFAULT_VOCABULARY)


@pytest.mark.parametrize(
    "text",
    [
        "What is the difference between REST and GraphQL?",
        "Explain SQL, NoSQL, Kubernetes, Docker, Redis, Kafka, OAuth, JWT.",
        "API, REST, GraphQL",
        "I used Python and SQL to develop daily data ingestion pipelines.",
        "How do you use " + DEFAULT_VOCABULARY.split("Terms: ")[1],
    ],
)
def test_real_technical_speech_preserved(text):
    assert not is_prompt_echo(text, DEFAULT_VOCABULARY)


def test_no_hint_no_filter():
    assert not is_prompt_echo(DEFAULT_VOCABULARY, None)


@pytest.mark.parametrize("provider", ["cloud", "local"])
def test_hint_never_becomes_question_and_next_real_question_survives(provider):
    real = "How many catalogs have you used?"
    responses = iter([DEFAULT_VOCABULARY, real])
    if provider == "cloud":
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"text": next(responses)})
        )
        stt = OpenAISTTProvider(
            "fake", prompt=DEFAULT_VOCABULARY, client=httpx.Client(transport=transport)
        )
    else:
        stt = FasterWhisperProvider(initial_prompt=DEFAULT_VOCABULARY)
        stt._model = MagicMock()
        stt._model.transcribe.side_effect = lambda *a, **k: (
            [MagicMock(text=next(responses))],
            None,
        )
    audio = np.ones(16000, dtype=np.int16)
    clock = [1.0]
    aggregator = UtteranceAggregator(clock=lambda: clock[0])
    aggregator.speech_started()
    rejected = stt.transcribe(audio)
    assert rejected == ""
    aggregator.final(rejected)
    clock[0] += 1
    assert aggregator.poll() is None
    aggregator.speech_started()
    aggregator.final(stt.transcribe(audio))
    clock[0] += 1
    assert aggregator.poll() == (real, "")
