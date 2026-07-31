"""PDF text extraction via PyMuPDF. No OCR — text-based PDFs only."""

import fitz  # PyMuPDF


def extract_text(filepath: str) -> str:
    """Extract all text from a PDF. Returns empty string on failure or if no text found."""
    try:
        doc = fitz.open(filepath)
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()
        return text.strip()
    except Exception:
        return ""


def extract_text_with_pages(filepath: str) -> list[tuple[int, str]]:
    """Extract text page by page. Returns list of (page_number, text)."""
    try:
        doc = fitz.open(filepath)
        pages = []
        for i, page in enumerate(doc):
            text = page.get_text().strip()
            if text:
                pages.append((i + 1, text))
        doc.close()
        return pages
    except Exception:
        return []
