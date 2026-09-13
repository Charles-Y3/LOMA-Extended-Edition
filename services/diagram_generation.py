# -*- coding: utf-8 -*-
"""Flowchart/process diagram rendering: no diffusion at all. An LLM authors a small
directed graph (nodes + edges), then a deterministic PIL layered layout (a simplified
Sugiyama-style algorithm — rank by longest path, order within rank by barycenter,
then assign coordinates) draws real labeled boxes and routed arrows — diffusion
models don't understand structured diagrams (see pipeline/direct/image_intent.py for
why this path exists).

This supports genuine branching (decision nodes with labeled Yes/No-style edges),
merges (multiple edges into one node), parallel fan-out/fan-in, and a single simple
retry/loop-back edge. Graphs beyond a modest size (see _MAX_NODES/_MAX_EDGES/
_MAX_LOOP_BACK) degrade to a short linear chain with a limitation note rather than
attempting a layout that would come out cramped or overlapping — the same "be honest
about the ceiling" approach used elsewhere in this renderer.

Rendering is supersampled (drawn at _SCALE x the final size, then downsampled with
LANCZOS) so rounded corners, diamonds and arrow diagonals come out anti-aliased
instead of jagged raster edges."""
from __future__ import annotations

import json
import math
import os
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from services.image_generation import GENERATED_IMAGE_DIR, ImageGenerationResult, unique_output_path

_DIAGRAM_AUTHOR_SYSTEM = """You are LOMA's Diagram Author. Return ONLY valid JSON (no markdown fences).

Given the user's flowchart/process/workflow request, produce a small directed graph:
{
  "title": "short diagram title, in the SAME language as the user's request",
  "nodes": [
    {"id": "n1", "title": "short step name (1-4 words)", "description": "one short phrase, optional", "type": "start|process|decision|end"}
  ],
  "edges": [
    {"source": "n1", "target": "n2", "label": "optional short label, e.g. Yes/No for a decision branch", "is_loop_back": false}
  ]
}

Rules:
- Every node needs a unique short "id" (e.g. "n1", "n2") referenced by edges.
- Represent the ACTUAL structure the user describes: a straight sequence is a chain
  of edges n1->n2->n3; a decision/branch is one "decision" node with TWO OR MORE
  outgoing edges, each labeled with the short condition (in the user's own language,
  e.g. "Yes"/"No"); parallel work is two nodes both fed by the same source and both
  feeding the same downstream node; a retry/repeat is a single edge back to an
  earlier node with "is_loop_back": true.
- type: "start" for an entry node, "end" for a terminal node, "decision" for a
  branching check (keep its title very short, e.g. "Valid?"), "process" otherwise —
  if unsure, use "process"; the renderer infers start/end from graph structure.
- Keep the graph small and readable: at most 14 nodes, at most 18 edges, at most 1
  loop-back edge. Do not invent more structure than the user's request supports.
- title/step/label text must be in the user's own language, not translated to English.
"""

_VALID_TYPES = {"start", "process", "decision", "end"}
_MAX_NODES, _MAX_EDGES, _MAX_LOOP_BACK = 14, 18, 1

# Supersample scale: everything is laid out/drawn at this multiple of the final pixel
# size, then LANCZOS-downsampled once at the end — cheap way to get anti-aliased
# shapes/text out of PIL's otherwise-aliased raster drawing primitives.
_SCALE = 3

_MIN_BOX_W, _MAX_BOX_W = 200, 340
_MIN_BOX_H = 110


@dataclass
class DiagramNode:
    id: str
    title: str
    description: str = ""
    type: str = "process"


@dataclass
class DiagramEdge:
    source: str
    target: str
    label: str = ""
    is_loop_back: bool = False


@dataclass
class DiagramSpec:
    title: str
    nodes: list[DiagramNode] = field(default_factory=list)
    edges: list[DiagramEdge] = field(default_factory=list)
    is_supported: bool = True


