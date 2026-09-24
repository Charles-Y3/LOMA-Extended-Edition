# services/chat_archive.py
# -*- coding: utf-8 -*-
"""Metadata index for saved chat freezes (editable titles + timestamps)."""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from services.platform_paths import is_frozen, writable_root

# Saved chats live in the per-user data folder (dev runs: <repo>/data/chats, the same place
# as before). A packaged app's own folder can be read-only (macOS App Translocation) or
# code-signed, so it must never be written to.
CHAT_DIR = Path(writable_root()) / "data" / "chats"
_LEGACY_CHAT_DIR = Path(__file__).resolve().parents[2] / "data" / "chats"
INDEX_FILE = CHAT_DIR / "index.json"
CHAT_EXTENSIONS = (".yaml", ".md")


def _ensure_dir() -> None:
    if is_frozen() and not CHAT_DIR.exists() and _LEGACY_CHAT_DIR.is_dir():
        # Earlier packaged builds saved chats inside the install folder; carry them over.
        try:
            shutil.copytree(_LEGACY_CHAT_DIR, CHAT_DIR)
        except OSError:
            pass
    CHAT_DIR.mkdir(parents=True, exist_ok=True)


def _sanitize_folder_name(name: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", (name or "").strip()).strip().replace(" ", "_")
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned or cleaned in (".", ".."):
        raise ValueError("Invalid folder name")
    return cleaned[:48]


def _is_chat_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in CHAT_EXTENSIONS


def rel_path(folder: str, filename: str) -> str:
    folder = normalize_folder_path(folder)
    base = Path(filename).name
    return f"{folder}/{base}" if folder else base


def normalize_folder_path(folder: str) -> str:
    """Relative path under data/chats (empty string = root)."""
    parts: list[str] = []
    for segment in (folder or "").replace("\\", "/").split("/"):
        segment = segment.strip()
        if not segment or segment in (".", ".."):
            continue
        parts.append(_sanitize_folder_name(segment))
    return "/".join(parts)


def folder_abs_path(rel_folder: str) -> Path:
    rel = normalize_folder_path(rel_folder)
    path = (CHAT_DIR / rel).resolve() if rel else CHAT_DIR.resolve()
    if not str(path).startswith(str(CHAT_DIR.resolve())):
        raise ValueError("Invalid folder path")
    return path


def list_folders() -> list[str]:
    """Top-level folder names under data/chats (legacy helper)."""
    subdirs, _ = list_directory("")
    return subdirs


def list_directory(rel_folder: str = "") -> tuple[list[str], list[str]]:
    """
    List immediate children of a folder under data/chats.
    Returns (subdir_names, chat_relative_paths).
    """
    _ensure_dir()
    rel = normalize_folder_path(rel_folder)
    root = folder_abs_path(rel)
    if not root.is_dir():
        return [], []
    subdirs: list[str] = []
    files: list[str] = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            subdirs.append(entry.name)
        elif _is_chat_file(entry):
            child_rel = f"{rel}/{entry.name}" if rel else entry.name
            files.append(child_rel)
    files.sort(key=str.lower, reverse=True)
    return subdirs, files


def list_chat_files(folder: str = "") -> list[str]:
    """Return chat relative paths in one folder (non-recursive)."""
    _, files = list_directory(folder)
    return files


def parent_folder_path(rel_folder: str) -> str:
    rel = normalize_folder_path(rel_folder)
    if not rel:
        return ""
    parts = rel.split("/")
    return "/".join(parts[:-1]) if len(parts) > 1 else ""


def join_folder_path(parent: str, name: str) -> str:
    parent = normalize_folder_path(parent)
    child = _sanitize_folder_name(name)
    return f"{parent}/{child}" if parent else child


def chat_path(rel: str) -> Path:
    rel = (rel or "").replace("\\", "/").lstrip("/")
    path = (CHAT_DIR / rel).resolve()
    if not str(path).startswith(str(CHAT_DIR.resolve())):
        raise ValueError("Invalid chat path")
    return path


def create_folder(name: str, parent: str = "") -> str:
    folder = _sanitize_folder_name(name)
    parent = normalize_folder_path(parent)
    path = folder_abs_path(join_folder_path(parent, folder))
    path.mkdir(parents=True, exist_ok=False)
    return join_folder_path(parent, folder)


def rename_folder(old_name: str, new_name: str) -> str:
    old_rel = normalize_folder_path(old_name)
    parent = parent_folder_path(old_rel)
    new_rel = join_folder_path(parent, _sanitize_folder_name(new_name))
    if old_rel == new_rel:
        return new_rel
    src = folder_abs_path(old_rel)
    dst = folder_abs_path(new_rel)
    if not src.is_dir():
        raise FileNotFoundError(f"Folder not found: {old_rel}")
    if dst.exists():
        raise FileExistsError(f"Folder already exists: {new_rel}")
    src.rename(dst)
    index = load_index()
    updated: dict = {}
    prefix_old = f"{old_rel}/"
    prefix_new = f"{new_rel}/"
    for key, meta in index.items():
        if key.startswith(prefix_old):
            updated[key.replace(prefix_old, prefix_new, 1)] = meta
        elif key == old_rel:
            continue
        else:
            updated[key] = meta
    save_index(updated)
    return new_rel


def delete_folder(name: str) -> None:
    """Delete an empty folder only."""
    folder = normalize_folder_path(name)
    path = folder_abs_path(folder)
    if not path.is_dir():
        raise FileNotFoundError(f"Folder not found: {folder}")
    if any(path.iterdir()):
        raise OSError("Folder is not empty. Move or delete chats first.")
    path.rmdir()


def delete_folder_tree(rel_folder: str) -> None:
    """Delete a folder and all nested chats; updates index.json."""
    folder = normalize_folder_path(rel_folder)
    path = folder_abs_path(folder)
    if not path.is_dir():
        raise FileNotFoundError(f"Folder not found: {folder}")
    prefix = f"{folder}/"
    index = load_index()
    for key in list(index.keys()):
        if key.startswith(prefix):
            index.pop(key, None)
    save_index(index)
    shutil.rmtree(path)


def move_chat_file(rel: str, target_folder: str) -> str:
    rel = rel.replace("\\", "/")
    src = chat_path(rel)
    if not src.is_file():
        raise FileNotFoundError(rel)
    target_folder = normalize_folder_path(target_folder)
    dest_dir = folder_abs_path(target_folder)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if dest.resolve() == src.resolve():
        return rel_path(target_folder, src.name)
    if dest.exists():
        raise FileExistsError(f"{src.name} already exists in target folder")
    src.rename(dest)
    new_rel = rel_path(target_folder, src.name)
    index = load_index()
    if rel in index:
        index[new_rel] = index.pop(rel)
        save_index(index)
    return new_rel


def load_index() -> dict:
    _ensure_dir()
    if not INDEX_FILE.exists():
        return {}
    try:
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_index(index: dict) -> None:
    _ensure_dir()
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)


