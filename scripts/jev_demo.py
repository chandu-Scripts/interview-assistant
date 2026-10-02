"""Live Jev demo: python scripts/jev_demo.py ["question" ...]  (default: a mixed sample set)."""

import asyncio
import sys
import time

from interview_assistant.agents.jev import Jev, JevConfig
from interview_assistant.agents.types import Decision
from interview_assistant.config.settings import Settings
from interview_assistant.llm.router_factory import build_router

SAMPLE = [
    "Okay, great.",
    "Tell me about a time you resolved a conflict on your team.",
    "What was the outcome of that?",
    "Write a function to check if a string is a palindrome.",
    "How would you handle a million writes per second?",
    "What is the difference between a process and a thread?",
]


async def main(questions: list[str]) -> None:
    settings = Settings()
    router = build_router(settings)
    jev = Jev(
        router,
        JevConfig(
            profile=settings.candidate_profile
            or "Backend engineer, 5 years Python/Go, payments platform at a fintech.",
            classify_timeout_s=settings.jev_classify_timeout_s,
        ),
    )
    for q in questions:
        print(f"\n>>> {q}")
        t0 = time.time()
        first = None
        text = ""
        async for ev in jev.ask(q):
            if isinstance(ev, Decision):
                print(
                    f"    [{ev.category} via {ev.source} conf={ev.confidence:.2f} "
                    f"agent={ev.agent} tier={ev.tier}] decided in {time.time() - t0:.2f}s"
                )
                continue
            if ev.reset:
                text = ""
            if ev.event.delta and first is None:
                first = time.time() - t0
            text += ev.event.delta
            route = ev.route
        if text:
            print(f"    [{route} first token {first:.2f}s, total {time.time() - t0:.1f}s]")
            print("    " + text.strip()[:500].replace("\n", "\n    "))


asyncio.run(main(sys.argv[1:] or SAMPLE))