def _parse_json(raw: str) -> dict | None:
    text = (raw or "").strip()
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _mark_cycle_edges(nodes: list[DiagramNode], edges: list[DiagramEdge]) -> None:
    """DFS-based back-edge detection: any edge that would create a cycle is forced
    into is_loop_back so rank assignment always receives a clean DAG, even if the
    author LLM forgot to flag a retry loop itself."""
    adjacency: dict[str, list[DiagramEdge]] = defaultdict(list)
    for e in edges:
        adjacency[e.source].append(e)

    visited: set[str] = set()
    in_stack: set[str] = set()

    def dfs(nid: str) -> None:
        visited.add(nid)
        in_stack.add(nid)
        for e in adjacency.get(nid, []):
            if e.is_loop_back:
                continue
            if e.target in in_stack:
                e.is_loop_back = True
            elif e.target not in visited:
                dfs(e.target)
        in_stack.discard(nid)

    for n in nodes:
        if n.id not in visited:
            dfs(n.id)


def _assign_ranks(nodes: list[DiagramNode], edges: list[DiagramEdge]) -> dict[str, int]:
    """Longest-path-from-source ranking over the DAG (loop-back edges excluded) —
    becomes the primary layout axis (columns, left to right)."""
    outgoing: dict[str, list[str]] = {n.id: [] for n in nodes}
    indegree: dict[str, int] = {n.id: 0 for n in nodes}
    for e in edges:
        if e.is_loop_back or e.source not in outgoing or e.target not in indegree:
            continue
        outgoing[e.source].append(e.target)
        indegree[e.target] += 1

    rank: dict[str, int] = {n.id: 0 for n in nodes}
    queue = deque([nid for nid, d in indegree.items() if d == 0])
    seen = set(queue)
    processed = 0
    while queue:
        nid = queue.popleft()
        processed += 1
        for tgt in outgoing[nid]:
            rank[tgt] = max(rank[tgt], rank[nid] + 1)
            indegree[tgt] -= 1
            if indegree[tgt] == 0 and tgt not in seen:
                seen.add(tgt)
                queue.append(tgt)

    # A node Kahn's algorithm couldn't reach (a cycle _mark_cycle_edges somehow
    # missed) still gets a sane rank instead of crashing layout.
    if processed < len(nodes):
        max_rank = max(rank.values(), default=0)
        for n in nodes:
            if n.id not in seen:
                rank[n.id] = max_rank + 1
    return rank


def _order_within_ranks(
    nodes: list[DiagramNode], edges: list[DiagramEdge], rank: dict[str, int]
) -> dict[int, list[str]]:
    """Barycenter heuristic: nodes sharing a rank are ordered by the average
    row-position of their neighbors in the adjacent rank, iterated a few passes —
    enough to keep simple diamond/fan patterns crossing-free without a full
    Brandes-Köpf pass."""
    by_rank: dict[int, list[str]] = defaultdict(list)
    for n in nodes:
        by_rank[rank[n.id]].append(n.id)
    max_rank = max(rank.values(), default=0)
    order: dict[int, list[str]] = {r: list(ids) for r, ids in by_rank.items()}

    incoming: dict[str, list[str]] = defaultdict(list)
    outgoing: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        if not e.is_loop_back:
            outgoing[e.source].append(e.target)
            incoming[e.target].append(e.source)

    pos: dict[str, int] = {}

    def refresh_pos() -> None:
        for ids in order.values():
            for i, nid in enumerate(ids):
                pos[nid] = i

    refresh_pos()

    def barycenter_pass(forward: bool) -> None:
        neighbor_map = incoming if forward else outgoing
        rng = range(1, max_rank + 1) if forward else range(max_rank - 1, -1, -1)
        for r in rng:
            if r not in order:
                continue

            def key(nid: str) -> float:
                neigh = neighbor_map.get(nid) or []
                return sum(pos.get(m, 0) for m in neigh) / len(neigh) if neigh else pos.get(nid, 0)

            order[r] = sorted(order[r], key=key)
            refresh_pos()

    barycenter_pass(True)
    barycenter_pass(False)
    barycenter_pass(True)
    return order


