# -*- coding: utf-8 -*-
"""Text extraction and chunking for Document Intelligence."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from contextlib import contextmanager
from typing import Callable

from extensions.document_intelligence.corpus.types import ChunkRecord
from extensions.document_intelligence.indexing_locks import file_appears_locked, note_locked_file
from extensions.document_intelligence.settings import load_settings

LogFn = Callable[[str], None]
VALID_EXTS = {".pdf", ".docx", ".doc", ".txt"}

NOISE_PATTERNS = [
    r"系列",
    r"^[0-9\s\-—_]+$",
    r"^第\s*[一二三四五六七八九十\d]+\s*頁$",
    r"(?i)^page\s*\d+",
    r"(?i)^p\.\s*\d+",
]


class _FallbackTokenEncoding:
    """Crude token estimator used when tiktoken's encoding data isn't reachable (its
    plugin discovery relies on scanning a namespace package that doesn't survive being
    frozen, and its BPE files are normally fetched from the network on first use, which
    this offline app can't rely on). Approximates cl100k_base's ~4-chars-per-token ratio
    closely enough for chunk sizing."""

    def encode(self, text: str) -> list[int]:
        return [0] * max(1, (len(text or "") + 3) // 4)


def _encoding():
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        pass
    try:
        import tiktoken

        return tiktoken.get_encoding("gpt2")
    except Exception:
        return _FallbackTokenEncoding()


def _token_count(text: str, enc) -> int:
    try:
        return len(enc.encode(text or ""))
    except Exception:
        return len(text or "")


def list_document_files(folder: str) -> list[str]:
    out: list[str] = []
    for root, _, files in os.walk(folder):
        for name in files:
            if os.path.splitext(name)[1].lower() in VALID_EXTS:
                out.append(os.path.join(root, name))
    return sorted(out)


def vault_path_for(file_path: str, ingest_root: str) -> str:
    try:
        return os.path.relpath(os.path.abspath(file_path), os.path.abspath(ingest_root)).replace(
            "\\", "/"
        )
    except ValueError:
        return os.path.basename(file_path)


def _collapse_vertical_pdf_text(text: str) -> str:
    """pypdf's extract_text() breaks vertically-set / columnar CJK PDF text into one
    glyph per line (each character occupies its own visual row) — the character
    SEQUENCE stays in correct reading order (verified against the same document's
    clean docx export: stripping all whitespace reproduces it exactly, in order), so
    rejoining lines with no separator restores normal flowing text without reordering
    anything. Without this, a translator/summarizer model sees a wall of single-
    character "lines" and produces garbled or incomplete output on weaker models.
    Horizontal PDFs (normal multi-character lines) are detected by line-length density
    and left untouched."""
    if not text:
        return text
    lines = text.split("\n")
    non_empty = [ln for ln in lines if ln.strip()]
    if len(non_empty) < 8:
        return text
    short = sum(1 for ln in non_empty if len(ln.strip()) <= 2)
    if short / len(non_empty) < 0.6:
        return text
    return "".join(lines)


_FURNITURE_WINDOW = 20
_FURNITURE_MIN_PAGES = 3
_FURNITURE_MIN_FRACTION = 0.5
_FURNITURE_MIN_MATCH_LEN = 3


def _dominant_fixed_length_run(strings: list[str], *, from_end: bool, threshold: int) -> str:
    """Prefix (or, if from_end, suffix) of up to `_FURNITURE_WINDOW` chars shared
    verbatim by the most of the given strings, among every length that clears
    `threshold` — not just the first (longest) length that happens to clear it: e.g.
    a header followed by inconsistent whitespace ("Title " on some pages, "Title  "
    on others) means the longest exact match only covers half the pages, while a
    shorter prefix covers all of them. Broadest coverage wins; longer length is only
    a tiebreaker among equally-covering candidates."""
    from collections import Counter

    best_candidate, best_count, best_length = "", 0, 0
    for length in range(_FURNITURE_WINDOW, _FURNITURE_MIN_MATCH_LEN - 1, -1):
        pieces = (
            [s[-length:] for s in strings if len(s) >= length]
            if from_end
            else [s[:length] for s in strings if len(s) >= length]
        )
        if not pieces:
            continue
        candidate, count = Counter(pieces).most_common(1)[0]
        if not candidate.strip() or count < threshold:
            continue
        if count > best_count or (count == best_count and length > best_length):
            best_candidate, best_count, best_length = candidate, count, length
    return best_candidate


def _strip_repeating_page_furniture(pages_text: list[str]) -> list[str]:
    """Remove a running header/footer that repeats near-identically across most pages
    of a PDF (e.g. a document/class title printed on every page, often followed by a
    per-page number that varies) — PDF text extraction has no structural header/footer
    concept, so this boilerplate otherwise gets ingested as real content on EVERY
    page: diluting/prefixing real-content chunks with irrelevant repeated text, and
    creating spurious tiny header-only chunks (which then also get excluded for being
    too short — a symptom that shows up as more "low-content" chunks than the actual
    cover-page count would suggest).

    Operates on a fixed leading/trailing character window of the raw page text rather
    than splitting on newlines — for a vertically-set PDF, _collapse_vertical_pdf_text
    can legitimately return a whole page as ONE line with no newlines at all (pypdf's
    raw extraction has no blank-line paragraph markers to convert), so treating "the
    first line" as "everything up to the first \\n" wiped out entire pages whenever
    the whole (headerless) blob happened to start with the header's prefix."""
    if len(pages_text) < _FURNITURE_MIN_PAGES:
        return pages_text

    non_empty = [t for t in pages_text if t.strip()]
    if len(non_empty) < _FURNITURE_MIN_PAGES:
        return pages_text
    threshold = max(_FURNITURE_MIN_PAGES, int(len(pages_text) * _FURNITURE_MIN_FRACTION))

    header = _dominant_fixed_length_run(non_empty, from_end=False, threshold=threshold)
    footer = _dominant_fixed_length_run(non_empty, from_end=True, threshold=threshold)
    if not header and not footer:
        return pages_text

    out: list[str] = []
    for t in pages_text:
        s = t
        if header and s.startswith(header):
            s = s[len(header) :]
        if footer and s.endswith(footer):
            s = s[: len(s) - len(footer)]
        out.append(s)
    return out


def _extract_blocks(file_path: str, *, log_fn: LogFn | None = None) -> list[dict]:
    ext = os.path.splitext(file_path)[1].lower()
    blocks: list[dict] = []
    section = "General"
    cfg = load_settings()

    if ext == ".txt":
        with open(file_path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                text = line.strip()
                if not text:
                    continue
                if re.match(r"^\[.*]$", text):
                    section = text.strip("[] ")
                blocks.append({"text": text, "section": section, "page_number": None})

    elif ext in (".docx", ".doc"):
        with open_docx_for_reading(file_path, log_fn=log_fn) as docx_path:
            if not docx_path:
                return []
            from docx import Document as DocxReader

            doc = DocxReader(docx_path)
            for table in doc.tables:
                for row in table.rows:
                    cells = [c.text.strip() for c in row.cells if c.text.strip()]
                    if len(cells) >= 2:
                        blocks.append({"text": cells[0], "section": "Table", "page_number": None})
                        blocks.append({"text": cells[1], "section": "Table", "page_number": None})
            for p in doc.paragraphs:
                text = p.text.strip()
                if not text or any(re.search(pat, text) for pat in NOISE_PATTERNS):
                    continue
                if p.style.name.startswith("Heading"):
                    section = text
                blocks.append({"text": text, "section": section, "page_number": None})

    elif ext == ".pdf":
        settings_pw = str(cfg.get("document_passwords") or "")
        reader = _open_pdf(file_path, settings_pw=settings_pw, log_fn=log_fn)
        if reader is None:
            return []
        pages_text = [
            _collapse_vertical_pdf_text((page.extract_text() or "").strip())
            for page in reader.pages
        ]
        pages_text = _strip_repeating_page_furniture(pages_text)
        for page_num, text in enumerate(pages_text, start=1):
            if not text:
                continue
            for para in re.split(r"\n{2,}", text):
                para = para.strip()
                if para and not any(re.search(pat, para) for pat in NOISE_PATTERNS):
                    blocks.append({"text": para, "section": section, "page_number": page_num})

    return blocks


def _open_pdf(file_path: str, *, settings_pw: str, log_fn: LogFn | None):
    from pypdf import PdfReader

    from extensions.document_intelligence.passwords import (
        password_attempts,
        remember_password,
        remember_skip,
        request_password_dialog,
        was_skipped,
    )

    reader = PdfReader(file_path)
    if not reader.is_encrypted:
        return reader
    tried = password_attempts(file_path, settings_pw)
    for pwd in list(tried):
        if reader.decrypt(pwd):
            remember_password(file_path, pwd)
            return reader
        reader = PdfReader(file_path)
    if was_skipped(file_path):
        if log_fn:
            log_fn(f"Skipped encrypted PDF (no password): {os.path.basename(file_path)}")
        return None
    while True:
        pwd = request_password_dialog(file_path)
        if not pwd:
            remember_skip(file_path)
            if log_fn:
                log_fn(f"Skipped encrypted PDF (no password): {os.path.basename(file_path)}")
            return None
        if pwd not in tried:
            tried.append(pwd)
        if reader.decrypt(pwd):
            remember_password(file_path, pwd)
            return reader
        reader = PdfReader(file_path)
        if log_fn:
            log_fn(f"Wrong password for {os.path.basename(file_path)} — try again.")


def _safe_unlink(path: str) -> None:
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def _office_is_encrypted(file_path: str) -> bool | None:
    """True/False when known; None if msoffcrypto is unavailable."""
    try:
        import msoffcrypto

        with open(file_path, "rb") as raw:
            office = msoffcrypto.OfficeFile(raw)
            return bool(office.is_encrypted())
    except ImportError:
        return None
    except Exception:
        return False


def _decrypt_office_to_temp(
    file_path: str,
    *,
    settings_pw: str,
    log_fn: LogFn | None,
) -> str | None:
    try:
        import io

        import msoffcrypto
    except ImportError:
        if log_fn:
            log_fn(
                f"Encrypted Office file needs msoffcrypto-tool: {os.path.basename(file_path)}"
            )
        return None

    from extensions.document_intelligence.passwords import (
        password_attempts,
        remember_password,
        remember_skip,
        request_password_dialog,
        was_skipped,
    )

    try:
        with open(file_path, "rb") as raw:
            if not msoffcrypto.OfficeFile(raw).is_encrypted():
                return None
    except Exception:
        return None

    def _try_pwd(pwd: str) -> str | None:
        try:
            with open(file_path, "rb") as raw:
                office = msoffcrypto.OfficeFile(raw)
                decrypted = io.BytesIO()
                office.load_key(password=pwd)
                office.decrypt(decrypted)
                decrypted.seek(0)
                fd, temp_path = tempfile.mkstemp(
                    suffix=os.path.splitext(file_path)[1] or ".docx"
                )
                os.close(fd)
                with open(temp_path, "wb") as out:
                    out.write(decrypted.read())
                return temp_path
        except Exception:
            return None

    tried = password_attempts(file_path, settings_pw)
    for pwd in list(tried):
        temp_path = _try_pwd(pwd)
        if temp_path:
            remember_password(file_path, pwd)
            return temp_path
    if was_skipped(file_path):
        if log_fn:
            log_fn(f"Skipped encrypted document (no password): {os.path.basename(file_path)}")
        return None
    while True:
        pwd = request_password_dialog(file_path)
        if not pwd:
            remember_skip(file_path)
            if log_fn:
                log_fn(
                    f"Skipped encrypted document (no password): {os.path.basename(file_path)}"
                )
            return None
        if pwd not in tried:
            tried.append(pwd)
        temp_path = _try_pwd(pwd)
        if temp_path:
            remember_password(file_path, pwd)
            return temp_path
        if log_fn:
            log_fn(f"Wrong password for {os.path.basename(file_path)} — try again.")


# Standard LibreOffice install locations that a plain .app/.deb/.rpm install doesn't
# add to PATH — checked only if `soffice`/`libreoffice` isn't already on PATH.
_SOFFICE_FALLBACK_PATHS = (
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",  # macOS
    "/usr/bin/soffice",  # Linux (most distro packages)
    "/usr/bin/libreoffice",
    "/opt/libreoffice/program/soffice",  # Linux (vendor tarball installs)
    "/snap/bin/libreoffice",  # Linux (snap)
)


def _find_soffice() -> str | None:
    for cmd_name in ("soffice", "libreoffice"):
        exe = shutil.which(cmd_name)
        if exe:
            return exe
    for candidate in _SOFFICE_FALLBACK_PATHS:
        if os.path.isfile(candidate):
            return candidate
    return None


_doc_convert_cache: dict[str, str] = {}
_doc_convert_lock = threading.Lock()


def prime_doc_conversion_cache(doc_paths: list[str], *, log_fn: LogFn | None = None) -> None:
    """Convert every given .doc file to .docx through ONE warmed-up Word/LibreOffice
    instance instead of launching a fresh process per file — a single legacy .doc
    used to cost a full Word (or LibreOffice) cold start (often 1-3s+) on its own,
    so a folder of N .doc files paid that cost N times over. Call this once with
    the whole batch before indexing individual files; _convert_doc_to_docx() below
    checks this cache first and only falls back to a one-off conversion for
    anything not covered here (encrypted files are deliberately excluded — see
    below).
    """
    with _doc_convert_lock:
        todo = [p for p in doc_paths if os.path.abspath(p) not in _doc_convert_cache]
    if not todo:
        return
    # Encrypted .doc files need decrypt-then-convert handled per-file (see
    # _resolve_docx_path) — handing an still-encrypted file to Word/LibreOffice
    # automation risks a native password prompt that a headless/invisible
    # automation session can never dismiss, hanging the whole batch. Only batch
    # files confirmed NOT encrypted; anything encrypted or unknown (msoffcrypto
    # unavailable) stays on the slower, safe per-file path.
    safe = [p for p in todo if _office_is_encrypted(p) is False]
    if not safe:
        return
    if sys.platform == "win32":
        _convert_doc_batch_word(safe, log_fn=log_fn)
    remaining = [p for p in safe if os.path.abspath(p) not in _doc_convert_cache]
    if remaining:
        _convert_doc_batch_soffice(remaining, log_fn=log_fn)


def _convert_doc_batch_word(doc_paths: list[str], *, log_fn: LogFn | None) -> None:
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        if log_fn:
            log_fn("Legacy .doc on Windows needs pywin32 (Microsoft Word installed).")
        return

    pythoncom.CoInitialize()
    word = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        word.AutomationSecurity = 3
        for doc_path in doc_paths:
            abs_doc = os.path.abspath(doc_path)
            fd, out_path = tempfile.mkstemp(suffix=".docx")
            os.close(fd)
            try:
                doc = word.Documents.Open(FileName=abs_doc, ReadOnly=True, Visible=False)
                doc.SaveAs2(out_path, FileFormat=16)
                doc.Close(False)
                with _doc_convert_lock:
                    _doc_convert_cache[abs_doc] = out_path
            except Exception as exc:
                _safe_unlink(out_path)
                if log_fn:
                    log_fn(f".doc conversion failed ({os.path.basename(doc_path)}): {exc}")
    except Exception as exc:
        if log_fn:
            log_fn(f"Word batch conversion unavailable: {exc}")
    finally:
        if word is not None:
            try:
                word.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()


def _convert_doc_batch_soffice(doc_paths: list[str], *, log_fn: LogFn | None) -> None:
    exe = _find_soffice()
    if not exe:
        return
    out_dir = tempfile.mkdtemp()
    try:
        abs_paths = [os.path.abspath(p) for p in doc_paths]
        subprocess.run(
            [exe, "--headless", "--convert-to", "docx", "--outdir", out_dir, *abs_paths],
            check=True,
            capture_output=True,
            timeout=120 + 15 * len(abs_paths),
        )
        for p in abs_paths:
            candidate = os.path.join(out_dir, os.path.splitext(os.path.basename(p))[0] + ".docx")
            if os.path.isfile(candidate):
                fd, out_path = tempfile.mkstemp(suffix=".docx")
                os.close(fd)
                shutil.move(candidate, out_path)
                with _doc_convert_lock:
                    _doc_convert_cache[p] = out_path
    except Exception as exc:
        if log_fn:
            log_fn(f"Batch .doc conversion (LibreOffice) failed: {exc}")
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def _convert_doc_to_docx(doc_path: str, *, log_fn: LogFn | None) -> str | None:
    abs_doc = os.path.abspath(doc_path)
    with _doc_convert_lock:
        cached = _doc_convert_cache.get(abs_doc)
    if cached and os.path.isfile(cached):
        return cached

    fd, out_path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)

    if sys.platform == "win32":
        try:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            word = None
            try:
                word = win32com.client.DispatchEx("Word.Application")
                word.Visible = False
                word.DisplayAlerts = 0
                word.AutomationSecurity = 3
                doc = word.Documents.Open(
                    FileName=abs_doc, ReadOnly=True, Visible=False
                )
                doc.SaveAs2(out_path, FileFormat=16)
                doc.Close(False)
                return out_path
            except Exception as exc:
                if log_fn:
                    log_fn(f".doc conversion failed ({os.path.basename(doc_path)}): {exc}")
            finally:
                if word is not None:
                    word.Quit()
                pythoncom.CoUninitialize()
        except ImportError:
            if log_fn:
                log_fn("Legacy .doc on Windows needs pywin32 (Microsoft Word installed).")

    out_dir = tempfile.mkdtemp()
    try:
        exe = _find_soffice()
        if exe:
            subprocess.run(
                [exe, "--headless", "--convert-to", "docx", "--outdir", out_dir, abs_doc],
                check=True,
                capture_output=True,
                timeout=120,
            )
            candidate = os.path.join(
                out_dir, os.path.splitext(os.path.basename(abs_doc))[0] + ".docx"
            )
            if os.path.isfile(candidate):
                shutil.move(candidate, out_path)
                return out_path
    except Exception as exc:
        if log_fn:
            log_fn(f".doc conversion failed ({os.path.basename(doc_path)}): {exc}")
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)

    _safe_unlink(out_path)
    if log_fn:
        log_fn(
            f"Could not convert .doc — install Microsoft Word or LibreOffice, "
            f"or save as .docx: {os.path.basename(doc_path)}"
        )
    return None


