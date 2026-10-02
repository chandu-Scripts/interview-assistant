"""Live router demo. Usage: python scripts/router_demo.py [tier] [prompt]
Try a forced failover: ROUTER_FAST="openai:no-such-model,gemini:gemini-flash-latest" """

import asyncio
import sys

from interview_assistant.config.settings import Settings
from interview_assistant.llm.router import AllRoutesFailed, AttemptRecord
from interview_assistant.llm.router_factory import build_router
from interview_assistant.llm.types import LLMRequest, Message


def show(rec: AttemptRecord) -> None:
    status = "OK  " if rec.ok else "FAIL"
    ft = f"{rec.first_token_s:.2f}s" if rec.first_token_s is not None else "-"
    extra = f" {rec.error}" if rec.error else f" usage={rec.usage}"
    print(f"\n[{status}] {rec.route} first_token={ft} total={rec.latency_s:.2f}s{extra}")


async def main(tier: str, prompt: str) -> None:
    router = build_router(Settings(), on_attempt=show)
    print("chain:", [str(r) for r in router.chain_for(tier)])
    req = LLMRequest([Message("user", prompt)], max_tokens=200)
    try:
        async for ev in router.stream(req, tier):
            if ev.reset:
                print(f"\n--- restart on {ev.route} ---")
            print(ev.event.delta, end="", flush=True)
    except AllRoutesFailed as e:
        sys.exit(f"all routes failed: {str(e)[:300]}")


asyncio.run(main(*(sys.argv[1:3] if len(sys.argv) > 2 else ["fast", "Say hello in five words."])))
