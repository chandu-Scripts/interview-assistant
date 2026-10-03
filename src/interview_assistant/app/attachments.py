from pathlib import Path

ATTACHMENT_FILTER = "Documents (*.pdf *.docx *.txt *.md)"
# Attached text is sent with every answer of the session, so cap it to keep per-answer cost bounded.
MAX_CHARS = 12000


class AttachmentError(ValueError):
    pass


def read_attachment(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        if suffix in {".txt", ".md"}:
            text = path.read_text(encoding="utf-8", errors="replace")
        elif suffix == ".pdf":
            from pypdf import PdfReader

            text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
        elif suffix == ".docx":
            import docx

            text = "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
        else:
            raise AttachmentError(
                f"Unsupported file type {suffix or '(none)'}: use PDF, DOCX, TXT or MD"
            )
    except AttachmentError:
        raise
    except Exception as e:  # parsing untrusted user files can fail in many library-specific ways
        raise AttachmentError(f"Could not read {path.name}: {e}") from e
    text = text.strip()
    if not text:
        raise AttachmentError(f"{path.name} has no readable text")
    return text[:MAX_CHARS]
