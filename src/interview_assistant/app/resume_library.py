import builtins
import json
import math
import os
import tempfile
import time
from collections.abc import Sequence
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
        self.error: str | None = None

    def list(self) -> list[Resume]:
        try:
            resumes = self._read()
        except (OSError, ValueError) as e:
            self.error = str(e)
            return []
        self.error = None
        return resumes

    def _read(self) -> builtins.list[Resume]:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError()
            resumes = []
            for r in raw:
                if (
                    not isinstance(r, dict)
                    or not isinstance(r.get("name"), str)
                    or not isinstance(r.get("text"), str)
                ):
                    raise ValueError()
                stamp = float(r["last_used"])
                if not math.isfinite(stamp):
                    raise ValueError()
                resumes.append(Resume(r["name"], r["text"], stamp))
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise ValueError(
                "Cannot read the saved resume library. The original file has been preserved."
            ) from e
        return sorted(resumes, key=lambda r: r.last_used, reverse=True)

    def add(self, name: str, text: str) -> Resume:
        resumes = [r for r in self._read() if r.name != name]
        resume = Resume(name, text, time.time())
        resumes.append(resume)
        self._save(resumes)
        return resume

    def touch(self, name: str) -> None:
        self._save(
            [Resume(r.name, r.text, time.time()) if r.name == name else r for r in self._read()]
        )

    def rename(self, name: str, new_name: str) -> None:
        new_name = new_name.strip()
        resumes = self._read()
        if not new_name:
            raise ValueError("Enter a resume name.")
        if any(r.name != name and r.name.casefold() == new_name.casefold() for r in resumes):
            raise ValueError("A resume with that name already exists.")
        if not any(r.name == name for r in resumes):
            raise ValueError("This resume is no longer in the library.")
        self._save(
            [Resume(new_name, r.text, r.last_used) if r.name == name else r for r in resumes]
        )

    def delete(self, name: str) -> None:
        self._save([r for r in self._read() if r.name != name])

    def _save(self, resumes: Sequence[Resume]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = [{"name": r.name, "text": r.text, "last_used": r.last_used} for r in resumes]
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._path.parent,
                prefix=".resumes-",
                suffix=".tmp",
                delete=False,
            ) as f:
                temporary = Path(f.name)
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            temporary.replace(self._path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
