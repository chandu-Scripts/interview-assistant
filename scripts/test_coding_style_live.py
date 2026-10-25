"""Opt-in billed coding prompt check; synthetic questions only, never user documents."""

import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter

import httpx

from interview_assistant.agents.specialists import SPECIALISTS, system_prompt
from interview_assistant.agents.types import Category
from interview_assistant.config.loader import load_settings
from interview_assistant.llm.openai_llm import OpenAIProvider
from interview_assistant.llm.types import LLMError, LLMRequest, Message

CASES = [
    (
        Category.CODING,
        "Write a Python function merge_intervals(intervals) to merge overlapping closed "
        "intervals without mutating the input. Handle an empty list, unsorted input, nested "
        "intervals and touching endpoints. Use [[8,10],[1,4],[2,3],[4,6]] as the example.",
    ),
    (
        Category.DATA_SQL,
        "Write a SQLite query for the top two distinct salaries per department, retaining "
        "all tied employees and excluding NULL salaries. Schema employees(id INTEGER, "
        "department TEXT, salary INTEGER). Rows: (1,'A',100),(2,'A',100),(3,'A',90),"
        "(4,'A',80),(5,'B',NULL),(6,'B',50). Sort by department, salary descending, id.",
    ),
]


async def main(model: str) -> None:
    settings = load_settings()
    if not settings.openai_api_key:
        raise SystemExit("OpenAI key unavailable; live check not run")
    results = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=10)) as client:
        provider = OpenAIProvider(settings.openai_api_key.get_secret_value(), client=client)
        for category, question in CASES:
            spec = SPECIALISTS[category]
            start = perf_counter()
            first_token = None
            parts = []
            finish = None
            try:
                async for event in provider.stream(
                    LLMRequest(
                        [Message("user", question)],
                        system=system_prompt(spec, None, answer_length="Short"),
                        model=model,
                        max_tokens=spec.max_tokens,
                    )
                ):
                    if event.delta:
                        if first_token is None:
                            first_token = perf_counter() - start
                        parts.append(event.delta)
                    if event.finish_reason:
                        finish = event.finish_reason
            except LLMError as error:
                raise SystemExit(
                    f"Live check failed: {error.kind.value}, HTTP {error.status}"
                ) from None
            results.append(
                {
                    "question": question,
                    "answer": "".join(parts),
                    "model": model,
                    "finish": finish,
                    "first_token_s": first_token,
                    "total_s": perf_counter() - start,
                }
            )
            print(f"{model} {category.value}: finish={finish}", flush=True)
    destination = Path("tmp") / f"coding-style-{model}.json"
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Synthetic answers for manual review: {destination}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=["gpt-5.4", "gpt-5.4-mini"], required=True)
    asyncio.run(main(parser.parse_args().model))
