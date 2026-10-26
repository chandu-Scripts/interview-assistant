from pathlib import Path

ATTACHMENT_FILTER = "Documents (*.pdf *.docx *.txt *.md)"
# Attached text is sent with every answer of the session, so cap it to keep per-answer cost bounded.
MAX_CHARS = 12000


class AttachmentError(ValueError):
    pass


def _read_docx(path: Path) -> str:
    from collections.abc import Iterator

    import docx
    from docx.blkcntnr import BlockItemContainer
    from docx.document import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    def content(container: Document | BlockItemContainer) -> Iterator[str]:
        for block in container.iter_inner_content():
            if isinstance(block, Paragraph):
                if block.text.strip():
                    yield block.text
            elif isinstance(block, Table):
                seen: set[object] = set()
                for row in block.rows:
                    for cell in row.cells:
                        # Merged cells are exposed more than once by python-docx.
                        if cell._tc not in seen:
                            seen.add(cell._tc)
                            yield from content(cell)

    document = docx.Document(str(path))
    headers: list[str] = []
    footers: list[str] = []
    seen_parts: set[object] = set()
    for section in document.sections:
        for part, destination in (
            (section.header, headers),
            (section.first_page_header, headers),
            (section.even_page_header, headers),
            (section.footer, footers),
            (section.first_page_footer, footers),
            (section.even_page_footer, footers),
        ):
            if part._element not in seen_parts:
                seen_parts.add(part._element)
                destination.extend(content(part))
    return "\n".join([*headers, *content(document), *footers])


def read_attachment(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        if suffix in {".txt", ".md"}:
            text = path.read_text(encoding="utf-8", errors="replace")
        elif suffix == ".pdf":
            from pypdf import PdfReader

            text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
        elif suffix == ".docx":
            text = _read_docx(path)
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
        raise AttachmentError(
            f"{path.name} has no readable text. Use a text-based PDF, DOCX or TXT file; "
            "scanned images need text recognition first."
        )
    return text[:MAX_CHARS]
