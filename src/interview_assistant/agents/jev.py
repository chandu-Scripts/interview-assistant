import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass, replace

from interview_assistant.agents.classifier import Classifier
from interview_assistant.agents.specialists import SPECIALISTS, system_prompt
from interview_assistant.agents.types import Category, Decision, Turn
from interview_assistant.llm.router import RoutedResult, RouteEvent, Router, collect_routed
from interview_assistant.llm.types import LLMRequest, Message
from interview_assistant.privacy.redact import redact_text

log = logging.getLogger(__name__)

JevEvent = Decision | RouteEvent


@dataclass
class JevConfig:
    profile: str | None = None  # candidate background (resume summary, target role)
    history_turns: int = 3  # previous Q/A pairs sent as context
    history_chars: int = 3000  # hard cap on context size
    classify_timeout_s: float = 2.5
    redact: bool = False  # mask PII/secrets in everything sent to AI providers (togglable live)
    # Per-session context (Step 17's "Create Interview Session"); empty by default, mutated live
    # when a session is activated/ended - the same pattern already used above for `redact`
    # (see privacy/state.py's PrivacyState, and app/interview_session.py's InterviewSessionManager).
    company: str = ""
    job_description: str = ""
    instructions: str = ""
    answer_format: str = ""
    answer_length: str = ""
    answer_tone: str = ""
    language: str = "English"


class Jev:
    """Orchestrator: classify -> pick specialist -> stream the answer through the model router.

    ask() yields exactly one Decision first, then RouteEvents (honour RouteEvent.reset). A question
    that is not really a question yields only its Decision. Cancel the consuming task to
    interrupt (e.g. a new question arrives); an interrupted answer is not stored in memory.
    """

    def __init__(self, router: Router, config: JevConfig | None = None) -> None:
        self._router = router
        self.config = config or JevConfig()
        self.history: list[Turn] = []
        self._classifier = Classifier(self._complete, self.config.classify_timeout_s)

    def _outbound(self, request: LLMRequest) -> LLMRequest:
        """The request as it leaves this PC: optionally with PII and secrets masked."""
        if not self.config.redact:
            return request
        return replace(
            request,
            system=redact_text(request.system) if request.system else request.system,
            messages=[Message(m.role, redact_text(m.content)) for m in request.messages],
        )

    async def _complete(self, request: LLMRequest, tier: str) -> RoutedResult:
        return await collect_routed(self._router, self._outbound(request), tier, tag="classify")

    def clear_history(self) -> None:
        self.history.clear()

    async def decide(self, question: str) -> Decision:
        last = self.history[-1] if self.history else None
        return await self._classifier.classify(
            question, last.question if last else "", last.category if last else None
        )

    def manual_decision(self, category: Category) -> Decision:
        """A user-picked specialist, bypassing classification (the "wrong specialist?" fix)."""
        spec = SPECIALISTS[category]
        return Decision(category, spec.tier, 1.0, "user", spec.name)

    async def ask(
        self, question: str, decision: Decision | None = None, image: bytes | None = None
    ) -> AsyncGenerator[JevEvent, None]:
        """Pass a pre-computed `decision` (from decide()) to avoid classifying twice. `image` is
        PNG bytes sent with this question's answer request only (never with classification)."""
        decision = decision or await self.decide(question)
        yield decision
        if not decision.should_answer:
            return
        spec = SPECIALISTS[decision.category]
        request = LLMRequest(
            self._messages(question),
            system=system_prompt(
                spec,
                self.config.profile,
                company=self.config.company,
                job_description=self.config.job_description,
                instructions=self.config.instructions,
                answer_format=self.config.answer_format,
                answer_length=self.config.answer_length,
                answer_tone=self.config.answer_tone,
                language=self.config.language,
            ),
            max_tokens=spec.max_tokens,
            image=image,
        )
        parts: list[str] = []
        async for ev in self._router.stream(
            self._outbound(request), decision.tier, tag=decision.category.value
        ):
            if ev.reset:
                parts.clear()
            parts.append(ev.event.delta)
            yield ev
        self._remember(Turn(question, "".join(parts).strip(), decision.category))

    def _messages(self, question: str) -> list[Message]:
        turns: list[Turn] = []
        budget = self.config.history_chars
        for t in reversed(self.history[-self.config.history_turns :]):
            budget -= len(t.question) + len(t.answer)
            if budget < 0:
                break
            turns.append(t)
        msgs: list[Message] = []
        for t in reversed(turns):
            msgs += [Message("user", t.question), Message("assistant", t.answer)]
        msgs.append(Message("user", question))
        return msgs

    def _remember(self, turn: Turn) -> None:
        if turn.answer:
            self.history.append(turn)
            del self.history[: -max(self.config.history_turns * 3, 10)]


__all__ = ["Category", "Decision", "Jev", "JevConfig", "JevEvent"]
