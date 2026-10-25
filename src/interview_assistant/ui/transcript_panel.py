from html import escape

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QTextEdit, QVBoxLayout, QWidget

from interview_assistant.stt.base import TranscriptEvent

MAX_UTTERANCES = 300


class TranscriptPanel(QWidget):
    """Live transcript: finalized utterances in normal text, the ongoing one in grey italics."""

    # Emitted from the STT worker thread; the queued connection delivers it on the UI thread.
    event_received = Signal(object)
    error_received = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._finals: dict[tuple[str, int], str] = {}
        self._partials: dict[tuple[str, int], str] = {}
        self.view = QTextEdit()
        self.view.setReadOnly(True)
        self.view.setPlaceholderText("Transcript will appear here…")
        self.status_label = QLabel("")
        layout = QVBoxLayout(self)
        layout.addWidget(self.view)
        layout.addWidget(self.status_label)
        self.event_received.connect(self.apply)
        self.error_received.connect(self.show_error)

    @property
    def final_texts(self) -> list[str]:
        return [t for t in self._finals.values() if t]

    def apply(self, ev: TranscriptEvent) -> None:
        key = (ev.source.value if ev.source is not None else "", ev.utterance_id)
        if ev.is_final:
            self._finals[key] = ev.text
            self._partials.pop(key, None)
            # Hours-long interviews: keep the view (and memory) bounded to the recent past.
            while len(self._finals) > MAX_UTTERANCES:
                del self._finals[next(iter(self._finals))]
        elif key not in self._finals:
            self._partials[key] = ev.text
            while len(self._partials) > MAX_UTTERANCES:
                del self._partials[next(iter(self._partials))]
        self.status_label.setText("")
        if ev.is_final and not ev.text.strip():
            self.status_label.setText(
                "Speech was detected, but no words were recognized. Try again."
            )
        self._render()

    def show_error(self, message: str) -> None:
        self.status_label.setText(f"Transcription error: {message}")

    def clear(self) -> None:
        self._finals.clear()
        self._partials.clear()
        self.view.clear()

    def _render(self) -> None:
        parts = [f"<p>{escape(t)}</p>" for t in self.final_texts]
        parts += [
            f"<p style='color:#888;font-style:italic'>{escape(t)}</p>"
            for _, t in sorted(self._partials.items())
        ]
        self.view.setHtml("".join(parts))
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())
