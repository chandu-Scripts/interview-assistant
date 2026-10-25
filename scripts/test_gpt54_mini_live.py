"""Opt-in billed smoke test. Synthetic questions only; no capture or user-data writes."""

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx

from interview_assistant.agents.specialists import SPECIALISTS, system_prompt
from interview_assistant.agents.types import Category
from interview_assistant.config.keys import KeyringStore
from interview_assistant.config.settings import Settings
from interview_assistant.cost.pricing import PriceBook
from interview_assistant.llm.openai_llm import OpenAIProvider
from interview_assistant.llm.types import LLMError, LLMRequest, Message


class BenchmarkProvider(OpenAIProvider):
    def build_payload(self, req: LLMRequest) -> dict:
        body = super().build_payload(req)
        if body["model"] == "gpt-5.6-luna":
            body["reasoning_effort"] = "none"
            body.pop("temperature", None)
        return body


async def main(model_override: str | None = None) -> None:
    settings = Settings()
    key = (
        settings.openai_api_key.get_secret_value()
        if settings.openai_api_key
        else KeyringStore().get("openai")
    )
    if not key:
        print("No OpenAI API key available; live test not run.")
        return
    questions = [
        (Category.CONCEPT, "What is the difference between a Python list and tuple?"),
        (Category.DATA_SQL, "Write SQL to find the second highest distinct salary."),
        (Category.HR, "How many people were on your project team?"),
    ]
    results = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=10)) as client:
        provider = BenchmarkProvider(key, client=client)
        runs = (
            [(model_override, questions)]
            if model_override
            else [("gpt-5.4-mini", questions), ("gpt-4o-mini", questions[:1])]
        )
        for model, cases in runs:
            for category, question in cases:
                spec = SPECIALISTS[category]
                request = LLMRequest(
                    [Message("user", question)],
                    model=model,
                    system=system_prompt(spec, None),
                    max_tokens=spec.max_tokens,
                )
                start = time.perf_counter()
                first = None
                text = ""
                usage = None
                finish = None
                try:
                    async for event in provider.stream(request):
                        if event.delta:
                            if first is None:
                                first = time.perf_counter() - start
                            text += event.delta
                        if event.usage:
                            usage = event.usage
                        if event.finish_reason:
                            finish = event.finish_reason
                except LLMError as error:
                    print(
                        json.dumps(
                            {"model": model, "error": error.kind.value, "status": error.status}
                        )
                    )
                    return
                result = {
                    "model": model,
                    "question": question,
                    "first_text_s": round(first, 3) if first is not None else None,
                    "total_s": round(time.perf_counter() - start, 3),
                    "finish": finish,
                    "answer": text,
                    "estimated_usd": (
                        (usage.input_tokens * 0.20 + usage.output_tokens * 1.20) / 1_000_000
                        if model == "gpt-5.6-luna"
                        else PriceBook().llm_cost(
                            "openai", model, usage.input_tokens, usage.output_tokens
                        )
                    )
                    if usage
                    else None,
                }
                results.append(result)
                print(json.dumps(result), flush=True)
    out = Path("tmp/gpt56-luna-live.json" if model_override else "tmp/gpt54-mini-live.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=["gpt-5.6-luna"])
    asyncio.run(main(parser.parse_args().model))
