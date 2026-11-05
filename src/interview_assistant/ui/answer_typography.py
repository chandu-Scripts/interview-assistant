"""Answer-only typography, shared by the preview and live renderer."""

from dataclasses import dataclass

from PySide6.QtGui import QFont

FAMILIES = ("Segoe UI", "Arial", "Calibri", "Consolas")
WEIGHTS = {"Regular": 400, "Medium": 500, "Bold": 700}
SIZES = {"Small": 10, "Medium": 11, "Large": 14}
FIELDS = frozenset({"answer_font_family", "answer_font_weight", "answer_font_size"})


@dataclass(frozen=True)
class AnswerTypography:
    family: str = "Segoe UI"
    weight: str = "Regular"
    size: str = "Medium"

    def __post_init__(self) -> None:
        if self.family not in FAMILIES or self.weight not in WEIGHTS or self.size not in SIZES:
            raise ValueError("Unsupported answer typography")

    def font(self, code: bool) -> QFont:
        font = QFont("Consolas" if code else self.family, SIZES[self.size])
        font.setWeight(QFont.Weight.Normal if code else QFont.Weight(WEIGHTS[self.weight]))
        font.setFixedPitch(code or self.family == "Consolas")
        return font

    def stylesheet(self, code: bool) -> str:
        family = "Consolas" if code else self.family
        weight = 400 if code else WEIGHTS[self.weight]
        # Local QSS wins over the workspace/overlay's global QWidget font rules.
        return (
            f"QLabel {{ font-family: '{family}'; font-size: {SIZES[self.size]}pt; "
            f"font-weight: {weight}; }}"
        )
