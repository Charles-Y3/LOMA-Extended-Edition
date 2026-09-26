# -*- coding: utf-8 -*-
"""Guard: user-visible text must go through tr()/t() (project rule 7), never a hardcoded English literal.

Scans every call that shows text to the user (ui.notify / ui.label / ui.button / set_assistant_content /
post_assistant_message / add_paragraph ...) and fails on an English string literal passed to it.
Diagnostic log lines (log / sink.log / add_log, which only go to the log file) are not user-facing and are
not scanned. Add an entry to ALLOWED only for text that is genuinely not translatable (a brand name)."""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"venv", ".git", "tests", "node_modules", "__pycache__", "packaging", "scripts", "data", "logs", "docs", "config"}

SINKS = {
    "notify", "label", "button", "markdown", "tooltip", "set_text", "set_content", "expansion", "checkbox", "switch",
    "input", "textarea", "select", "tab", "chip", "html", "set_assistant_content", "post_assistant_message",
    "append_assistant_message", "add_paragraph", "add_heading", "show_message",
}
KW_SINKS = {"label", "placeholder", "hint", "title", "text", "caption", "message", "description", "error"}
WORD = re.compile(r"[A-Za-z]{3,}")

# (file, literal) pairs that are not translatable text.
ALLOWED = {
    ("ui/layouts/nav_panel.py", "LOMA"),  # brand name
    # Template: the label passed in by the caller is already translated.
    ("extensions/ludicity_shared/chat_flow.py", '<span style="color:#60a5fa">**:** </span>'),
}


def _english(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        s = node.value
    elif isinstance(node, ast.JoinedStr):
        s = "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
    else:
        return None
    if not WORD.findall(s):
        return None
    if re.fullmatch(r"[\w\-./:#%{}\s]*", s) and len(WORD.findall(s)) == 1 and (s.islower() or "_" in s or "-" in s or "." in s):
        return None  # css classes / identifiers / keys
    if re.search(r"(text-|bg-|border-|w-\[|px-|py-|gap-|flex|items-|justify-|rounded|shrink|font-)", s):
        return None  # tailwind class strings
    if s.startswith(("text-", "w-", "q-", "bg-", "flat", "outline", "dense", "size=")):
        return None
    return s


def _call_name(func) -> str:
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _find_violations() -> list[str]:
    found: list[str] = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts) or re.search(r"(i18n|test_)", path.name):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _call_name(node.func) not in SINKS:
                continue
            candidates = list(node.args[:1]) + [kw.value for kw in node.keywords if kw.arg in KW_SINKS]
            for cand in candidates:
                text = _english(cand)
                if text and len(text.strip()) >= 3 and (rel, text.strip()) not in ALLOWED:
                    found.append(f"{rel}:{node.lineno}: {text.strip()[:90]}")
    return sorted(found)


def test_no_hardcoded_english_at_user_facing_sinks():
    violations = _find_violations()
    assert not violations, (
        "Hardcoded English shown to the user (use `from pipeline.i18n import t as tr` + tr(\"key\") and add the "
        "key for en, zh_tw, zh_cn, es, de):\n" + "\n".join(violations)
    )


def test_the_scanner_itself_catches_a_violation(tmp_path):
    """Control: prove the guard would fail on a new hardcoded string."""
    sample = 'from nicegui import ui\nui.notify("Something went wrong here")\nsink.set_assistant_content(f"Done: {x}")\n'
    tree = ast.parse(sample)
    hits = [
        _english(c)
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and _call_name(n.func) in SINKS
        for c in n.args[:1]
    ]
    assert [h for h in hits if h] == ["Something went wrong here", "Done: "]