def _fallback_linear_spec(spec: DiagramSpec) -> DiagramSpec:
    """Graph exceeded what the layout can render cleanly — degrade to a short
    linear chain (v1-style) in topological order rather than attempting a cramped
    or overlapping layout."""
    rank = _assign_ranks(spec.nodes, spec.edges)
    ordered = sorted(spec.nodes, key=lambda n: rank.get(n.id, 0))[:10]
    for i, node in enumerate(ordered):
        node.type = "start" if i == 0 else ("end" if i == len(ordered) - 1 else "process")
    edges = [DiagramEdge(source=ordered[i].id, target=ordered[i + 1].id) for i in range(len(ordered) - 1)]
    return DiagramSpec(title=spec.title, nodes=ordered, edges=edges, is_supported=False)


def _author_diagram(user_query: str, prof: dict, model: str, context: str = "") -> DiagramSpec:
    from pipeline.capability_runtime.chat_runner import generate_text_sync

    user_content = user_query
    if context:
        user_content = (
            f"{user_query}\n\nGround your facts in this material — do not state a "
            f"number or date that isn't supported by it:\n{context}"
        )

    raw = generate_text_sync(
        prof,
        model,
        [
            {"role": "system", "content": _DIAGRAM_AUTHOR_SYSTEM},
            {"role": "user", "content": user_content},
        ],
        disable_thinking=True,
    )
    data = _parse_json(raw) or {}

    nodes: list[DiagramNode] = []
    seen_ids: set[str] = set()
    for i, n in enumerate(data.get("nodes") or []):
        if not isinstance(n, dict):
            continue
        title = str(n.get("title") or "").strip()
        if not title:
            continue
        node_id = str(n.get("id") or f"n{i}").strip() or f"n{i}"
        if node_id in seen_ids:
            node_id = f"{node_id}_{i}"
        seen_ids.add(node_id)
        raw_type = str(n.get("type") or "").strip().lower()
        nodes.append(DiagramNode(
            id=node_id,
            title=title,
            description=str(n.get("description") or "").strip(),
            type=raw_type if raw_type in _VALID_TYPES else "process",
        ))
    if not nodes:
        nodes = [DiagramNode(id="n0", title=user_query.strip()[:24] or "Step")]

    valid_ids = {n.id for n in nodes}
    edges: list[DiagramEdge] = []
    for e in (data.get("edges") or []):
        if not isinstance(e, dict):
            continue
        src, tgt = str(e.get("source") or "").strip(), str(e.get("target") or "").strip()
        if src not in valid_ids or tgt not in valid_ids or src == tgt:
            continue
        edges.append(DiagramEdge(
            source=src, target=tgt,
            label=str(e.get("label") or "").strip(),
            is_loop_back=bool(e.get("is_loop_back")),
        ))
    # A single node, or nodes with no usable edges, still gets a sane chain.
    if not edges and len(nodes) > 1:
        edges = [DiagramEdge(source=nodes[i].id, target=nodes[i + 1].id) for i in range(len(nodes) - 1)]

    _mark_cycle_edges(nodes, edges)
    loop_back_count = sum(1 for e in edges if e.is_loop_back)
    is_supported = len(nodes) <= _MAX_NODES and len(edges) <= _MAX_EDGES and loop_back_count <= _MAX_LOOP_BACK

    forward_edges = [e for e in edges if not e.is_loop_back]
    has_incoming = {e.target for e in forward_edges}
    has_outgoing = {e.source for e in forward_edges}
    for node in nodes:
        if node.type != "process":
            continue
        if node.id not in has_incoming:
            node.type = "start"
        elif node.id not in has_outgoing:
            node.type = "end"

    spec = DiagramSpec(title=str(data.get("title") or "").strip(), nodes=nodes, edges=edges, is_supported=is_supported)
    return spec if is_supported else _fallback_linear_spec(spec)


def _limitation_note(spec: DiagramSpec) -> str:
    """Empty when the graph rendered as genuinely requested — a note is only shown
    when the request had to be simplified (graph too large/tangled to lay out
    cleanly)."""
    if spec.is_supported:
        return ""
    from pipeline.i18n import t as tr

    return tr("diagram.limitation_too_complex")


