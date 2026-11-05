"""Connect the two typography editors through the existing account settings store."""

from collections.abc import Callable

from interview_assistant.config.user_settings import UserSettingsStore
from interview_assistant.ui.answer_typography import AnswerTypography
from interview_assistant.ui.overlay import OverlayWindow
from interview_assistant.ui.settings_dialog import SettingsDialog


def bind_answer_appearance(
    overlay: OverlayWindow,
    dialog: SettingsDialog,
    store: UserSettingsStore,
    active: Callable[[], bool],
    can_edit: Callable[[], bool],
) -> None:
    def apply_saved(typography: AnswerTypography) -> None:
        if active():
            overlay.set_typography(typography)

    def save_inline(typography: AnswerTypography) -> None:
        previous = overlay.answer.typography
        if not can_edit():
            overlay.font_controls.set_typography(previous)
            return
        defaults = AnswerTypography()
        changes = {
            f"answer_font_{name}": value if value != getattr(defaults, name) else None
            for name, value in (
                ("family", typography.family),
                ("weight", typography.weight),
                ("size", typography.size),
            )
        }
        try:
            store.update(changes)
        except (OSError, ValueError, RuntimeError):
            overlay.font_controls.set_typography(previous)
            overlay.font_controls.show_error("Font change not saved. Please try again.")
            return
        if active():
            overlay.set_typography(typography)
            dialog.set_saved_typography(typography)

    dialog.typography_saved.connect(apply_saved)
    overlay.font_controls.changed.connect(save_inline)
