# -*- coding: utf-8 -*-
"""Normalize presentation drafts into canonical slide markdown for preview and export."""
from __future__ import annotations

import re

_STRUCTURED_SLIDE_START = re.compile(
    r"^(?:(?:Final\s+)?Slide\s+\d+|Final\s+Slide)\s*[:\-]",
    re.MULTILINE | re.IGNORECASE,
)
_CANONICAL_SLIDE_MARKER = re.compile(
    r"^---\s*Slide\s+\d+\s*---\s*$",
    re.MULTILINE | re.IGNORECASE,
)
_BARE_SLIDE_SPLIT = re.compile(r"\n\s*---\s*\n", re.MULTILINE)
_SLIDE_HEADING_SPLIT = re.compile(r"(?=^##\s+)", re.MULTILINE)
_FIELD_TITLE = re.compile(r"^Title\s*:\s*(.*)$", re.IGNORECASE)
_FIELD_SUBTITLE = re.compile(r"^Subtitle\s*:\s*(.*)$", re.IGNORECASE)
_FIELD_CONTENT = re.compile(r"^Content\s*:\s*(.*)$", re.IGNORECASE)
_FIELD_DESIGN = re.compile(r"^(?:Design|Footer)\s*:\s*(.*)$", re.IGNORECASE)
_META_LABEL = re.compile(r"^(?:title|subtitle|content|design|footer)\s*:?\s*$", re.IGNORECASE)
_SLIDE_NUM_TITLE = re.compile(r"^slide\s+\d+\s*$", re.IGNORECASE)
_SLIDE_NUM_PREFIX = re.compile(r"^slide\s+\d+\s*[:\-\.]?\s*", re.IGNORECASE)
_THEME_COMMENT = re.compile(r"<!--\s*loma-theme:[^>]*-->", re.IGNORECASE)
_DESIGN_FENCE = re.compile(r"```(?:json|yaml)?\s*design\s*\n.*?```", re.DOTALL | re.IGNORECASE)
_NUMBERED_BULLET = re.compile(r"^\d+[\).\]]\s+")
_NUMBERED_SLIDE_HEADING = re.compile(
    r"^##\s+Slide\s+\d+\s*[:\-]\s*(.*)$",
    re.MULTILINE | re.IGNORECASE,
)
_NUMBERED_SLIDE_HEADING_SPLIT = re.compile(
    r"(?=^##\s+Slide\s+\d+\s*[:\-])",
    re.MULTILINE | re.IGNORECASE,
)


def _has_numbered_slide_headings(text: str) -> bool:
    return len(_NUMBERED_SLIDE_HEADING.findall(text or "")) >= 2


def _normalize_list_markers(text: str) -> str:
    """Normalize '*   item' and indented bullets to markdown '-' lists."""
    lines: list[str] = []
    for line in (text or "").splitlines():
        m = re.match(r"^(\s*)[\*\-]\s+(.+)$", line)
        if m:
            indent = m.group(1)
            body = m.group(2).strip()
            prefix = "  " if indent else ""
            lines.append(f"{prefix}- {body}")
        else:
            lines.append(line)
    return "\n".join(lines)


