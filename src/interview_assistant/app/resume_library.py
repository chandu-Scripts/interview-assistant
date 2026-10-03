import json
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Resume:
    name: str
    text: str
    last_used: float


class ResumeLibrary:
    """Resumes attached before, kept in a small JSON file so the session dialog can offer them
    again. Attaching a file with the same name replaces the earlier copy."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def list(self) -> list[Resume]:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []  # a damaged library is treated as empty rather than blocking the app
        resumes = [Resume(r["name"], r["text"], float(r["last_used"])) for r in raw]
        return sorted(resumes, key=lambda r: r.last_used, reverse=True)

    def add(self, name: str, text: str) -> Resume:
        resumes = [r for r in self.list() if r.name != name]
        resume = Resume(name, text, time.time())
        resumes.append(resume)
        self._save(resumes)
        return resume

    def touch(self, name: str) -> None:
        self._save(
            [Resume(r.name, r.text, time.time()) if r.name == name else r for r in self.list()]
        )

    def _save(self, resumes: list[Resume]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = [{"name": r.name, "text": r.text, "last_used": r.last_used} for r in resumes]
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
