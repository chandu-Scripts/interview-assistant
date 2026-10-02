import re
from collections import Counter
from dataclasses import dataclass, field

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
_DIGIT_RUN = re.compile(r"(?<![\w])\+?\d[\d ().-]{7,}\d(?![\w])")
_SECRETS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),  # OpenAI-style
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"),  # Anthropic
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),  # Google
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),  # GitHub
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),  # Slack
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}"),
]


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


@dataclass
class RedactionResult:
    text: str
    counts: Counter[str] = field(default_factory=Counter)

    @property
    def changed(self) -> bool:
        return bool(self.counts)


def redact(text: str) -> RedactionResult:
    """Replace emails, phone numbers, card numbers, SSNs and API keys/tokens with placeholders.

    Conservative on purpose: plain numbers (years, quantities, "10,000,000 users") are left alone.
    """
    counts: Counter[str] = Counter()

    def sub(pattern: re.Pattern[str], label: str, s: str) -> str:
        def repl(_m: re.Match[str]) -> str:
            counts[label] += 1
            return f"[{label}]"

        return pattern.sub(repl, s)

    for pat in _SECRETS:
        text = sub(pat, "SECRET", text)
    text = sub(_EMAIL, "EMAIL", text)
    text = sub(_SSN, "SSN", text)

    def digits_repl(m: re.Match[str]) -> str:
        raw = m.group(0)
        digits = re.sub(r"\D", "", raw)
        if 13 <= len(digits) <= 19 and _luhn(digits):
            counts["CARD"] += 1
            return "[CARD]"
        # A phone number: 10-15 digits with phone-like separators, or a leading "+".
        separated = bool(re.search(r"[ ().-]", raw)) or raw.startswith("+")
        if 10 <= len(digits) <= 15 and separated:
            counts["PHONE"] += 1
            return "[PHONE]"
        return raw

    text = _DIGIT_RUN.sub(digits_repl, text)
    return RedactionResult(text, counts)


def redact_text(text: str) -> str:
    return redact(text).text