def _numbered_slide_headings_to_canonical(text: str) -> str:
    """
    Convert decks that use '## Slide N: Title' sections (often separated by ---)
    into canonical --- Slide N --- markdown.
    """
    body = _strip_embedded_meta(strip_code_fences(text or "")).strip()
    if not _has_numbered_slide_headings(body):
        return ""

    first = _NUMBERED_SLIDE_HEADING.search(body)
    preamble = body[: first.start()].strip() if first else ""
    deck_title = ""
    for line in preamble.splitlines():
        line = line.strip()
        if line.startswith("# ") and not line.startswith("## "):
            deck_title = sanitize_slide_title(line[2:])
            break

    parts = [
        p.strip()
        for p in _NUMBERED_SLIDE_HEADING_SPLIT.split(body)
        if p.strip() and _NUMBERED_SLIDE_HEADING.match(p.split("\n", 1)[0].strip())
    ]
    slide_blocks: list[str] = []
    for part in parts:
        lines = part.splitlines()
        header = lines[0].strip()
        hm = _NUMBERED_SLIDE_HEADING.match(header)
        if not hm:
            continue
        slide_label = hm.group(1).strip()
        slide_title = sanitize_slide_title(slide_label) or "Untitled"
        body_lines = _normalize_list_markers("\n".join(lines[1:]).strip())
        body_lines = "\n".join(
            ln
            for ln in body_lines.splitlines()
            if ln.strip() and not re.fullmatch(r"-{3,}", ln.strip())
        ).strip()
        if slide_label.lower() in ("title", "cover"):
            if body_lines:
                first_line = body_lines.split("\n", 1)[0].strip()
                bold = re.match(r"^\*\*(.+?)\*\*\s*$", first_line)
                if bold:
                    slide_title = sanitize_slide_title(bold.group(1)) or slide_title
                    body_lines = (
                        body_lines.split("\n", 1)[1].strip()
                        if "\n" in body_lines
                        else ""
                    )
            if deck_title and slide_title.lower() in ("title", "cover", ""):
                slide_title = deck_title
        if not body_lines and slide_title:
            slide_blocks.append(f"# {slide_title}")
            continue
        block = f"## {slide_title}"
        if body_lines:
            block += f"\n{body_lines}"
        slide_blocks.append(block.strip())

    if not slide_blocks:
        return ""

    first_title = _slide_title_from_block(slide_blocks[0]).lower()
    if deck_title:
        if first_title in ("title", "") or "title" in first_title:
            if not any(ln.startswith("# ") for ln in slide_blocks[0].splitlines()):
                slide_blocks[0] = f"# {deck_title}\n\n{slide_blocks[0]}"
        elif not _block_is_title_only(slide_blocks[0]):
            slide_blocks.insert(0, f"# {deck_title}")

    return _slides_to_canonical_markdown(
        pad_title_only_slides_blocks(slide_blocks, query=deck_title)
    )


def _strip_embedded_meta(text: str) -> str:
    body = _THEME_COMMENT.sub("", text or "")
    body = _DESIGN_FENCE.sub("", body)
    return re.sub(r"\n{3,}", "\n\n", body).strip()


def _is_slide_block(chunk: str) -> bool:
    chunk = (chunk or "").strip()
    if not chunk:
        return False
    if chunk.startswith("<!--"):
        return False
    return bool(
        re.match(r"^#+\s+", chunk)
        or chunk.startswith(("- ", "* "))
        or _NUMBERED_BULLET.match(chunk)
    )


def _dedupe_repeated_deck(slides: list[str]) -> list[str]:
    slides = _truncate_restart_duplicate(slides)
    n = len(slides)
    if n < 6 or n % 2 != 0:
        return slides
    half = n // 2
    a = [_normalize_slide_title_key(s) for s in slides[:half]]
    b = [_normalize_slide_title_key(s) for s in slides[half:]]
    if a and a == b:
        return slides[:half]
    return slides


def _normalize_slide_title_key(block: str) -> str:
    t = _slide_title_from_block(block).lower()
    t = re.sub(r"^slide\s+\d+\s*[:\-\.]?\s*", "", t).strip()
    t = re.sub(r"^(title\s*slide|untitled)\s*$", "", t).strip()
    return t


def _truncate_restart_duplicate(slides: list[str]) -> list[str]:
    """Drop a second deck when the LLM restarts with 'Slide 1: Title Slide' etc."""
    if len(slides) < 6:
        return slides
    for i in range(3, len(slides)):
        title = _slide_title_from_block(slides[i]).lower()
        if not title:
            continue
        if re.search(r"\bslide\s*1\b", title) and "title" in title:
            return slides[:i]
        if i >= len(slides) // 2 and re.match(r"^slide\s*1\s*[:\-\.]", title):
            return slides[:i]
    return slides


