"""Compact inline typography controls for the live answer panel."""

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtGui import QFontDatabase, QHideEvent
from PySide6.QtWidgets import QGridLayout, QLabel, QWidget

from interview_assistant.ui.answer_typography import FAMILIES, SIZES, WEIGHTS, AnswerTypography
from interview_assistant.ui.controls import ChoiceComboBox


class AnswerFontControls(QWidget):
    changed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.family, self.weight, self.font_size = (ChoiceComboBox() for _ in range(3))
        available = set(QFontDatabase.families())
        for family in FAMILIES:
            self.family.addItem(family if family in available else f"{family} (fallback)", family)
        for weight in WEIGHTS:
            self.weight.addItem(weight, weight)
        for size, points in SIZES.items():
            self.font_size.addItem(f"{size} ({points} pt)", size)
        self.combos = (self.family, self.weight, self.font_size)
        for column, (label, combo) in enumerate(
            zip(("Font family", "Font style", "Font size"), self.combos, strict=True)
        ):
            combo.setAccessibleName(label)
            combo.setMinimumWidth(0)
            layout.addWidget(QLabel(label), 0, column)
            layout.addWidget(combo, 1, column)
            layout.setColumnStretch(column, 1)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.hide()
        layout.addWidget(self.message, 2, 0, 1, 3)
        self.set_typography(AnswerTypography())
        for combo in self.combos:
            combo.currentIndexChanged.connect(self._changed)

    def set_typography(self, typography: AnswerTypography) -> None:
        for combo, value in zip(
            self.combos, (typography.family, typography.weight, typography.size), strict=True
        ):
            with QSignalBlocker(combo):
                combo.setCurrentIndex(combo.findData(value))
        self.message.clear()
        self.message.hide()

    def _changed(self) -> None:
        self.changed.emit(
            AnswerTypography(
                self.family.currentData(), self.weight.currentData(), self.font_size.currentData()
            )
        )

    def show_error(self, message: str) -> None:
        self.message.setText(message)
        self.message.show()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        for combo in self.combos:
            combo.hidePopup()
        super().hideEvent(event)
