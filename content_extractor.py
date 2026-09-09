"""Document text extraction: PDF, Word, Excel and plain text. No OCR.

Dispatch is by file extension (see scanner.SUPPORTED_EXTENSIONS):

  .pdf          PyMuPDF (text-based PDFs only; scanned pages stay empty)
  .docx / .doc  python-docx when installed; otherwise a stdlib OOXML
                fallback (unzips word/document.xml) for .docx. Legacy
                binary .doc files need python-docx — without it they
                are skipped with a warning.
  .xlsx / .xls  openpyxl when installed (all sheets, values only);
                otherwise a stdlib OOXML fallback for .xlsx. Legacy
                binary .xls files need xlrd — without it they are
                skipped with a warning.
  .txt          read directly with encoding sniffing
                (UTF-8 → UTF-16 → Windows-1252).

Every extractor returns "" on failure so one bad file never breaks a
scan; failures are logged at WARN. Extracted text is capped at
MAX_TEXT_CHARS to keep giant files (logs, dumps) from bloating the DB.
"""

import os
import zipfile
import xml.etree.ElementTree as ET

import applog

try:
    from scanner import SUPPORTED_EXTENSIONS
except ImportError:  # standalone use without scanner on the path
    SUPPORTED_EXTENSIONS = frozenset({
        '.pdf', '.docx', '.doc', '.xlsx', '.xls', '.txt',
    })

# Upper bound on indexed text per file (~1 MB of text). The FTS5 index
# and snippets only need the readable content, not the whole dump.
MAX_TEXT_CHARS = 1_000_000
# Upper bound on raw bytes read for plain-text files (4 MB — decoding a
# huge log just to keep the first 1 M chars wastes time).
_MAX_TXT_BYTES = 4 * 1024 * 1024


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
    """Extract searchable text from any supported document.

    Returns empty string when the file cannot be opened, has no text,
    or is of an unsupported type."""
    ext = os.path.splitext(filepath)[1].lower()
    try:
        if ext == '.pdf':
            text = _extract_pdf(filepath)
        elif ext in ('.docx', '.doc'):
            text = _extract_word(filepath)
        elif ext in ('.xlsx', '.xls'):
            text = _extract_excel(filepath)
        elif ext == '.txt':
            text = _extract_txt(filepath)
        else:
            applog.log(f'Extract: unsupported type, skipping {filepath!r}',
                       level='WARN')
            return ""
    except Exception as e:
        applog.log(f'Extract: error reading {filepath!r}: {e}', level='WARN')
        return ""
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]
    return text.strip()


def extract_text_with_pages(filepath: str) -> list[tuple[int, str]]:
    """Extract text page/sheet by page. Returns list of (page_number, text).

    Only PDFs have real pages; every other type returns [(1, full_text)]
    (or [] when there is no text), so callers keep one code path."""
    ext = os.path.splitext(filepath)[1].lower()
    if ext == '.pdf':
        return _extract_pdf_pages(filepath)
    text = extract_text(filepath)
    return [(1, text)] if text else []


# ── PDF ──

def _extract_pdf(filepath: str) -> str:
    """Extract all text from a PDF. Skips corrupt pages gracefully."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        applog.log('Extract: PyMuPDF not installed, skipping '
                   f'{filepath!r}', level='WARN')
        return ""
    try:
        with _suppress_stderr():
            doc = fitz.open(filepath)
    except Exception as e:
        applog.log(f'Extract: cannot open {filepath!r}: {e}', level='WARN')
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
    except Exception as e:
        applog.log(f'Extract: error reading {filepath!r}: {e}', level='WARN')
    finally:
        doc.close()

    return "".join(parts)


def _extract_pdf_pages(filepath: str) -> list[tuple[int, str]]:
    """Extract PDF text page by page. Returns list of (page_number, text)."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return []
    try:
        with _suppress_stderr():
            doc = fitz.open(filepath)
    except Exception as e:
        applog.log(f'Extract: cannot open {filepath!r}: {e}', level='WARN')
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


# ── Plain text ──