def _strip_trailing_script_after_markers(text: str) -> str:
    """Remove trailing 'Slide N: Title' script blocks after canonical markdown."""
    if not _has_canonical_slide_markers(text):
        return text
    m = list(_CANONICAL_SLIDE_MARKER.finditer(text))
    if not m:
        return text
    last_end = m[-1].end()
    tail = text[last_end:]
    if _STRUCTURED_SLIDE_START.search(tail):
        return text[:last_end].strip()
    if re.search(r"(?m)^Slide\s+\d+\s*[:\-]", tail):
        cut = _STRUCTURED_SLIDE_START.search(tail)
        if cut:
            return text[: last_end + cut.start()].strip()
    return text


def _is_agenda_title(title: str) -> bool:
    """True for 'Agenda', 'Outline', or 'Agenda: …' / 'Outline — …'."""
    return bool(re.match(r"^(agenda|outline)\b", (title or "").strip(), re.I))


_GENERIC_FILLER_BULLETS = {
    "review the main ideas from this section",
    "apply one takeaway in your classroom or home",
    "review the main ideas from this presentation",
    "apply one takeaway this week",
}


def _slide_bullet_texts(block: str) -> list[str]:
    out: list[str] = []
    for ln in (block or "").splitlines():
        s = ln.strip()
        if s.startswith(("- ", "* ")):
            out.append(s[2:].strip())
        else:
            m = _NUMBERED_BULLET.match(s)
            if m:
                out.append(s[m.end() :].strip())
    return out


def _bullets_are_generic_filler(bullets: list[str]) -> bool:
    if not bullets:
        return False
    return all(
        re.sub(r"[.!?]+$", "", b.strip().lower()) in _GENERIC_FILLER_BULLETS
        for b in bullets
    )


def _is_junk_slide_block(block: str, *, all_titles: list[str]) -> bool:
    """Drop LLM/repair filler: Section N, bare Agenda TOC, generic closing stubs."""
    title = _slide_title_from_block(block)
    tl = (title or "").strip().lower()
    if not tl:
        return True
    if re.fullmatch(r"section\s+\d+", tl):
        return True
    if re.fullmatch(r"key\s*topic\s+\d+", tl):
        return True
    bullets = _slide_bullet_texts(block)
    if _bullets_are_generic_filler(bullets):
        return True
    # Bare "Agenda"/"Outline" when a richer "Agenda: …" slide ALSO exists —
    # a genuine duplicate (two agenda-shaped slides), not just "an agenda
    # whose bullets happen to match the other slide titles". That second
    # case used to also be treated as junk here, but a correct agenda is
    # *supposed* to list the same section names as the content slides it
    # summarizes — that's not a duplicate, it's the whole point of an
    # agenda. That over-broad check was silently deleting every legitimate
    # agenda slide the structured deck pipeline produces (confirmed via a
    # real run: "10 slides" requested and confirmed in chat, compiled to 9
    # because the agenda slide got scrubbed as "junk").
    if tl in ("agenda", "outline") and any(
        _is_agenda_title(t) and t.strip().lower() not in ("agenda", "outline")
        for t in all_titles
    ):
        return True
    return False


def scrub_junk_slides(slides: list[str]) -> list[str]:
    """Remove placeholder / duplicate-filler slides from a split deck."""
    slides = [s for s in slides if _is_slide_block(s)]
    if not slides:
        return slides
    titles = [_slide_title_from_block(s) for s in slides]
    kept: list[str] = []
    for i, block in enumerate(slides):
        if i == 0:
            kept.append(block)
            continue
        if _is_junk_slide_block(block, all_titles=titles):
            continue
        kept.append(block)
    return kept if kept else slides[:1]


def is_well_structured_deck(slides: list[str]) -> bool:
    """Title slide + agenda + at least one content slide with bullets."""
    slides = [s for s in slides if _is_slide_block(s)]
    if len(slides) < 4:
        return False
    if not _block_is_title_only(slides[0]):
        return False
    agenda_title = _slide_title_from_block(slides[1])
    if not _is_agenda_title(agenda_title):
        return False
    if not any(ln.strip().startswith(("- ", "* ")) for ln in slides[1].splitlines()):
        return False
    content_with_bullets = 0
    for block in slides[2:]:
        if any(ln.strip().startswith(("- ", "* ")) for ln in block.splitlines()):
            content_with_bullets += 1
    return content_with_bullets >= 1


