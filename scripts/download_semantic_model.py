"""Explicit one-time download; the desktop matcher itself never uses the network."""

from pathlib import Path

import httpx

REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
BASE = f"https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/{REVISION}"
DEST = Path(__file__).resolve().parents[1] / "src/interview_assistant/assets/semantic"


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    with httpx.Client(follow_redirects=True, timeout=60) as client:
        for remote, local in [
            ("onnx/model.onnx", "model.onnx"),
            ("tokenizer.json", "tokenizer.json"),
            ("README.md", "MODEL_CARD.md"),
        ]:
            target = DEST / local
            if target.exists():
                continue
            temporary = target.with_suffix(target.suffix + ".download")
            with client.stream("GET", f"{BASE}/{remote}") as response:
                response.raise_for_status()
                with temporary.open("wb") as stream:
                    for chunk in response.iter_bytes():
                        stream.write(chunk)
            temporary.replace(target)
            print(f"Downloaded {local}: {target.stat().st_size} bytes", flush=True)
    (DEST / "REVISION").write_text(REVISION, encoding="utf-8")


if __name__ == "__main__":
    main()
