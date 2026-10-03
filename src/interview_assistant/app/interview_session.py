"""Activating a "Create Interview Session" (Step 17) against the running app.

Mutates the live Jev config the same way privacy/state.py's PrivacyState already mutates
`redact` live - no restart needed - and tags new history turns with the session via
HistoryStore.set_active_interview_session(). The dialog that collects SessionDetails
(ui/session_dialog.py) stays free of this wiring so it's trivially testable on its own.
"""

from dataclasses import dataclass

from interview_assistant.agents.jev import JevConfig
from interview_assistant.history.store import HistoryStore, InterviewSession


@dataclass
class SessionDetails:
    """What "Create Interview Session" collects, before it becomes a stored InterviewSession
    (which additionally has an id/created_at/ended_at)."""

    company: str = ""
    role_title: str = ""
    job_description: str = ""
    resume_override: str = ""  # empty = fall back to the global candidate_profile
    ai_instructions: str = ""
    answer_format: str = ""
    answer_length: str = ""
    answer_tone: str = ""
    model_route_override: str = ""


class InterviewSessionManager:
    """Owns at most one active interview session at a time; activating a new one ends whichever
    was active first."""

    def __init__(
        self, jev_config: JevConfig, history: HistoryStore, base_profile: str | None
    ) -> None:
        self._config = jev_config
        self._history = history
        self._base_profile = base_profile  # the global candidate_profile, before any override
        self.current: InterviewSession | None = None

    def activate(self, details: SessionDetails) -> InterviewSession:
        self.end_active()
        session_id = self._history.create_interview_session(
            company=details.company,
            role_title=details.role_title,
            job_description=details.job_description,
            resume_override=details.resume_override,
            ai_instructions=details.ai_instructions,
            answer_format=details.answer_format,
            answer_length=details.answer_length,
            answer_tone=details.answer_tone,
            model_route_override=details.model_route_override,
        )
        session = self._history.get_interview_session(session_id)
        assert session is not None  # just inserted, under the same lock
        self.current = session
        self._history.set_active_interview_session(session_id)
        self._config.profile = details.resume_override.strip() or self._base_profile
        self._config.company = details.company
        self._config.job_description = details.job_description
        self._config.instructions = details.ai_instructions
        self._config.answer_format = details.answer_format
        self._config.answer_length = details.answer_length
        self._config.answer_tone = details.answer_tone
        return session

    def end_active(self) -> None:
        """Ends the current session (if any) and reverts Jev's config to the plain global
        profile - safe to call even when nothing is active."""
        if self.current is not None:
            self._history.end_interview_session(self.current.id)
            self.current = None
        self._history.set_active_interview_session(None)
        self._config.profile = self._base_profile
        self._config.company = ""
        self._config.job_description = ""
        self._config.instructions = ""
        self._config.answer_format = ""
        self._config.answer_length = ""
        self._config.answer_tone = ""