def _slides_to_canonical_markdown(slides: list[str]) -> str:
    lines: list[str] = []
    for idx, block in enumerate(slides, start=1):
        lines.append(f"--- Slide {idx} ---")
        lines.append(block.strip())
        lines.append("")
    return "\n".join(lines).strip()


def sanitize_slide_title(text: str) -> str:
    """Remove meta labels and generic slide-number placeholders from titles."""
    t = (text or "").strip()
    if not t:
        return ""
    t = re.sub(r"^(?:title|subtitle)\s*:\s*", "", t, flags=re.IGNORECASE).strip()
    t = _SLIDE_NUM_PREFIX.sub("", t).strip()
    t = re.sub(r"^slide\s+\d+\s*[:\-\.]\s*", "", t, flags=re.I).strip()
    if _SLIDE_NUM_TITLE.match(t):
        return ""
    if re.fullmatch(r"slide\s+\d+", t, re.I):
        return ""
    return t


def sanitize_bullet_text(text: str) -> str:
    """Strip field labels from bullet/body lines."""
    t = (text or "").strip()
    if not t:
        return ""
    for prefix in ("Title:", "Subtitle:", "Content:", "Design:", "Footer:"):
        if t.lower().startswith(prefix.lower()):
            return t[len(prefix) :].strip()
    if _META_LABEL.match(t):
        return ""
    return t


def strip_code_fences(text: str) -> str:
    """Remove a single outer ```lang ... ``` wrapper if present."""
    body = (text or "").strip()
    if not body.startswith("```"):
        return body
    lines = body.split("\n")
    end = len(lines)
    if lines[-1].strip() == "```":
        end = len(lines) - 1
    return "\n".join(lines[1:end]).strip()


def _has_canonical_slide_markers(text: str) -> bool:
    return bool(_CANONICAL_SLIDE_MARKER.search(text))


def _has_structured_script(text: str) -> bool:
    return bool(_STRUCTURED_SLIDE_START.search(text))


def _parse_structured_slide_block(block: str) -> tuple[str, str, list[str]]:
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    title = ""
    subtitle = ""
    bullets: list[str] = []
    section: str | None = None

    for line in lines[1:]:
        title_match = _FIELD_TITLE.match(line)
        if title_match:
            title = title_match.group(1).strip()
            section = None
            continue
        subtitle_match = _FIELD_SUBTITLE.match(line)
        if subtitle_match:
            subtitle = subtitle_match.group(1).strip()
            section = None
            continue
        content_match = _FIELD_CONTENT.match(line)
        if content_match:
            section = "content"
            rest = content_match.group(1).strip()
            if rest:
                bullets.append(rest)
            continue
        design_match = _FIELD_DESIGN.match(line)
        if design_match:
            section = "design"
            rest = design_match.group(1).strip()
            if rest and rest.startswith(('"', "'")):
                bullets.append(rest.strip("\"'"))
            continue
        if section == "design":
            continue
        if section == "content":
            if line.startswith("- "):
                bullets.append(line[2:].strip())
            else:
                bullets.append(line)
            continue
        if line.startswith("- "):
            bullets.append(line[2:].strip())

    return title, subtitle, bullets


def _structured_script_to_markdown(text: str) -> str:
    parts = _STRUCTURED_SLIDE_START.split(text)
    headers = _STRUCTURED_SLIDE_START.findall(text)
    if not headers:
        return text

    deck_title = ""
    slide_sections: list[str] = []
    slide_num = 0

    for header, body in zip(headers, parts[1:]):
        block = f"{header}{body}"
        title, subtitle, bullets = _parse_structured_slide_block(block)
        slide_num += 1
        if slide_num == 1 and title and not deck_title:
            deck_title = title

        lines = [f"--- Slide {slide_num} ---", f"## {title or 'Untitled'}"]
        if subtitle:
            lines.append(f"- {subtitle}")
        for bullet in bullets:
            lines.append(f"- {bullet}")
        slide_sections.append("\n".join(lines))

    prefix = f"# {deck_title}\n\n" if deck_title else ""
    return prefix + "\n\n".join(slide_sections)


