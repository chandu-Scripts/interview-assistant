"""Opt-in billed synthetic evaluation through Jev. Never sends real resume/session data."""

import argparse
import asyncio
import json
import re
from pathlib import Path
from time import perf_counter

import httpx

from interview_assistant.agents.jev import Jev, JevConfig
from interview_assistant.config.loader import load_settings
from interview_assistant.llm.openai_llm import OpenAIProvider
from interview_assistant.llm.router import AllRoutesFailed, Route, RouteEvent, Router, RouterConfig

CASES = [
    {"id": "sql_concept", "question": "How do you optimise SQL queries?"},
    {
        "id": "list_tuple",
        "question": "What is the difference between a Python list and tuple?",
        "instructions": "Include one short shopping list analogy.",
        "required": "shopping",
    },
    {
        "id": "python",
        "question": "Write a Python function merge_intervals(intervals) to merge overlapping closed intervals without mutating the input. Handle empty and unsorted input, nested intervals, and touching endpoints. Example: [[8,10],[1,4],[2,3],[4,6]].",
        "instructions": "Include type hints in the Python function.",
        "required": "->",
    },
    {
        "id": "sql_code",
        "question": "Write a SQLite query for the top two distinct salaries per department, keeping all tied employees and excluding NULL salaries. Schema employees(id INTEGER, department TEXT, salary INTEGER). Rows: (1,'A',100),(2,'A',100),(3,'A',90),(4,'A',80),(5,'B',NULL),(6,'B',50). Sort by department, salary descending, id.",
    },
    {
        "id": "confirmed_team",
        "question": "What is your team size?",
        "profile": "Confirmed project facts: six team members: three data engineers including me, one tech lead, one tester, one business analyst.",
    },
    {"id": "missing_fact", "question": "How many catalogs have you used in your current project?"},
]

MARKERS = re.compile(r"\b(?:yeah|so|mainly|basically|i mean|like|also|kind of|yes)\b", re.I)
FENCES = re.compile(r"```([^\n]*)\n(.*?)```", re.S)


def check_style(answer: str) -> dict:
    """Literal marker checks are a proxy; naturalness and factuality need human review."""
    paragraphs = []
    for language, content in FENCES.findall(answer):
        if language.strip().lower() not in ("", "text", "plaintext"):
            continue
        if content.strip().lower().startswith("output"):
            continue
        for paragraph in re.split(r"\n\s*\n", content.strip()):
            text = re.sub(
                r"^(Approach|Line-by-line explanation)\s*:?\s*", "", paragraph, flags=re.I
            ).strip()
            if not text or text.startswith("|"):
                continue
            if text.lower().startswith(("expected output", "expected result")):
                continue
            prose = re.sub(r"`[^`]*`", "", text)
            markers = MARKERS.findall(prose)
            paragraphs.append({"text": text, "markers": markers, "count": len(markers)})
    return {
        "no_prohibited_dashes": not any(
            c in paragraph["text"] for paragraph in paragraphs for c in ("\u2013", "\u2014")
        ),
        "all_content_fenced": not FENCES.sub("", answer).strip(),
        "filler_counts_pass": bool(paragraphs) and all(3 <= p["count"] <= 4 for p in paragraphs),
        "paragraphs": paragraphs,
    }


def check_coding(answer: str, language: str) -> dict:
    blocks = FENCES.findall(answer)
    code_indices = [i for i, (tag, _) in enumerate(blocks) if tag.strip() == language]
    if len(code_indices) != 1:
        return {"four_parts": False, "all_lines_commented": False, "all_lines_explained": False}
    index = code_indices[0]
    code = blocks[index][1]
    explanation = "\n".join(text for _, text in blocks[index + 2 :])
    comment = "#" if language == "python" else "--"
    lines = [line for line in code.splitlines() if line.strip()]
    return {
        "four_parts": (
            index == 1
            and blocks[0][1].lstrip().startswith("Approach")
            and len(blocks) > index + 2
            and blocks[index + 1][1].lstrip().startswith("Output")
            and blocks[index + 2][1].lstrip().startswith("Line-by-line explanation")
        ),
        "all_lines_commented": all(
            re.search(rf"{comment}\s*Line {number}:\s+\S.+", line)
            for number, line in enumerate(lines, 1)
        ),
        "all_lines_explained": all(
            re.search(rf"\bLine {number}\b", explanation, re.I)
            for number in range(1, len(lines) + 1)
        ),
    }


async def main(output: Path, repeats: int, selected: list[str] | None) -> None:
    settings = load_settings()
    if not settings.openai_api_key:
        raise SystemExit("OpenAI key unavailable; evaluation not run")
    output.parent.mkdir(parents=True, exist_ok=True)
    results = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=10)) as client:
        provider = OpenAIProvider(settings.openai_api_key.get_secret_value(), client=client)
        router = Router(
            {"openai": provider},
            RouterConfig(
                chains={
                    "fast": [Route("openai", "gpt-5.4-mini")],
                    "smart": [Route("openai", "gpt-5.4")],
                }
            ),
        )
        for repeat in range(repeats):
            for case in CASES:
                if selected and case["id"] not in selected:
                    continue
                jev = Jev(
                    router,
                    JevConfig(
                        classify_with_llm=False,
                        use_builtin_prompt=True,
                        instructions=case.get("instructions", ""),
                        profile=case.get("profile"),
                        coding_model_route="openai:gpt-5.4",
                        fast_model_route="openai:gpt-5.4-mini",
                    ),
                )
                parts = []
                finish = route = None
                first = None
                start = perf_counter()
                try:
                    async for event in jev.ask(case["question"]):
                        if not isinstance(event, RouteEvent):
                            continue
                        route = str(event.route)
                        if event.event.delta:
                            first = perf_counter() - start if first is None else first
                            parts.append(event.event.delta)
                        if event.event.finish_reason:
                            finish = event.event.finish_reason
                except AllRoutesFailed as error:
                    raise SystemExit(
                        "Live evaluation failed: " + ", ".join(e.kind.value for e in error.errors)
                    ) from None
                answer = "".join(parts)
                checks = check_style(answer)
                if case["id"] in ("python", "sql_code"):
                    checks["coding"] = check_coding(
                        answer, "python" if case["id"] == "python" else "sql"
                    )
                required = case.get("required", "").lower() in answer.lower()
                row = {
                    **case,
                    "repeat": repeat + 1,
                    "route": route,
                    "answer": answer,
                    "finish": finish,
                    "first_token_s": first,
                    "total_s": perf_counter() - start,
                    "checks": checks,
                    "extra_instruction_pass": required,
                }
                results.append(row)
                output.write_text(json.dumps(results, indent=2), encoding="utf-8")
                print(
                    json.dumps(
                        {
                            "case": case["id"],
                            "repeat": repeat + 1,
                            "finish": finish,
                            "fillers": [p["count"] for p in checks["paragraphs"]],
                            "no_dashes": checks["no_prohibited_dashes"],
                            "extra_guidance": required,
                            "coding": checks.get("coding"),
                        }
                    ),
                    flush=True,
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--output", type=Path, default=Path("tmp/answer-style-eval.json"))
    parser.add_argument("--repeats", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--cases", nargs="+", choices=[case["id"] for case in CASES])
    args = parser.parse_args()
    asyncio.run(main(args.output, args.repeats, args.cases))