def _resolve_docx_path(
    file_path: str,
    *,
    settings_pw: str,
    log_fn: LogFn | None,
) -> tuple[str | None, list[str]]:
    ext = os.path.splitext(file_path)[1].lower()
    temps: list[str] = []

    if ext == ".doc":
        source = file_path
        if _office_is_encrypted(file_path) is not False:
            unlocked = _decrypt_office_to_temp(
                file_path, settings_pw=settings_pw, log_fn=log_fn
            )
            if not unlocked and _office_is_encrypted(file_path):
                return None, temps
            if unlocked:
                source = unlocked
                temps.append(unlocked)
        converted = _convert_doc_to_docx(source, log_fn=log_fn)
        if not converted:
            for t in temps:
                _safe_unlink(t)
            return None, []
        temps.append(converted)
        return converted, temps

    if ext != ".docx":
        return None, []

    from docx import Document as DocxReader

    encrypted = _office_is_encrypted(file_path)
    if encrypted is False:
        try:
            DocxReader(file_path)
            return file_path, []
        except Exception as first_exc:
            err = str(first_exc).lower()
            if "password" not in err and "encrypted" not in err and "package" not in err:
                if log_fn:
                    log_fn(f"DOCX error {os.path.basename(file_path)}: {first_exc}")
                return None, []

    decrypted = _decrypt_office_to_temp(file_path, settings_pw=settings_pw, log_fn=log_fn)
    if decrypted:
        return decrypted, [decrypted]
    if encrypted is False:
        try:
            DocxReader(file_path)
            return file_path, []
        except Exception:
            pass
    return None, []