def _block_is_title_only(block: str) -> bool:
    lines = [ln.strip() for ln in (block or "").splitlines() if ln.strip()]
    if not lines:
        return False
    if any(ln.startswith("## ") for ln in lines):
        return False
    bullets = sum(1 for ln in lines if ln.startswith(("- ", "* ")))
    if bullets > 1:
        return False
    return any(ln.startswith("# ") for ln in lines)


def split_presentation_slides(text: str) -> list[str]:
    """Split normalized presentation markdown into per-slide blocks."""
    body = _strip_embedded_meta((text or "").strip())
    if not body:
        return []

    if _has_canonical_slide_markers(body):
        first_marker = _CANONICAL_SLIDE_MARKER.search(body)
        preamble = body[: first_marker.start()].strip() if first_marker else ""
        deck_title = ""
        if preamble:
            for line in preamble.splitlines():
                line = line.strip()
                if line.startswith("# "):
                    deck_title = sanitize_slide_title(line[2:])
                    break
        slide_body = body[first_marker.start() :] if first_marker else body
        parts = _CANONICAL_SLIDE_MARKER.split(slide_body)
        blocks = [p.strip() for p in parts if _is_slide_block(p)]
        if blocks and deck_title and not _slide_title_from_block(blocks[0]):
            blocks[0] = f"# {deck_title}\n\n{blocks[0]}".strip()
        return _dedupe_repeated_deck(blocks)

    parts = _BARE_SLIDE_SPLIT.split(body)
    blocks = [p.strip() for p in parts if _is_slide_block(p)]

    if len(blocks) <= 1:
        heading_parts = [p.strip() for p in _SLIDE_HEADING_SPLIT.split(body) if p.strip()]
        if len(heading_parts) > 1:
            deck_title = ""
            if heading_parts[0].startswith("# "):
                deck_title = heading_parts[0].split("\n", 1)[0].strip()
                heading_parts = heading_parts[1:]
            blocks = heading_parts
            if deck_title and blocks:
                blocks[0] = f"{deck_title}\n\n{blocks[0]}"

    return _dedupe_repeated_deck(blocks)


def _slide_title_from_block(block: str) -> str:
    for line in block.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return sanitize_slide_title(line[2:].strip())
        if line.startswith("## "):
            return sanitize_slide_title(line[3:].strip())
    return ""


def _outline_bullets_from_slides(slides: list[str]) -> list[str]:
    bullets: list[str] = []
    for block in slides[2:] if len(slides) > 2 else slides[1:]:
        title = _slide_title_from_block(block)
        tl = (title or "").strip().lower()
        if not title or _is_agenda_title(title):
            continue
        if tl in ("summary", "conclusion") or "next steps" in tl or "takeaway" in tl:
            continue
        if re.fullmatch(r"section\s+\d+", tl) or re.fullmatch(r"key\s*topic\s+\d+", tl):
            continue
        bullets.append(title)
        if len(bullets) >= 6:
            break
    return bullets or ["Key themes from the source material"]


def _synthetic_bullets(title: str, *, query: str = "") -> list[str]:
    """Fallback bullets when the author only produced a heading."""
    combined = f"{query} {title}".lower()
    if "kindness" in combined:
        return [
            "Small acts of kindness release oxytocin and strengthen social bonds",
            "One considerate gesture can improve a stranger's entire day",
            "Daily kindness habits compound into lasting community trust",
        ]
    t = (title or "this topic").strip()
    short = t[:60]
    return [
        f"Clarify why {short.lower()} matters to your audience",
        f"Give one concrete example that illustrates {short.lower()}",
        f"End with a specific action listeners can take this week",
    ]


