"""The intentionally small, bounded request contract accepted by the relay."""

import base64
import binascii
import math
import struct
import zlib
from typing import Any

from fastapi import HTTPException
from interview_assistant.cost.pricing import PriceBook, TokenPrice

from interview_relay.config import RelaySettings

MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_TEXT_BYTES = 64 * 1024
MAX_PNG_BYTES = 4 * 1024 * 1024
MAX_IMAGES = 4
# Conservative maxima, all supported detail levels, verified 2026-11-01 against:
# https://developers.openai.com/api/docs/guides/images-vision#calculating-costs
# Tiles fit at most 2048 x 768 (8 tiles); 5.4-mini's largest budget is 10,000 patches.
IMAGE_TOKEN_BOUNDS = {
    "gpt-4o": 85 + 8 * 170,
    "gpt-4o-mini": 2833 + 8 * 5667,
    "gpt-5.4-mini": 12001,  # ceil(10,000 * 1.2), plus documented rounding tolerance
}


def invalid(detail: str = "Unsupported or malformed request body") -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


def price_for(model: str) -> TokenPrice:
    # Do not let prefix matching accidentally price a new audio/realtime/specialized variant.
    if model not in IMAGE_TOKEN_BOUNDS:
        raise HTTPException(503, "Relay model has no supported pricing contract")
    price = PriceBook().token_price("openai", model)
    if price is None or not all(
        math.isfinite(rate) and rate > 0 for rate in (price.input_per_m, price.output_per_m)
    ):
        raise HTTPException(503, "Relay model pricing is unavailable")
    return price


def png_dimensions(url: str) -> tuple[int, int]:
    prefix = "data:image/png;base64,"
    if not url.startswith(prefix) or len(url) > len(prefix) + ((MAX_PNG_BYTES + 2) // 3) * 4:
        raise invalid("Images must be inline PNG files no larger than 4 MiB")
    try:
        raw = base64.b64decode(url[len(prefix) :], validate=True)
    except (ValueError, binascii.Error):
        raise invalid("Invalid PNG encoding") from None
    if len(raw) > MAX_PNG_BYTES or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise invalid("Invalid PNG file")
    pos, width, height = 8, 0, 0
    seen_header = seen_data = seen_end = False
    while pos + 12 <= len(raw):
        length = struct.unpack_from(">I", raw, pos)[0]
        kind = raw[pos + 4 : pos + 8]
        end = pos + 12 + length
        if end > len(raw):
            raise invalid("Truncated PNG file")
        chunk = raw[pos + 8 : pos + 8 + length]
        crc = struct.unpack_from(">I", raw, pos + 8 + length)[0]
        if zlib.crc32(kind + chunk) & 0xFFFFFFFF != crc:
            raise invalid("Invalid PNG checksum")
        if not seen_header and kind != b"IHDR":
            raise invalid("Invalid PNG header")
        if kind == b"IHDR":
            if seen_header or length != 13:
                raise invalid("Invalid PNG header")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", chunk
            )
            allowed_depths = {
                0: {1, 2, 4, 8, 16},
                2: {8, 16},
                3: {1, 2, 4, 8},
                4: {8, 16},
                6: {8, 16},
            }
            if (
                not 1 <= width <= 8192
                or not 1 <= height <= 8192
                or width * height > 16_777_216
                or depth not in allowed_depths.get(color, set())
                or compression != 0
                or filtering != 0
                or interlace not in {0, 1}
            ):
                raise invalid("PNG dimensions or format are unsupported")
            seen_header = True
        elif kind == b"IDAT":
            seen_data = True
        elif kind == b"IEND":
            if length != 0 or end != len(raw) or not seen_data:
                raise invalid("Invalid PNG ending")
            seen_end = True
        elif kind in {b"acTL", b"fcTL", b"fdAT"}:
            raise invalid("Animated PNG images are unsupported")
        elif kind != b"PLTE" and (not kind.isalpha() or kind[:1].isupper()):
            raise invalid("Unsupported PNG chunk")
        pos = end
    if not seen_end or pos != len(raw):
        raise invalid("Incomplete PNG file")
    return width, height


