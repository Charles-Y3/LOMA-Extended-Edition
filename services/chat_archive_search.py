# -*- coding: utf-8 -*-
"""Chat archive keyword search."""
from __future__ import annotations

import re
from pathlib import Path

from services.session import chat_archive

_TOKEN = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN.findall(text or "") if len(t) > 1}


def _read_snippet(rel_path: str, limit: int = 4000) -> str:
    try:
        path = chat_archive.chat_path(rel_path)
    except ValueError:
        return ""
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except Exception:
        return ""


def _display_title(rel_path: str) -> str:
    meta = chat_archive.get_entry(rel_path) or {}
    return str(meta.get("title") or Path(rel_path).stem)


def list_all_chat_files() -> list[str]:
    """Every saved chat path under data/chats (recursive)."""
    root = chat_archive.CHAT_DIR
    if not root.is_dir():
        return []
    out: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in chat_archive.CHAT_EXTENSIONS:
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith("."):
            continue
        out.append(rel)
    return out


def keyword_score(query: str, rel_path: str) -> float:
    q = (query or "").strip().lower()
    if not q:
        return 0.0
    title = _display_title(rel_path).lower()
    snippet = _read_snippet(rel_path).lower()
    hay = f"{title}\n{rel_path.lower()}\n{snippet}"
    if q in hay:
        return 10.0 + hay.count(q)
    q_tokens = _tokens(q)
    if not q_tokens:
        return 0.0
    hay_tokens = _tokens(hay)
    overlap = len(q_tokens & hay_tokens)
    if overlap == 0:
        return 0.0
    return overlap / len(q_tokens) * 5.0


def search_chat_files(rel_paths: list[str], query: str) -> list[str]:
    q = (query or "").strip()
    if not q:
        return list(rel_paths)
    scored = [(p, keyword_score(q, p)) for p in rel_paths]
    hits = [(p, s) for p, s in scored if s > 0]
    hits.sort(key=lambda x: (-x[1], x[0].lower()))
    return [p for p, _ in hits]