def _extract_txt(filepath: str) -> str:
    """Read a plain-text file, sniffing the encoding.

    Tries UTF-8 (incl. BOM), then UTF-16 for files with lots of NUL
    bytes, then Windows-1252 (never fails — covers ANSI Notepad files).
    Binary files misnamed .txt are detected and skipped."""
    try:
        with open(filepath, 'rb') as f:
            raw = f.read(_MAX_TXT_BYTES + 1)
    except OSError as e:
        applog.log(f'Extract: cannot open {filepath!r}: {e}', level='WARN')
        return ""
    if not raw:
        return ""
    raw = raw[:_MAX_TXT_BYTES]

    if raw.count(b'\x00') > len(raw) // 4:
        # Likely UTF-16 (or binary). Try UTF-16 variants first.
        for enc in ('utf-16', 'utf-16-le', 'utf-16-be'):
            try:
                text = raw.decode(enc)
                if _looks_like_text(text):
                    return text
            except Exception:
                continue
        applog.log(f'Extract: binary file misnamed .txt, skipping '
                   f'{filepath!r}', level='WARN')
        return ""
    try:
        return raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode('cp1252')
    except Exception:
        return raw.decode('utf-8', errors='replace')


def _looks_like_text(text, sample=2000):
    """Heuristic: mostly printable chars → real text, not binary."""
    chunk = text[:sample]
    if not chunk:
        return False
    printable = sum(1 for ch in chunk if ch.isprintable() or ch in '\n\r\t')
    return printable / len(chunk) > 0.7


# ── Word ──

def _extract_word(filepath: str) -> str:
    """Extract text from .docx/.doc (paragraphs + tables)."""
    if not os.path.exists(filepath):
        applog.log(f'Extract: cannot open {filepath!r}: file not found',
                   level='WARN')
        return ""
    try:
        import docx  # python-docx
    except ImportError:
        docx = None
    if docx is not None:
        try:
            doc = docx.Document(filepath)
            parts = [p.text for p in doc.paragraphs if p.text]
            for table in doc.tables:
                for row in table.rows:
                    for cell in row.cells:
                        if cell.text:
                            parts.append(cell.text)
            text = '\n'.join(parts)
            if text.strip():
                return text
            # Empty via python-docx (e.g. text only in headers) — fall
            # through to the OOXML fallback which reads raw w:t nodes.
        except Exception as e:
            applog.log(f'Extract: python-docx failed for {filepath!r}: {e}',
                       level='WARN')
    else:
        applog.log('Extract: python-docx not installed, using OOXML '
                   'fallback', level='WARN')
    if not zipfile.is_zipfile(filepath):
        # Legacy binary .doc (OLE) — only python-docx (above) can read it.
        applog.log(f'Extract: legacy .doc without python-docx support, '
                   f'skipping {filepath!r}', level='WARN')
        return ""
    return _extract_docx_xml(filepath)


_W_NS = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def _extract_docx_xml(filepath: str) -> str:
    """Stdlib .docx fallback: concatenate every <w:t> node, grouped by
    paragraph so line breaks survive."""
    try:
        with zipfile.ZipFile(filepath) as z:
            try:
                xml_bytes = z.read('word/document.xml')
            except KeyError:
                return ""
    except Exception as e:
        applog.log(f'Extract: cannot unzip {filepath!r}: {e}', level='WARN')
        return ""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        applog.log(f'Extract: bad document.xml in {filepath!r}: {e}',
                   level='WARN')
        return ""
    paras = []
    for p in root.iter(_W_NS + 'p'):
        text = ''.join(n.text or '' for n in p.iter(_W_NS + 't'))
        if text:
            paras.append(text)
    if paras:
        return '\n'.join(paras)
    # No paragraphs (unusual) — flat list of text nodes.
    return ' '.join(n.text for n in root.iter(_W_NS + 't') if n.text)


# ── Excel ──

