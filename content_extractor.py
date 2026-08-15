"""PDF text extraction via PyMuPDF. No OCR — text-based PDFs only."""

import os
import fitz  # PyMuPDF


def _suppress_stderr():
    """Context manager that redirects stderr to devnull to silence MuPDF warnings."""
    class _Suppress:
        def __enter__(self):
            self._fd = os.open(os.devnull, os.O_WRONLY)
            self._saved = os.dup(2)
            os.dup2(self._fd, 2)
        def __exit__(self, *args):
            os.dup2(self._saved, 2)
            os.close(self._fd)
            os.close(self._saved)
    return _Suppress()


def extract_text(filepath: str) -> str:
    """Extract all text from a PDF. Skips corrupt pages gracefully.
    Returns empty string if the file cannot be opened or has no text."""
    try:
        with _suppress_stderr():
            doc = fitz.open(filepath)
    except Exception:
        return ""

    parts = []
    try:
        for page in doc:
            try:
                page_text = page.get_text()
                if page_text:
                    parts.append(page_text)
            except Exception:
                continue
    finally:
        doc.close()

    return "".join(parts).strip()


def extract_text_with_pages(filepath: str) -> list[tuple[int, str]]:
    """Extract text page by page. Returns list of (page_number, text)."""
    try:
        with _suppress_stderr():
            doc = fitz.open(filepath)
    except Exception:
        return []

    pages = []
    try:
        for i, page in enumerate(doc):
            try:
                text = page.get_text().strip()
                if text:
                    pages.append((i + 1, text))
            except Exception:
                continue
    finally:
        doc.close()

    return pages
