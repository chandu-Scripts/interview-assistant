"""Streaming paragraph cards with independent, plain-text clipboard actions."""

import re
from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QTextDocument
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True)
class AnswerBlock:
    text: str
    code: bool = False


def split_blocks(text: str, markdown: bool) -> list[AnswerBlock]:
    """Keep code intact, including incomplete streamed fences; split prose paragraphs."""
    result = []
    buffer: list[str] = []
    fence = ""
    language = ""

    def flush(code: bool = False) -> None:
        content = "\n".join(buffer)
        buffer.clear()
        if not content.strip():
            return
        if code:
            result.append(AnswerBlock(content, True))
            return
        for paragraph in re.split(r"\n\s*\n", content.strip()):
            if markdown and not fence:
                doc = QTextDocument()
                doc.setMarkdown(paragraph)
                paragraph = doc.toPlainText()
            if paragraph.strip():
                result.append(AnswerBlock(paragraph))

    for line in text.splitlines():
        match = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line) if markdown else None
        if fence:
            if (
                match
                and match[1][0] == fence[0]
                and len(match[1]) >= len(fence)
                and not match[2].strip()
            ):
                flush(language not in ("text", "plaintext"))
                fence = ""
            else:
                buffer.append(line)
        elif match:
            flush()
            fence, language = match[1], match[2].strip().lower()
        else:
            buffer.append(line)
    flush(bool(fence) and language not in ("text", "plaintext"))
    return result


class ParagraphCard(QFrame):
    def __init__(self, number: int, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("answerParagraph")
        self.setStyleSheet(
            "QFrame#answerParagraph { background: rgba(67, 106, 133, 55); "
            "border: 1px solid rgba(150, 210, 230, 40); border-radius: 12px; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 14)
        header = QHBoxLayout()
        header.addStretch()
        self.copy_btn = QPushButton("⧉")
        self.copy_btn.setAccessibleName(f"Copy paragraph {number}")
        self.copy_btn.setToolTip("Copy this paragraph only")
        self.copy_btn.setFixedSize(34, 28)
        header.addWidget(self.copy_btn)
        layout.addLayout(header)
        self.label = QLabel()
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.label)
        self._text = ""
        self._reset = QTimer(self)
        self._reset.setSingleShot(True)
        self._reset.timeout.connect(lambda: self.copy_btn.setText("⧉"))
        self.copy_btn.clicked.connect(self.copy)

    def update_block(self, block: AnswerBlock) -> None:
        if self._text == block.text and self.label.font().fixedPitch() == block.code:
            return
        self._text = block.text
        font = QFont("Consolas" if block.code else "Segoe UI", 11)
        font.setFixedPitch(block.code)
        self.label.setFont(font)
        self.label.setText(block.text)
        self.copy_btn.setToolTip(
            "Copy this code block" if block.code else "Copy this paragraph only"
        )

    def copy(self) -> None:
        QApplication.clipboard().setText(self._text)
        self.copy_btn.setText("✓")
        self._reset.start(1200)


class AnswerView(QScrollArea):
    """QTextBrowser-compatible answer surface; buttons are real widgets, not text links."""

    def __init__(self) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._content = QWidget()
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(0, 0, 4, 0)
        self._layout.setSpacing(10)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._placeholder = QLabel()
        self._placeholder.setWordWrap(True)
        self._layout.addWidget(self._placeholder)
        self.setWidget(self._content)
        self.cards: list[ParagraphCard] = []
        self._document = QTextDocument(self)
        self._raw = ""
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.timeout.connect(self._scroll_to_end)

    def setPlaceholderText(self, text: str) -> None:  # noqa: N802
        self._placeholder.setText(text)

    def setPlainText(self, text: str) -> None:  # noqa: N802
        self._document.setPlainText(text)
        self._set(text, False)

    def setMarkdown(self, text: str) -> None:  # noqa: N802
        self._document.setMarkdown(text)
        self._set(text, True)

    def toPlainText(self) -> str:  # noqa: N802
        return self._document.toPlainText()

    def clear(self) -> None:
        self.setPlainText("")

    def append(self, text: str) -> None:
        self.setPlainText(self.toPlainText() + "\n" + text)

    def _set(self, text: str, markdown: bool) -> None:
        self._raw = text
        bar = self.verticalScrollBar()
        follow = bar.value() >= bar.maximum() - 4
        blocks = split_blocks(text, markdown)
        while len(self.cards) > len(blocks):
            card = self.cards.pop()
            self._layout.removeWidget(card)
            card.hide()
            card.deleteLater()
        for i, block in enumerate(blocks):
            if i == len(self.cards):
                card = ParagraphCard(i + 1, self._content)
                self.cards.append(card)
                self._layout.addWidget(card)
            self.cards[i].update_block(block)
        self._placeholder.setVisible(not blocks)
        self._layout.activate()
        if follow:
            self._scroll_timer.start(0)

    def _scroll_to_end(self) -> None:
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