@contextmanager
def open_docx_for_reading(file_path: str, *, log_fn: LogFn | None = None):
    cfg = load_settings()
    settings_pw = str(cfg.get("document_passwords") or "")
    path, temps = _resolve_docx_path(file_path, settings_pw=settings_pw, log_fn=log_fn)
    try:
        yield path
    finally:
        for t in temps:
            _safe_unlink(t)


_COVER_PAGE_MARKERS = ("封面", "cover", "扉頁", "扉页", "title page", "titlepage")
# Not a plain substring check — "cover" as a bare substring also matches "discovery",
# "coverage", "recovery", "uncovered", etc., which wrongly flagged large real-content
# files (e.g. a 90+ page report titled "...Coverage...") as single-page cover pages.
# \b doesn't help here (underscore counts as a word char, so "封面_2024" wouldn't match
# \b封面\b — a very common CJK filename pattern), so instead only reject a match that's
# directly flanked by another ASCII letter — exactly the "glued onto another English
# word" case, which is the only realistic false-positive shape.
_COVER_PAGE_PATTERN = re.compile(
    "|".join(re.escape(m) for m in _COVER_PAGE_MARKERS), re.IGNORECASE
)


def _looks_like_cover_filename(source: str) -> bool:
    for m in _COVER_PAGE_PATTERN.finditer(source):
        start, end = m.span()
        before = source[start - 1] if start > 0 else ""
        after = source[end] if end < len(source) else ""
        if before.isascii() and before.isalpha():
            continue
        if after.isascii() and after.isalpha():
            continue
        return True
    return False