def generate_diagram(
    user_query: str,
    *,
    prof: dict,
    model: str,
    output_path: str | None = None,
    context: str = "",
) -> tuple[ImageGenerationResult, str]:
    """Returns (result, limitation_note) — the note is empty unless the requested
    graph had to be simplified. `context`, if given, is grounding material (an
    attached-source excerpt or web search result) the diagram's facts should be
    based on — see pipeline/base/grounding.py."""
    from pipeline.deliverables.presentation_theme import infer_palette_from_query, resolve_theme

    spec = _author_diagram(user_query, prof, model, context)
    theme = resolve_theme(query=user_query, design={"palette": infer_palette_from_query(user_query)})

    os.makedirs(GENERATED_IMAGE_DIR, exist_ok=True)
    if output_path is None:
        stem = re.sub(r"[^a-z0-9]+", "_", (user_query or "diagram").lower())[:40].strip("_") or "diagram"
        output_path = unique_output_path(stem + ".png", "diagram")

    _render_diagram_graph(spec, theme, output_path)

    from PIL import Image

    with Image.open(output_path) as img:
        out_w, out_h = img.size

    result = ImageGenerationResult(
        output_path, user_query, 0, out_w, out_h, 0, 0.0, "diagram-render",
    )
    return result, _limitation_note(spec)


def _draw_text_centered(draw, cx, cy, text: str, font, fill) -> None:
    """True visual centering on (cx, cy) using the glyph's own rendered bounding box.
    PIL's anchor="mm" centers on the font's ascender/descender design box instead —
    for descender-less glyphs (digits, "Valid?") that leaves them floating high
    inside a circle/diamond instead of sitting in its visual middle."""
    bbox = draw.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((cx - w / 2 - bbox[0], cy - h / 2 - bbox[1]), text, font=font, fill=fill)


def _draw_node_shape(draw, node_type: str, x0, y0, x1, y1, *, fill, outline=None, width=0, radius=0) -> None:
    """Shape encodes node meaning, matching flowchart convention: start/end are
    stadium/pill shapes, decision is a diamond, everything else is a rounded
    rectangle. Diamond vertices land exactly on the bounding box's edge-centers, so
    edge-anchor math (right/left/top/bottom-center) works unchanged for every shape."""
    if node_type in ("start", "end"):
        r = (y1 - y0) / 2
        draw.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=fill, outline=outline, width=width)
    elif node_type == "decision":
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        pts = [(cx, y0), (x1, cy), (cx, y1), (x0, cy)]
        draw.polygon(pts, fill=fill)
        if outline and width:
            draw.line(pts + [pts[0]], fill=outline, width=width, joint="curve")
    else:
        draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=fill, outline=outline, width=width)


def _draw_polyline_arrow(draw, points: list[tuple[float, float]], color, scale: int = 1, *, dashed: bool = False) -> None:
    for a, b in zip(points[:-1], points[1:]):
        if dashed:
            _draw_dashed_segment(draw, a, b, color, scale)
        else:
            draw.line([a, b], fill=color, width=4 * scale)

    x0, y0 = points[-2]
    x1, y1 = points[-1]
    angle = math.atan2(y1 - y0, x1 - x0)
    head_len = 14 * scale
    spread = math.radians(22)
    p1 = (x1 - head_len * math.cos(angle - spread), y1 - head_len * math.sin(angle - spread))
    p2 = (x1 - head_len * math.cos(angle + spread), y1 - head_len * math.sin(angle + spread))
    draw.polygon([(x1, y1), p1, p2], fill=color)


