"""Stream a prompt from a provider. Usage: python scripts/llm_chat.py <provider> <prompt>"""

import asyncio
import sys

from interview_assistant.config.settings import Settings
from interview_assistant.llm.factory import build_providers
from interview_assistant.llm.types import LLMError, LLMRequest, Message, Usage


async def main(name: str, prompt: str) -> None:
    providers = build_providers(Settings())
    if name not in providers:
        sys.exit(f"provider {name!r} not configured (available: {sorted(providers)})")
    usage = Usage()
    req = LLMRequest([Message("user", prompt)], max_tokens=400)
    try:
        async for ev in providers[name].stream(req):
            print(ev.delta, end="", flush=True)
            usage = ev.usage or usage
    except LLMError as e:
        sys.exit(f"\nerror (retryable={e.retryable}): {str(e)[:200]}")
    print(f"\n[usage in={usage.input_tokens} out={usage.output_tokens}]")


asyncio.run(main(sys.argv[1], sys.argv[2]))
