"""Opt-in billed prompt check using synthetic questions, never resumes or captured audio."""

import asyncio
import json
import re
from pathlib import Path

import httpx

from interview_assistant.agents.specialists import SPECIALISTS, system_prompt
from interview_assistant.agents.types import Category
from interview_assistant.config.keys import KeyringStore
from interview_assistant.config.settings import Settings
from interview_assistant.llm.base import collect
from interview_assistant.llm.openai_llm import OpenAIProvider
from interview_assistant.llm.types import LLMError, LLMRequest, Message


async def main() -> None:
    settings = Settings()
    key = (
        settings.openai_api_key.get_secret_value()
        if settings.openai_api_key
        else KeyringStore().get("openai")
    )
    if not key:
        raise SystemExit("OpenAI key unavailable; live test not run")
    results = []
    async with httpx.AsyncClient(timeout=45) as client:
        provider = OpenAIProvider(key, client=client)
        for category, question in [
            (Category.DATA_SQL, "How do you optimise SQL queries?"),
            (Category.CONCEPT, "What is the difference between a Python list and tuple?"),
            (
                Category.CODING,
                "Write a Python function that adds two numbers. Use 2 and 3 as sample input.",
            ),
        ]:
            spec = SPECIALISTS[category]
            try:
                result = await collect(
                    provider,
                    LLMRequest(
                        [Message("user", question)],
                        system=system_prompt(spec, None),
                        model="gpt-4o-mini",
                        max_tokens=spec.max_tokens,
                    ),
                )
            except LLMError as error:
                raise SystemExit(
                    f"Live test failed: {error.kind.value}, HTTP {error.status}"
                ) from None
            blocks = re.findall(r"```([^\n]*)\n(.*?)```", result.text, re.S)
            counts = [
                len(
                    re.findall(
                        r"\b(?:yeah|so|mainly|basically|i mean|like|also|kind of|yes)\b", text, re.I
                    )
                )
                for language, text in blocks
                if language.strip() in ("", "text", "plaintext")
            ]
            row = {
                "question": question,
                "model": "gpt-4o-mini",
                "answer": result.text,
                "finish": result.finish_reason,
                "prose_marker_counts": counts,
            }
            results.append(row)
            print(json.dumps(row), flush=True)
    Path("tmp/spoken-style-live.json").write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