def pad_title_only_slides(text: str, *, query: str = "") -> str:
    """Add bullets to content slides that only have a ## heading."""
    slides = split_presentation_slides(text)
    if not slides:
        return text
    rebuilt: list[str] = []
    for idx, block in enumerate(slides):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        has_bullet = any(
            ln.startswith(("- ", "* ")) or _NUMBERED_BULLET.match(ln) for ln in lines
        )
        title = _slide_title_from_block(block)
        is_outline = _is_agenda_title(title) or title.lower() in ("summary", "conclusion")
        is_title_slide = idx == 0 and _block_is_title_only(block)
        if not is_title_slide and not is_outline and not has_bullet and title:
            block = block.rstrip() + "\n" + "\n".join(
                f"- {b}" for b in _synthetic_bullets(title, query=query)
            )
        rebuilt.append(block.strip())
    return _slides_to_canonical_markdown(rebuilt)


def ensure_deck_structure(text: str, *, deck_title: str = "") -> str:
    """Guarantee slide 1 = title, slide 2 = agenda; emit canonical --- Slide N --- markdown."""
    normalized = normalize_presentation_markdown(text)
    slides = split_presentation_slides(normalized)
    slides = [s for s in slides if _is_slide_block(s)]
    slides = _dedupe_repeated_deck(slides)
    slides = scrub_junk_slides(slides)

    if is_well_structured_deck(slides):
        return _slides_to_canonical_markdown(
            scrub_junk_slides(pad_title_only_slides_blocks(slides, query=deck_title))
        )

    if not slides:
        title = (deck_title or "Presentation").strip() or "Presentation"
        return _slides_to_canonical_markdown(
            [f"# {title}", "## Agenda\n- Key topics\n- Main insights\n- Conclusion"]
        )

    first_title = _slide_title_from_block(slides[0])
    first_lines = [ln.strip() for ln in slides[0].splitlines() if ln.strip()]
    first_bullets = sum(1 for ln in first_lines if ln.startswith(("- ", "* ")))
    needs_title = (
        first_bullets > 1
        or not first_title
        or not _block_is_title_only(slides[0])
    )

    query_title = (deck_title or "").strip()
    use_query_title = bool(query_title) and len(query_title.split()) <= 2
    title = first_title or (query_title if use_query_title else "") or "Presentation"
    rebuilt: list[str] = []

    if needs_title:
        subtitle = ""
        if first_bullets == 1:
            for ln in first_lines:
                if ln.startswith(("- ", "* ")):
                    subtitle = ln[2:].strip()
                    break
        title_block = f"# {title}"
        if subtitle:
            title_block += f"\n- {subtitle}"
        rebuilt.append(title_block)
        body_slides = slides
    else:
        rebuilt.append(slides[0].strip())
        body_slides = slides[1:]

    second_title = _slide_title_from_block(body_slides[0]) if body_slides else ""
    if not body_slides or not _is_agenda_title(second_title):
        outline_items = _outline_bullets_from_slides(rebuilt + body_slides)
        rebuilt.append("## Agenda\n" + "\n".join(f"- {b}" for b in outline_items))
        rebuilt.extend(s.strip() for s in body_slides if s.strip())
    else:
        rebuilt.extend(s.strip() for s in body_slides if s.strip())

    rebuilt = scrub_junk_slides(pad_title_only_slides_blocks(rebuilt, query=deck_title))
    return _slides_to_canonical_markdown(rebuilt)


def pad_title_only_slides_blocks(slides: list[str], *, query: str = "") -> list[str]:
    """Pad heading-only slides in an already-split deck."""
    out: list[str] = []
    for idx, block in enumerate(slides):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        has_bullet = any(
            ln.startswith(("- ", "* ")) or _NUMBERED_BULLET.match(ln) for ln in lines
        )
        title = _slide_title_from_block(block)
        is_outline = _is_agenda_title(title) or title.lower() in ("summary", "conclusion")
        is_title_slide = idx == 0 and _block_is_title_only(block)
        if not is_title_slide and not is_outline and not has_bullet and title:
            block = block.rstrip() + "\n" + "\n".join(
                f"- {b}" for b in _synthetic_bullets(title, query=query)
            )
        out.append(block.strip())
    return out


