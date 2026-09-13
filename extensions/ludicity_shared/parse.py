# -*- coding: utf-8 -*-
"""Parse tagged LLM sections (avoids fragile JSON)."""
from __future__ import annotations

import re


# A section marker: 2+ dashes then an UPPERCASE tag (optional trailing dashes), or '## TAG'.
# Case-sensitive on the tag (scoped (?-i:...)) so prose words after a stray '--' don't
# count as the next section.
_NEXT_SECTION = r"(?:-{2,}\s*(?-i:[A-Z][A-Z0-9_]{2,})\s*-*|#{1,}\s*(?-i:[A-Z][A-Z0-9_]{2,}))"


def parse_tagged_sections(text: str, *tags: str) -> dict[str, str]:
    """Extract sections like ---TAG---, ---TAG, or ## TAG.

    Local models frequently drop the trailing dashes and put the content on the SAME
    line as the marker (e.g. '---SCENE The autumn air…'), so match leniently: 2+ leading
    dashes, optional trailing dashes, an optional ':', and content starting on the same
    line — up to the next section marker."""
    out: dict[str, str] = {}
    if not text:
        return out
    for tag in tags:
        pat = (
            rf"(?:-{{2,}}\s*{re.escape(tag)}\s*-*|#{{1,}}\s*{re.escape(tag)})"
            rf"\s*:?[ \t]*\n?(.*?)(?={_NEXT_SECTION}|\Z)"
        )
        m = re.search(pat, text, re.I | re.S)
        if m:
            out[tag] = m.group(1).strip()
    return out


def _strip_line_decoration(line: str) -> str:
    """Drop leading markdown/list decoration (**, __, #, >, -, •, spaces) so a
    tagged key line is recognized even when the model bolds or bullets it."""
    return re.sub(r"^[\s>*_#\-•]+", "", line or "")