def _draw_dashed_segment(draw, a: tuple[float, float], b: tuple[float, float], color, scale: int) -> None:
    ax, ay = a
    bx, by = b
    length = math.hypot(bx - ax, by - ay)
    if length == 0:
        return
    dash_len, gap_len = 10 * scale, 8 * scale
    ux, uy = (bx - ax) / length, (by - ay) / length
    dist = 0.0
    while dist < length:
        seg_end = min(dist + dash_len, length)
        draw.line(
            [(ax + ux * dist, ay + uy * dist), (ax + ux * seg_end, ay + uy * seg_end)],
            fill=color, width=max(2, 3 * scale // 2),
        )
        dist = seg_end + gap_len


def _render_diagram_graph(spec: DiagramSpec, theme, output_path: str) -> None:
    from PIL import Image, ImageDraw, ImageFilter

    from services.poster_fonts import font_for_text, wrap_text

    nodes = spec.nodes or [DiagramNode(id="n0", title=spec.title or "Diagram")]
    edges = spec.edges
    node_by_id = {n.id: n for n in nodes}

    # Defense in depth: _author_diagram() already marks cycle-forming edges as
    # loop-back before calling this function, but rank assignment must never see a
    # true cycle regardless of caller (a raw graph passed in directly would
    # otherwise starve Kahn's algorithm of any indegree-0 node and collapse every
    # node onto the same rank) — re-marking already-flagged edges is a no-op.
    _mark_cycle_edges(nodes, edges)

    rank = _assign_ranks(nodes, edges)
    order = _order_within_ranks(nodes, edges, rank)
    order_index = {nid: i for ids in order.values() for i, nid in enumerate(ids)}
    num_ranks = max(rank.values(), default=0) + 1
    max_rows = max((len(ids) for ids in order.values()), default=1)

    gap_x, gap_y = 110, 70
    margin = 60
    title_h = 90 if spec.title else 20
    has_loop_back = any(e.is_loop_back for e in edges)
    loop_lane_h = 70 if has_loop_back else 0
    # A forward edge that skips a rank (e.g. a "No" branch that goes straight to an
    # "End" two columns over) would otherwise route its final horizontal segment
    # straight through whatever column it skips — visually crossing through that
    # column's box/text. Route those through a dedicated lane below the diagram
    # instead, same idea as the loop-back lane but solid/forward-pointing.
    has_bypass = any(
        (not e.is_loop_back) and (rank.get(e.target, 0) - rank.get(e.source, 0) > 1)
        for e in edges
    )
    bypass_lane_h = 60 if has_bypass else 0

    bg = tuple(theme.background_rgb)
    primary = tuple(theme.primary_rgb)
    accent = tuple(theme.accent_rgb)
    title_color = tuple(theme.title_rgb)
    body_color = tuple(theme.body_rgb)
    loop_color = tuple(int(a * 0.55 + b * 0.45) for a, b in zip(accent, bg))

    # --- content-aware sizing pass (logical/unscaled units), uniform across the
    # whole graph — box width fits the longest line actually present, box height
    # fits whichever node needs the most room. ---
    probe_img = Image.new("RGB", (10, 10))
    probe_draw = ImageDraw.Draw(probe_img)

    def _text_w(text: str, size: int, bold: bool) -> int:
        font = font_for_text(text, size, bold=bold)
        bbox = probe_draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0]

    needed_w = 0
    for node in nodes:
        needed_w = max(needed_w, _text_w(node.title, 22, True))
        if node.description:
            needed_w = max(needed_w, _text_w(node.description, 15, False))
    box_w = max(_MIN_BOX_W, min(_MAX_BOX_W, needed_w + 60))
    inner_w = box_w - 30

    def _content_height(node: DiagramNode) -> int:
        title_font = font_for_text(node.title, 22, bold=True)
        lines = wrap_text(node.title, title_font, inner_w, probe_draw)
        h = 20 + min(len(lines), 3) * 28
        if node.description:
            desc_font = font_for_text(node.description, 15)
            dlines = wrap_text(node.description, desc_font, inner_w, probe_draw)
            h += min(len(dlines), 3) * 20
        return h + 20

    box_h = max([_MIN_BOX_H] + [_content_height(n) for n in nodes])

    # --- positions: column (x) from rank, row (y) from within-rank order; a column
    # with fewer nodes than the tallest one is vertically centered against it. ---
    positions: dict[str, tuple[int, int, int, int]] = {}
    for r in range(num_ranks):
        ids = order.get(r, [])
        col_x0 = margin + r * (box_w + gap_x)
        row_offset = ((max_rows - len(ids)) / 2) * (box_h + gap_y) if ids else 0
        for i, nid in enumerate(ids):
            y0 = margin + title_h + row_offset + i * (box_h + gap_y)
            positions[nid] = (col_x0, int(y0), col_x0 + box_w, int(y0) + box_h)

    canvas_w = margin * 2 + num_ranks * box_w + max(0, num_ranks - 1) * gap_x
    canvas_h = (
        margin * 2 + title_h + max_rows * box_h + max(0, max_rows - 1) * gap_y
        + bypass_lane_h + loop_lane_h
    )

    # Numbered badges only make sense — and match v1's look — for a genuinely
    # simple chain; a branching/merging graph relies on shape + arrows instead,
    # like most professional flowchart tools do once there's more than one path.
    is_linear_chain = (
        not has_loop_back
        and len(edges) == len(nodes) - 1
        and all(
            sum(1 for e in edges if e.source == n.id) <= 1 and sum(1 for e in edges if e.target == n.id) <= 1
            for n in nodes
        )
    )

    S = _SCALE
    img = Image.new("RGB", (canvas_w * S, canvas_h * S), bg)
    draw = ImageDraw.Draw(img)

    if spec.title:
        title_font = font_for_text(spec.title, 34 * S, bold=True)
        draw.text((margin * S, (margin // 2) * S), spec.title, font=title_font, fill=title_color)

    # Drop shadows: draw offset silhouettes on a separate blurred layer, then
    # composite under the boxes for a soft sense of depth instead of flat cutouts.
    shadow_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_layer)
    shadow_offset = 6 * S
    for nid, (x0, y0, x1, y1) in positions.items():
        _draw_node_shape(
            shadow_draw, node_by_id[nid].type,
            x0 * S + shadow_offset, y0 * S + shadow_offset,
            x1 * S + shadow_offset, y1 * S + shadow_offset,
            fill=(0, 0, 0, 90), radius=18 * S,
        )
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=5 * S))
    img = Image.alpha_composite(img.convert("RGBA"), shadow_layer).convert("RGB")
    draw = ImageDraw.Draw(img)

    for node in nodes:
        x0, y0, x1, y1 = positions[node.id]
        X0, Y0, X1, Y1 = x0 * S, y0 * S, x1 * S, y1 * S

        _draw_node_shape(
            draw, node.type, X0, Y0, X1, Y1,
            fill=(255, 255, 255), outline=primary, width=3 * S, radius=18 * S,
        )

        if is_linear_chain:
            badge_r = 16 * S
            draw.ellipse([X0 - badge_r, Y0 - badge_r, X0 + badge_r, Y0 + badge_r], fill=primary)
            num_font = font_for_text(str(rank[node.id] + 1), 18 * S, bold=True)
            _draw_text_centered(draw, X0, Y0, str(rank[node.id] + 1), num_font, (255, 255, 255))

        box_cx = (X0 + X1) / 2
        text_w_px = int(box_w * 0.56) * S if node.type == "decision" else inner_w * S

        title_line_h, desc_line_h = 28 * S, 20 * S
        step_font = font_for_text(node.title, 22 * S, bold=True)
        title_lines = wrap_text(node.title, step_font, text_w_px, draw)[:3]

        desc_lines: list[str] = []
        desc_font = None
        if node.description:
            desc_font = font_for_text(node.description, 15 * S)
            desc_lines = wrap_text(node.description, desc_font, text_w_px, draw)[:3]

        gap_after_title = 6 * S if desc_lines else 0
        block_h = len(title_lines) * title_line_h + gap_after_title + len(desc_lines) * desc_line_h
        ty = (Y0 + Y1) / 2 - block_h / 2

        for line in title_lines:
            lw = draw.textbbox((0, 0), line, font=step_font)[2]
            draw.text((box_cx - lw / 2, ty), line, font=step_font, fill=title_color)
            ty += title_line_h
        ty += gap_after_title
        for line in desc_lines:
            lw = draw.textbbox((0, 0), line, font=desc_font)[2]
            draw.text((box_cx - lw / 2, ty), line, font=desc_font, fill=body_color)
            ty += desc_line_h

    # --- edges, drawn last so arrowheads sit on top of node borders ---
    bypass_lane_y = (canvas_h - loop_lane_h - bypass_lane_h + 20) * S
    loop_lane_y = (canvas_h - loop_lane_h + 20) * S

    # A straight diagonal/horizontal line only looks right for a plain 1-to-1
    # connection. A decision's two branches (or any fan-out/fan-in) land in
    # different rows almost by definition — one branch would coincidentally get a
    # straight line while the other gets an elbow, which reads as inconsistent even
    # though both are "the same kind" of connection. Route every branch of a
    # fan-out/fan-in with the elbow style instead, so sibling edges always match.
    forward_out_count: dict[str, int] = defaultdict(int)
    forward_in_count: dict[str, int] = defaultdict(int)
    for e in edges:
        if not e.is_loop_back:
            forward_out_count[e.source] += 1
            forward_in_count[e.target] += 1

    for edge in edges:
        if edge.source not in positions or edge.target not in positions:
            continue
        sx0, sy0, sx1, sy1 = positions[edge.source]
        tx0, ty0, tx1, ty1 = positions[edge.target]
        scy, tcy = (sy0 + sy1) / 2 * S, (ty0 + ty1) / 2 * S

        if edge.is_loop_back:
            scx = (sx0 + sx1) / 2 * S
            tcx = (tx0 + tx1) / 2 * S
            points = [(scx, sy1 * S), (scx, loop_lane_y), (tcx, loop_lane_y), (tcx, ty1 * S)]
            _draw_polyline_arrow(draw, points, loop_color, S, dashed=True)
            continue

        gap_ranks = rank.get(edge.target, 0) - rank.get(edge.source, 0)
        if gap_ranks > 1:
            # Skips at least one column — route below the diagram entirely so the
            # path never crosses through a column's boxes/text.
            scx = (sx0 + sx1) / 2 * S
            tcx = (tx0 + tx1) / 2 * S
            points = [(scx, sy1 * S), (scx, bypass_lane_y), (tcx, bypass_lane_y), (tcx, ty1 * S)]
            _draw_polyline_arrow(draw, points, accent, S)
            if edge.label:
                _draw_edge_label(draw, edge.label, points[1], points[2], bg, accent, S)
            continue

        src_right = (sx1 * S, scy)
        tgt_left = (tx0 * S, tcy)
        same_row = order_index.get(edge.source) == order_index.get(edge.target)
        is_plain_link = forward_out_count[edge.source] <= 1 and forward_in_count[edge.target] <= 1
        if same_row and gap_ranks == 1 and is_plain_link:
            points = [src_right, tgt_left]
        else:
            mid_x = sx1 * S + gap_x * S / 2
            points = [src_right, (mid_x, scy), (mid_x, tcy), tgt_left]
        _draw_polyline_arrow(draw, points, accent, S)
        if edge.label:
            if len(points) == 4:
                # Elbow route: label on the vertical segment, not the first
                # horizontal one — sibling branches out of the same decision node
                # share an identical first segment (same source point), so a label
                # placed there would stack directly on top of a sibling's label.
                # The vertical segment differs per branch (each has its own target
                # row), so it's always a safe, distinct spot.
                _draw_edge_label(draw, edge.label, points[1], points[2], bg, accent, S)
            else:
                _draw_edge_label(draw, edge.label, points[0], points[1], bg, accent, S)

    img = img.resize((canvas_w, canvas_h), Image.LANCZOS)
    img.save(output_path)


def _draw_edge_label(draw, label: str, p0, p1, bg, accent, scale: int) -> None:
    """A small pill chip at the midpoint of one edge segment (e.g. "Yes"/"No" on a
    decision branch)."""
    from services.poster_fonts import font_for_text

    label_font = font_for_text(label, 14 * scale, bold=True)
    lx = p0[0] + (p1[0] - p0[0]) / 2
    ly = p0[1] + (p1[1] - p0[1]) / 2
    lw = draw.textbbox((0, 0), label, font=label_font)[2]
    pad = 6 * scale
    draw.rounded_rectangle(
        [lx - lw / 2 - pad, ly - 10 * scale, lx + lw / 2 + pad, ly + 10 * scale],
        radius=8 * scale, fill=bg, outline=accent, width=max(1, scale // 2),
    )
    _draw_text_centered(draw, lx, ly, label, label_font, accent)