def extract_compile_markdown(text: str) -> str:
    """Pull slide markdown from mixed LLM output (JSON plan + markdown deck)."""
    body = (text or "").strip()
    if not body:
        return ""
    if _has_canonical_slide_markers(body):
        m = _CANONICAL_SLIDE_MARKER.search(body)
        if m and m.start() > 0:
            body = body[m.start() :]
        return normalize_presentation_markdown(body)
    if body.lstrip().startswith("{"):
        from pipeline.deliverables.presentation_deck import parse_deck_spec

        spec, _ = parse_deck_spec(body)
        if spec:
            return normalize_presentation_markdown(spec.to_markdown())
    return normalize_presentation_markdown(body)


def normalize_presentation_markdown(text: str) -> str:
    """
    Convert LLM presentation drafts into canonical markdown:
    # Deck title

    --- Slide 1 ---
    ## Slide title
    - bullet
    """
    body = _strip_embedded_meta(strip_code_fences(text or ""))
    if not body.strip():
        return ""

    if _has_canonical_slide_markers(body):
        body = _strip_trailing_script_after_markers(body)
        slides = split_presentation_slides(body)
        if slides:
            return _slides_to_canonical_markdown(slides)
        return body.strip()
    if _has_structured_script(body):
        return _structured_script_to_markdown(body).strip()
    if _has_numbered_slide_headings(body):
        converted = _numbered_slide_headings_to_canonical(body)
        if converted:
            return converted.strip()

    return body.strip()


_VISUAL_BULLET_RE = re.compile(
    r"^(?:\*\*)?visuals?(?:\*\*)?\s*:\s*(.+)$",
    re.IGNORECASE,
)
_SPEAKER_NOTES_BULLET_RE = re.compile(
    r"^(?:\*\*)?speaker\s*notes?(?:\*\*)?\s*:\s*(.+)$",
    re.IGNORECASE,
)
_NOTES_LINE_RE = re.compile(r"^\[NOTES:\s*(.+?)\]\s*$", re.IGNORECASE)
# Optional surrounding **bold**/__bold__ markers — the authoring system prompt
# asks for a plain "[NOTES: ...]" line, but models routinely write
# "**Speaker Notes:** ..." instead (bolding the label like regular prose),
# which the unanchored form above never matched, silently losing every note.
_SPEAKER_NOTES_LINE_RE = re.compile(
    r"^(?:\*\*|__)?speaker\s*notes?(?:\*\*|__)?\s*:\s*(?:\*\*|__)?\s*(.+)$",
    re.IGNORECASE,
)


def parse_speaker_notes_line(line: str) -> str:
    """Extract speaker notes from a dedicated markdown line, if any."""
    text = (line or "").strip()
    if not text:
        return ""
    m = _NOTES_LINE_RE.match(text)
    if m:
        return m.group(1).strip()
    m = _SPEAKER_NOTES_LINE_RE.match(text)
    if m:
        return m.group(1).strip()
    return ""


def partition_slide_extras(
    bullets: list[str],
) -> tuple[list[str], str, str]:
    """Split slide bullets into visible content, image description, and speaker notes."""
    content: list[str] = []
    image_desc = ""
    notes_parts: list[str] = []
    for raw in bullets:
        text = (raw or "").strip()
        text = re.sub(r"^[-*]\s+", "", text)
        if not text:
            continue
        # Strip a leading bold label whether the colon sits inside or outside the
        # closing `**` (models write both "**Label**:" and "**Label:**").
        text = re.sub(r"^\*\*(.+?):\*\*\s*", r"\1: ", text, count=1)
        text = re.sub(r"^\*\*(.+?)\*\*\s*:\s*", r"\1: ", text, count=1)
        vm = re.match(r"^visuals?\s*:\s*(.+)$", text, re.IGNORECASE)
        if vm:
            image_desc = vm.group(1).strip()
            continue
        sm = re.match(r"^speaker\s*notes?\s*:\s*(.+)$", text, re.IGNORECASE)
        if sm:
            notes_parts.append(sm.group(1).strip())
            continue
        content.append((raw or "").strip())
    return content, image_desc, "\n\n".join(notes_parts)