def _extract_excel(filepath: str) -> str:
    """Extract text from .xlsx/.xls (all sheets, values only)."""
    if not os.path.exists(filepath):
        applog.log(f'Extract: cannot open {filepath!r}: file not found',
                   level='WARN')
        return ""
    ext = os.path.splitext(filepath)[1].lower()
    if ext == '.xls' and not zipfile.is_zipfile(filepath):
        return _extract_xls_legacy(filepath)
    # .xlsx (or an .xls that is really OOXML) —
    # prefer openpyxl, fall back to stdlib XML.
    try:
        import openpyxl
    except ImportError:
        openpyxl = None
    if openpyxl is not None:
        try:
            wb = openpyxl.load_workbook(filepath, read_only=True,
                                        data_only=True)
            try:
                parts = []
                for ws in wb.worksheets:
                    parts.append(ws.title)
                    for row in ws.iter_rows(values_only=True):
                        line = ' '.join(str(c) for c in row
                                        if c is not None and c != '')
                        if line:
                            parts.append(line)
                text = '\n'.join(parts)
                if text.strip():
                    return text
            finally:
                wb.close()
        except Exception as e:
            applog.log(f'Extract: openpyxl failed for {filepath!r}: {e}',
                       level='WARN')
    else:
        applog.log('Extract: openpyxl not installed, using OOXML fallback',
                   level='WARN')
    if not zipfile.is_zipfile(filepath):
        applog.log(f'Extract: cannot read spreadsheet {filepath!r}',
                   level='WARN')
        return ""
    return _extract_xlsx_xml(filepath)


def _extract_xls_legacy(filepath: str) -> str:
    """Extract text from legacy binary .xls (OLE) via xlrd."""
    try:
        import xlrd
    except ImportError:
        applog.log(f'Extract: legacy .xls needs xlrd, skipping '
                   f'{filepath!r}', level='WARN')
        return ""
    try:
        book = xlrd.open_workbook(filepath)
    except Exception as e:
        applog.log(f'Extract: cannot open {filepath!r}: {e}', level='WARN')
        return ""
    parts = []
    try:
        for sheet in book.sheets():
            parts.append(sheet.name)
            for r in range(sheet.nrows):
                cells = [str(sheet.cell_value(r, c))
                         for c in range(sheet.ncols)
                         if sheet.cell_value(r, c) not in (None, '')]
                if cells:
                    parts.append(' '.join(cells))
    except Exception as e:
        applog.log(f'Extract: error reading {filepath!r}: {e}', level='WARN')
    return '\n'.join(parts)


def _extract_xlsx_xml(filepath: str) -> str:
    """Stdlib .xlsx fallback: shared strings + inline values, sheet by
    sheet (sheet names included so filename-like queries match)."""
    try:
        z = zipfile.ZipFile(filepath)
    except Exception as e:
        applog.log(f'Extract: cannot unzip {filepath!r}: {e}', level='WARN')
        return ""
    with z:
        try:
            names = z.namelist()
        except Exception:
            return ""
        shared = _xlsx_shared_strings(z)
        sheet_names = _xlsx_sheet_names(z)
        parts = []
        sheets = sorted(n for n in names
                        if n.startswith('xl/worksheets/sheet') and
                        n.endswith('.xml'))
        for i, name in enumerate(sheets):
            if i < len(sheet_names):
                parts.append(sheet_names[i])
            try:
                xml_bytes = z.read(name)
            except KeyError:
                continue
            parts.append(_xlsx_sheet_text(xml_bytes, shared))
    return '\n'.join(p for p in parts if p)


def _xlsx_shared_strings(z):
    """Return the shared-string table (list) or [] when absent."""
    try:
        xml_bytes = z.read('xl/sharedStrings.xml')
    except KeyError:
        return []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    strings = []
    for si in root.iter(ns + 'si'):
        strings.append(''.join(n.text or '' for n in si.iter(ns + 't')))
    return strings


def _xlsx_sheet_names(z):
    """Sheet names from xl/workbook.xml (display order)."""
    try:
        xml_bytes = z.read('xl/workbook.xml')
    except KeyError:
        return []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    return [s.get('name') for s in root.iter(ns + 'sheet') if s.get('name')]


def _xlsx_sheet_text(xml_bytes, shared):
    """Cell values of one worksheet XML: cells space-joined per row."""
    ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return ""
    rows = []
    for row in root.iter(ns + 'row'):
        cells = []
        for c in row.iter(ns + 'c'):
            t = c.get('t')
            if t == 's':  # shared string index
                v = c.find(ns + 'v')
                if v is not None and v.text is not None:
                    try:
                        cells.append(shared[int(v.text)])
                    except (ValueError, IndexError):
                        pass
            elif t == 'inlineStr':
                cells.append(''.join(n.text or ''
                                     for n in c.iter(ns + 't')))
            else:  # number, date, bool, or plain string
                v = c.find(ns + 'v')
                if v is not None and v.text:
                    cells.append(v.text)
        line = ' '.join(c for c in cells if c)
        if line:
            rows.append(line)
    return '\n'.join(rows)