def parse_key_lines(text: str, keys: tuple[str, ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        probe = _strip_line_decoration(line)
        for key in keys:
            if probe.upper().startswith(key.upper() + ":"):
                # Strip trailing markdown (e.g. "**PROGRESS_M:** 12") from the value.
                out[key] = probe.split(":", 1)[1].strip().lstrip("*_ ").strip()
    return out


def strip_key_lines(text: str, keys: tuple[str, ...]) -> str:
    """Remove KEY: value lines from LLM output before display. Tolerates markdown
    decoration and a key appearing mid-line (the tag is a machine directive that
    must never reach the user, even if the model wraps prose around it)."""
    out: list[str] = []
    for line in (text or "").splitlines():
        probe = _strip_line_decoration(line.strip())
        if any(probe.upper().startswith(f"{k.upper()}:") for k in keys):
            continue
        # Also drop a line where the tag appears after some prose on the same line.
        if any(re.search(rf"\b{re.escape(k)}\s*[:=]", line, re.I) for k in keys):
            continue
        out.append(line)
    return "\n".join(out).strip()


def is_placeholder_section(text: str) -> bool:
    """True when an LLM section is an instruction stub, not real content."""
    t = (text or "").strip()
    if not t:
        return True
    low = t.lower()
    if low.startswith("(") and any(
        x in low
        for x in (
            "omit",
            "leave blank",
            "only if",
            "else leave",
            "integers -",
            "completely empty",
            "leave this section",
        )
    ):
        return True
    if any(
        x in low
        for x in (
            "reflecting the player's last action",
            "return exactly",
            "do not restart",
            "player chose an action",
            "continue the story from",
        )
    ):
        return True
    return False


def is_real_ending(text: str) -> bool:
    """True when ENDING section is a genuine epilogue, not a format stub."""
    if is_placeholder_section(text):
        return False
    t = clean_display_text(text)
    if len(t) < 50:
        return False
    low = t.lower()
    if "what do you want to do" in low:
        return False
    return True


def clean_display_text(text: str) -> str:
    """Drop section markers and parenthetical instruction lines from display."""
    out: list[str] = []
    for line in (text or "").splitlines():
        s = line.strip()
        if re.match(r"^---\w+---$", s, re.I):
            continue
        if s.startswith("(") and s.endswith(")") and len(s) < 140:
            continue
        low = s.lower()
        if low.startswith("add ") and "what do you want" in low:
            continue
        if "end the scenario section by asking" in low:
            continue
        out.append(line)
    return "\n".join(out).strip()


def extract_scene_body(raw: str) -> str:
    """Best-effort SCENE extraction for narration / life sim display."""
    parts = parse_tagged_sections(raw, "STORY_BIBLE", "SCENE", "SCENARIO", "CHOICES", "DELTAS", "ENDING", "SUMMARY")
    scene = (parts.get("SCENARIO") or parts.get("SCENE") or "").strip()
    if scene and not is_placeholder_section(scene):
        return clean_display_text(scene)
    if re.search(r"-{2,}\s*CHOICES\b", raw, re.I):
        head = re.split(r"-{2,}\s*CHOICES\b", raw, maxsplit=1, flags=re.I)[0]
        head = re.sub(r"-{2,}\s*STORY_BIBLE\b.*?(?=-{2,}\s*SCENE\b|$)", "", head, flags=re.I | re.S)
        head = re.sub(r"-{2,}\s*SCENE\s*-*", "", head, flags=re.I)
        cleaned = clean_display_text(head)
        if cleaned and not is_placeholder_section(cleaned):
            return cleaned
    cleaned = clean_display_text(raw)
    if cleaned and not is_placeholder_section(cleaned):
        return cleaned
    return ""


def extract_choices_block(raw: str) -> str:
    parts = parse_tagged_sections(raw, "CHOICES")
    block = (parts.get("CHOICES") or "").strip()
    if block:
        return block
    if re.search(r"-{2,}\s*CHOICES\b", raw, re.I):
        tail = re.split(r"-{2,}\s*CHOICES\s*-*", raw, maxsplit=1, flags=re.I)[1]
        if re.search(r"-{2,}\s*ENDING\b", tail, re.I):
            tail = re.split(r"-{2,}\s*ENDING\b", tail, maxsplit=1, flags=re.I)[0]
        return tail.strip()
    return ""


def extract_narration_scene(raw: str) -> str:
    """SCENE-only text for narration chat (never SUMMARY/BIBLE/CHOICES)."""
    body = (raw or "").strip()
    if not body:
        return ""
    if not re.search(r"-{2,}\s*SCENE\b", body, re.I):
        return ""
    parts = parse_tagged_sections(body, "STORY_BIBLE", "SUMMARY", "SCENE", "CHOICES", "ENDING")
    scene = (parts.get("SCENE") or "").strip()
    if not scene or is_placeholder_section(scene):
        tail = re.split(r"-{2,}\s*SCENE\s*-*", body, maxsplit=1, flags=re.I)
        if len(tail) < 2:
            return ""
        rest = tail[1]
        for marker in (r"-{2,}\s*CHOICES\b", r"-{2,}\s*ENDING\b"):
            rest = re.split(marker, rest, maxsplit=1, flags=re.I)[0]
        scene = rest.strip()
    scene = clean_display_text(scene)
    if not scene or is_placeholder_section(scene):
        return ""
    deduped, _ = dedupe_paragraphs(scene)
    return deduped


def extract_narration_choices(raw: str) -> list[str]:
    """Parse up to 3 actionable choices from narration LLM output."""
    block = extract_choices_block(raw)
    choices = parse_choice_list(block)
    if len(choices) >= 3:
        return choices[:3]
    if not choices and block:
        for ln in block.splitlines():
            s = ln.strip()
            if not s:
                continue
            m = re.match(r"^\d+[\).\]]\s+(.+)$", s)
            if m:
                s = m.group(1).strip()
            if s and not is_placeholder_section(s):
                choices.append(s)
    out: list[str] = []
    for c in choices:
        low = c.lower()
        if low.startswith("new distinct option") or low.startswith("new option"):
            continue
        if c not in out:
            out.append(c)
    return out[:3]


def extract_keyed_choices(raw: str) -> list[tuple[str, str]]:
    """Parse A/B/C labeled choices from narration LLM output. Handles both one-per-line
    and inline ('A: … B: … C: …') forms — local models often put all three on one line."""
    block = extract_choices_block(raw)
    keyed: list[tuple[str, str]] = []
    seen: set[str] = set()
    # Capture each 'A:/B:/C:' and its text up to the next such key or the end. \s matches
    # newlines (re.S), so this covers both inline and one-per-line layouts.
    pat = r"\b([ABC])\s*[:.)\-–]\s*(.+?)(?=(?:\s+[ABC]\s*[:.)\-–]\s)|\Z)"
    for m in re.finditer(pat, block or "", re.S):
        key = m.group(1).upper()
        label = re.sub(r"\s+", " ", m.group(2)).strip().strip('"').strip("'").strip("<>").strip()
        if not label or is_placeholder_section(label) or key in seen:
            continue
        seen.add(key)
        keyed.append((key, label))
    order = {"A": 0, "B": 1, "C": 2}
    keyed.sort(key=lambda x: order.get(x[0], 9))
    return keyed


def parse_choice_list(block: str) -> list[str]:
    """Parse numbered choices; strips angle-bracket wrappers."""
    out: list[str] = []
    for ln in (block or "").splitlines():
        s = ln.strip()
        if not s:
            continue
        if s[0].isdigit() and "." in s:
            s = s.split(".", 1)[1].strip()
        elif s.startswith("-"):
            s = s[1:].strip()
        if s.startswith("<") and s.endswith(">"):
            s = s[1:-1].strip()
        if not s or s in out:
            continue
        low = s.lower()
        if low.startswith("new distinct option") or low.startswith("<new"):
            continue
        out.append(s)
    return out[:3]


def stream_preview_text(raw: str) -> str:
    """Display-safe partial text while streaming (hide tags / prompts)."""
    body = extract_scene_body(raw)
    if body:
        return body
    return clean_display_text(raw)


def dedupe_paragraphs(text: str) -> tuple[str, int]:
    """Drop consecutive identical paragraphs; return (cleaned, removed_count)."""
    parts = [p.strip() for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip()]
    if not parts:
        return (text or "").strip(), 0
    out: list[str] = []
    removed = 0
    for part in parts:
        key = re.sub(r"\s+", " ", part.lower()).strip()
        if out:
            prev_key = re.sub(r"\s+", " ", out[-1].lower()).strip()
            if key == prev_key:
                removed += 1
                continue
        out.append(part)
    return "\n\n".join(out), removed


def _sentence_tokens(text: str) -> set[str]:
    sentences = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    tokens: set[str] = set()
    for sent in sentences:
        for tok in re.findall(r"[a-z0-9\u4e00-\u9fff]+", sent.lower()):
            if len(tok) > 2:
                tokens.add(tok)
    return tokens


def text_overlap_ratio(a: str, b: str) -> float:
    """Jaccard overlap on sentence word tokens."""
    ta, tb = _sentence_tokens(a), _sentence_tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def strip_repeated_paragraphs(
    new_text: str, prior_texts: list[str], *, threshold: float = 0.6
) -> str:
    """Drop paragraphs from new_text that substantially overlap any earlier chapter's text.

    Catches an LLM "recap" (re-narrating chapter 1/2 before continuing to chapter 3)
    deterministically, since prompt instructions alone aren't reliable — especially on
    smaller local models that tend to summarize everything they're given as context.
    """
    prior_paragraphs: list[str] = []
    for t in prior_texts:
        prior_paragraphs.extend(p.strip() for p in re.split(r"\n\s*\n", (t or "").strip()) if p.strip())
    prior_token_sets = [_sentence_tokens(p) for p in prior_paragraphs]
    prior_token_sets = [s for s in prior_token_sets if s]

    new_paragraphs = [p.strip() for p in re.split(r"\n\s*\n", (new_text or "").strip()) if p.strip()]
    if not new_paragraphs or not prior_token_sets:
        return (new_text or "").strip()

    kept: list[str] = []
    for p in new_paragraphs:
        toks = _sentence_tokens(p)
        if not toks:
            kept.append(p)
            continue
        is_repeat = any(len(toks & prior) / len(toks) >= threshold for prior in prior_token_sets)
        if not is_repeat:
            kept.append(p)
    if not kept:
        # Everything looked like a repeat — safer to show the original than nothing.
        return (new_text or "").strip()
    return "\n\n".join(kept)


def parse_stat_deltas(block: str) -> dict[str, int]:
    """Parse morale/supplies/safety deltas from DELTAS section."""
    deltas: dict[str, int] = {}
    text = (block or "").strip()
    if not text:
        return deltas
    for m in re.finditer(
        r"(morale|supplies|safety)\s*[:=]\s*([+-]?\d+)",
        text,
        re.I,
    ):
        deltas[m.group(1).lower()] = int(m.group(2))
    if deltas:
        return deltas
    for part in re.split(r"[\s,]+", text):
        kv = re.match(r"(morale|supplies|safety):([+-]?\d+)", part, re.I)
        if kv:
            deltas[kv.group(1).lower()] = int(kv.group(2))
    return deltas


def parse_bullet_list(block: str) -> list[str]:
    out: list[str] = []
    for line in (block or "").splitlines():
        s = line.strip()
        if not s:
            continue
        s = re.sub(r"^[-*•]\s*", "", s)
        s = re.sub(r"^\d+[\).\]]\s*", "", s)
        if s and not is_placeholder_section(s):
            out.append(s)
    return out[:6]


def format_debate_scores(scores: str, a_label: str, b_label: str) -> str:
    """Expand A=4/3/5 B=2/4/3 into named Logic/Evidence/Rebuttal lines."""
    raw = (scores or "").strip()
    if not raw:
        return ""
    dims = ("Logic", "Evidence", "Rebuttal")
    lines: list[str] = []

    def _one(side: str, label: str) -> None:
        m = re.search(rf"{side}\s*=\s*([\d/]+)", raw, re.I)
        if not m:
            return
        nums = [n for n in m.group(1).split("/") if n.strip().isdigit()]
        parts = []
        for i, n in enumerate(nums[:3]):
            parts.append(f"{dims[i]} {n}/5")
        if parts:
            lines.append(f"**{label}:** " + " · ".join(parts))

    _one("A", a_label)
    _one("B", b_label)
    return "\n".join(lines)