def sanitize_body(settings: RelaySettings, body: Any) -> tuple[dict[str, Any], int, TokenPrice]:
    """Validate before spending or contacting the provider; return an input-token ceiling."""
    allowed = {
        "model",
        "messages",
        "stream",
        "stream_options",
        "max_completion_tokens",
        "temperature",
        "reasoning_effort",
        "n",
    }
    if not isinstance(body, dict) or set(body) - allowed:
        raise invalid()
    if "n" in body and (type(body["n"]) is not int or body["n"] != 1):
        raise invalid("Only one completion is supported")
    tier = body.get("model")
    if not isinstance(tier, str) or tier not in {"fast", "smart"}:
        raise invalid("model must be 'fast' or 'smart'")
    model = settings.model_fast if tier == "fast" else settings.model_smart
    price = price_for(model)
    if (
        not 1 <= settings.max_tokens_ceiling <= 8192
        or not 1 <= settings.max_inflight_per_friend <= 8
    ):
        raise HTTPException(503, "Relay request limits are invalid")
    requested = body.get("max_completion_tokens", settings.max_tokens_ceiling)
    if type(requested) is not int or requested <= 0:
        raise invalid("max_completion_tokens must be a positive integer")
    messages = body.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 128:
        raise invalid("messages must be a non-empty list of at most 128 messages")
    clean_messages: list[dict[str, Any]] = []
    text_bytes, images, input_bound = 0, 0, 128

    def text_size(text: Any) -> int:
        if not isinstance(text, str):
            raise invalid("Message text must be a string")
        try:
            return len(text.encode("utf-8"))
        except UnicodeError:
            raise invalid("Message text must be valid UTF-8") from None

    for message in messages:
        if not isinstance(message, dict) or set(message) != {"role", "content"}:
            raise invalid("Messages accept only role and content")
        role, content = message["role"], message["content"]
        if not isinstance(role, str) or role not in {"system", "developer", "user", "assistant"}:
            raise invalid("Unsupported message role")
        input_bound += 128  # More than known message/role framing for supported models.
        if isinstance(content, str):
            text_bytes += text_size(content)
            clean_content: Any = content
        elif isinstance(content, list) and 1 <= len(content) <= 16:
            clean_content = []
            for part in content:
                if not isinstance(part, dict):
                    raise invalid("Invalid content part")
                if part.get("type") == "text" and set(part) == {"type", "text"}:
                    text_bytes += text_size(part["text"])
                    clean_content.append({"type": "text", "text": part["text"]})
                elif part.get("type") == "image_url" and set(part) == {"type", "image_url"}:
                    image = part["image_url"]
                    if (
                        role != "user"
                        or not isinstance(image, dict)
                        or set(image) - {"url", "detail"}
                    ):
                        raise invalid("Invalid image content")
                    if not isinstance(image.get("url"), str):
                        raise invalid("Invalid image URL")
                    levels = {"low", "high", "auto"}
                    if model == "gpt-5.4-mini":
                        levels.add("original")
                    if (
                        not isinstance(image.get("detail", "auto"), str)
                        or image.get("detail", "auto") not in levels
                    ):
                        raise invalid("Unsupported image detail")
                    png_dimensions(image["url"])
                    images += 1
                    input_bound += IMAGE_TOKEN_BOUNDS[model]
                    clean_content.append({"type": "image_url", "image_url": dict(image)})
                else:
                    raise invalid("Unsupported content part")
                input_bound += 32
        else:
            raise invalid("Message content must be text or supported content parts")
        if text_bytes > MAX_TEXT_BYTES or images > MAX_IMAGES:
            raise invalid("Request exceeds the text or image limit")
        clean_messages.append({"role": role, "content": clean_content})
    outgoing: dict[str, Any] = {
        "model": model,
        "messages": clean_messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_completion_tokens": min(requested, settings.max_tokens_ceiling),
    }
    if "temperature" in body:
        temp = body["temperature"]
        if (
            isinstance(temp, bool)
            or not isinstance(temp, int | float)
            or not math.isfinite(temp)
            or not 0 <= temp <= 2
        ):
            raise invalid("temperature must be a finite number between 0 and 2")
        outgoing["temperature"] = temp
    if "reasoning_effort" in body:
        effort = body["reasoning_effort"]
        if (
            model != "gpt-5.4-mini"
            or not isinstance(effort, str)
            or effort not in {"none", "minimal", "low", "medium", "high", "xhigh"}
        ):
            raise invalid("Unsupported reasoning effort")
        outgoing["reasoning_effort"] = effort
    return outgoing, input_bound + text_bytes, price