def _parse_legacy_md(path: Path) -> dict:
    """Extract title/summary/datetime from freeze markdown front matter."""
    title = path.stem.replace("freeze_", "Chat ")
    created = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    try:
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines()[:30]:
            if line.startswith("- **Summary:**"):
                title = line.split(":", 1)[1].strip().strip("*")
            if line.startswith("- **Export Date/Time:**"):
                created = line.split(":", 1)[1].strip().strip("*")
    except Exception:
        pass
    return {"title": title, "created_at": created, "updated_at": created}


def get_entry(filename: str) -> dict:
    index = load_index()
    if filename in index:
        return index[filename]
    try:
        path = chat_path(filename)
    except ValueError:
        path = CHAT_DIR / filename
    if path.exists():
        meta = _parse_legacy_md(path)
        index[filename] = meta
        save_index(index)
        return meta
    return {
        "title": filename,
        "created_at": "",
        "updated_at": "",
    }


def register_freeze(filename: str, title: str, created_at: str | None = None) -> None:
    now = created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    index = load_index()
    index[filename] = {
        "title": title.strip() or "Untitled chat",
        "created_at": now,
        "updated_at": now,
    }
    save_index(index)


def update_title(filename: str, new_title: str) -> None:
    index = load_index()
    entry = index.get(filename, get_entry(filename))
    entry["title"] = (new_title or "Untitled chat").strip()
    entry["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    index[filename] = entry
    save_index(index)


def remove_entry(filename: str) -> None:
    index = load_index()
    index.pop(filename, None)
    save_index(index)


def safe_filename_from_title(title: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", title).strip().replace(" ", "_")[:40]
    return slug or "chat"


def source_display_name(filename: str) -> str:
    """Label for Sources list: title_with_datetime (not freeze_*.md)."""
    meta = get_entry(filename)
    title = (meta.get("title") or "Chat").strip()
    slug = safe_filename_from_title(title)
    created = (meta.get("created_at") or "").strip()
    dt_slug = ""
    if created:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt_slug = datetime.strptime(created, fmt).strftime("%Y-%m-%d_%H-%M-%S")
                break
            except ValueError:
                continue
        if not dt_slug:
            dt_slug = re.sub(r"[^\w-]", "_", created)[:24]
    if dt_slug:
        return f"{slug}_{dt_slug}.txt"
    return f"{slug}.txt"