def _docx_page_count(file_path: str) -> int | None:
    """Word's own last-saved page count from docProps/app.xml — the only cheap way to
    get a real page count for a .docx without a layout engine. None if unavailable
    (encrypted package, non-Word-authored file, missing field)."""
    import zipfile

    try:
        with zipfile.ZipFile(file_path) as z:
            if "docProps/app.xml" not in z.namelist():
                return None
            xml = z.read("docProps/app.xml").decode("utf-8", errors="ignore")
    except Exception:
        return None
    m = re.search(r"<Pages>(\d+)</Pages>", xml)
    return int(m.group(1)) if m else None


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？；.!?;])\s*")


def _hard_split_by_chars(text: str, enc, max_tokens: int) -> list[str]:
    """Last-resort split for a single sentence that's larger than max_tokens all on
    its own (no punctuation to break at) — by character window, not by word: CJK text
    doesn't reliably use whitespace as a word boundary, so a word-based split here
    would hit the exact same problem this function replaces."""
    n = len(text)
    if n == 0:
        return []
    total_tokens = max(_token_count(text, enc), 1)
    chars_per_token = max(n / total_tokens, 1.0)
    window = max(int(max_tokens * chars_per_token * 0.9), 20)
    parts = []
    for i in range(0, n, window):
        piece = text[i : i + window].strip()
        if piece:
            parts.append(piece)
    return parts


