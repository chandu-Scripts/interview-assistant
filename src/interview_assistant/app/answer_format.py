"""Local fence repair; never rewrites wording or calls a model."""

import re


def format_answer_blocks(text: str) -> str:
    blocks: list[str] = []
    prose: list[str] = []
    code: list[str] | None = None
    language = ""
    fence_char = ""
    fence_length = 0

    def wrap(content: str, lang: str = "text") -> str:
        # A longer fence preserves literal backticks inside code/answers.
        longest = max((len(m.group()) for m in re.finditer(r"`+", content)), default=0)
        fence = "`" * max(3, longest + 1)
        return f"{fence}{lang}\n{content}\n{fence}"

    def flush_prose() -> None:
        if prose:
            blocks.append(wrap("\n".join(prose).strip()))
            prose.clear()

    for line in text.strip().splitlines():
        match = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line)
        if code is not None:
            if (
                match
                and match[1][0] == fence_char
                and len(match[1]) >= fence_length
                and not match[2].strip()
            ):
                blocks.append(wrap("\n".join(code), language))
                code = None
            else:
                code.append(line)
        elif match:
            flush_prose()
            code = []
            fence_char, fence_length = match[1][0], len(match[1])
            language = match[2].strip()
        elif line.strip():
            prose.append(line)
        else:
            flush_prose()
    if code is not None:
        blocks.append(wrap("\n".join(code), language))
    flush_prose()
    return "\n\n".join(blocks)
