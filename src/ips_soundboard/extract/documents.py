"""Text of an IPS document (Markdown, plain text or PDF), from a file or an upload."""

from __future__ import annotations

import io
from pathlib import Path

DOCUMENT_TYPES = ("md", "txt", "pdf")


class DocumentError(ValueError):
    """The document cannot be read as text."""


def document_text(filename: str, data: bytes) -> str:
    """Text of the document. PDFs need pypdf; scanned PDFs (images only) have no text to read."""
    if filename.lower().endswith(".pdf"):
        try:
            from pypdf import PdfReader
        except ImportError as error:
            raise DocumentError('reading PDFs needs pypdf: pip install -e ".[llm]"') from error
        text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
        if not text.strip():
            raise DocumentError("this PDF has no text layer (a scan?): save it as text or Markdown instead")
        return text
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise DocumentError("the file is not UTF-8 text: save it as .txt or .md (UTF-8)") from error


def read_document(path: str | Path) -> str:
    path = Path(path)
    return document_text(path.name, path.read_bytes())