def _split_oversized_text(text: str, enc, max_tokens: int) -> list[str]:
    """Split a single block larger than max_tokens at sentence boundaries (CJK and
    Latin punctuation), accumulating up to the token budget — not by blindly dividing
    whitespace-delimited "words" into thirds. That word-based approach broke badly on
    CJK text from PDFs with irregular inter-character spacing (a stray space between
    almost every character/short group): `.split()` produced near-single-character
    "words", and dividing a word count into exact thirds via integer step/range left
    remainder pieces as small as ONE leftover word — literally a single character
    surviving as its own "chunk", instead of a coherent passage."""
    sentences = [s for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    if not sentences:
        sentences = [text]
    parts: list[str] = []
    buf: list[str] = []
    buf_tokens = 0
    for sent in sentences:
        stoks = _token_count(sent, enc)
        if stoks > max_tokens:
            if buf:
                parts.append("".join(buf).strip())
                buf, buf_tokens = [], 0
            parts.extend(_hard_split_by_chars(sent, enc, max_tokens))
            continue
        if buf_tokens + stoks > max_tokens and buf:
            parts.append("".join(buf).strip())
            buf, buf_tokens = [], 0
        buf.append(sent)
        buf_tokens += stoks
    if buf:
        parts.append("".join(buf).strip())
    return [p for p in parts if p.strip()]


def _chunk_blocks(blocks: list[dict], enc, *, max_tokens: int, min_tokens: int) -> list[dict]:
    chunks: list[dict] = []
    buf: list[str] = []
    buf_section = "General"
    buf_page: int | None = None
    buf_tokens = 0

    def flush():
        nonlocal buf, buf_tokens, buf_section, buf_page
        if not buf:
            return
        text = "\n".join(buf).strip()
        if _token_count(text, enc) >= min_tokens or len(chunks) == 0:
            chunks.append(
                {"text": text, "section": buf_section, "page_number": buf_page}
            )
        buf = []
        buf_tokens = 0

    def add_piece(piece_text: str, section: str, page_number: int | None) -> None:
        # Oversized-block pieces flow through this SAME accumulator as normal blocks
        # (rather than being appended to `chunks` directly, as before) — so a small
        # trailing piece merges with whatever comes next instead of surviving as its
        # own standalone fragment, subject to the same min_tokens gate as everything
        # else via flush().
        nonlocal buf, buf_tokens, buf_section, buf_page
        tokens = _token_count(piece_text, enc)
        if buf_tokens + tokens > max_tokens and buf:
            flush()
        if not buf:
            buf_section = section
            buf_page = page_number
        buf.append(piece_text)
        buf_tokens += tokens

    for block in blocks:
        text = (block.get("text") or "").strip()
        if not text:
            continue
        section = block.get("section") or "General"
        page_number = block.get("page_number")
        tokens = _token_count(text, enc)
        if tokens > max_tokens:
            for part in _split_oversized_text(text, enc, max_tokens):
                add_piece(part, section, page_number)
            continue
        add_piece(text, section, page_number)
    flush()
    return chunks


def extract_file_chunks(
    file_path: str,
    *,
    ingest_root: str,
    log_fn: LogFn | None = None,
) -> list[ChunkRecord]:
    if file_appears_locked(file_path):
        note_locked_file(file_path)
        if log_fn:
            log_fn(f"Locked (close file to index): {os.path.basename(file_path)}")
        return []
    try:
        blocks = _extract_blocks(file_path, log_fn=log_fn)
    except (PermissionError, OSError) as exc:
        note_locked_file(file_path)
        if log_fn:
            log_fn(f"Locked (close file to index): {os.path.basename(file_path)} — {exc}")
        return []
    if not blocks:
        return []
    cfg = load_settings()
    enc = _encoding()
    raw_chunks = _chunk_blocks(
        blocks,
        enc,
        max_tokens=int(cfg.get("chunk_size_tokens") or 320),
        min_tokens=int(cfg.get("min_chunk_tokens") or 60),
    )
    if not raw_chunks:
        return []
    source = os.path.splitext(os.path.basename(file_path))[0]
    vp = vault_path_for(file_path, ingest_root)
    home = os.path.expanduser("~")
    file_abs = os.path.abspath(file_path)
    try:
        display = (
            os.path.relpath(file_abs, home).replace("\\", "/")
            if file_abs.startswith(home)
            else file_abs
        )
    except ValueError:
        display = file_abs
    looks_like_cover = _looks_like_cover_filename(source)
    is_low_content_file = False
    if looks_like_cover:
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            page_numbers = [b.get("page_number") for b in blocks if b.get("page_number")]
            page_count = max(page_numbers) if page_numbers else None
        elif ext in (".docx", ".doc"):
            page_count = _docx_page_count(file_path)
        else:
            page_count = None
        is_low_content_file = page_count == 1
    min_retrieval_tokens = int(cfg.get("min_retrieval_tokens") or 20)
    out: list[ChunkRecord] = []
    low_content_count = 0
    low_content_previews: list[str] = []
    for idx, ch in enumerate(raw_chunks):
        # Separate from is_low_content_file (whole-file, cover-page rule above): this
        # catches any individual chunk in any file too short to generate anything
        # useful from — a stray header, page number, or table-of-contents line that
        # survived extraction as its own segment.
        too_short = _token_count(ch["text"], enc) < min_retrieval_tokens
        is_low_content = is_low_content_file or too_short
        if is_low_content:
            low_content_count += 1
            page = ch.get("page_number")
            page_label = f"p.{page} " if page else ""
            preview = " ".join(str(ch["text"]).split())[:60]
            reason = "cover page" if is_low_content_file else "too short"
            low_content_previews.append(f'{page_label}({reason}) "{preview}"')
        out.append(
            ChunkRecord(
                chunk_id=str(uuid.uuid4()),
                text=ch["text"],
                source=source,
                vault_path=vp,
                file_path=display,
                page_number=ch.get("page_number"),
                section=ch.get("section") or "General",
                segment_index=idx,
                ingest_root=os.path.abspath(ingest_root),
                is_low_content=is_low_content,
            )
        )
    if log_fn:
        note = f" ({low_content_count} low-content, e.g. cover pages)" if low_content_count else ""
        log_fn(f"Extracted {len(out)} segments from {source}{note}")
        max_shown = 8
        for preview in low_content_previews[:max_shown]:
            log_fn(f"  excluded: {preview}")
        if len(low_content_previews) > max_shown:
            log_fn(f"  … and {len(low_content_previews) - max_shown} more excluded")
    return out
