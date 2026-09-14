# -*- coding: utf-8 -*-
"""Context Governor — the one place that guarantees no LLM request overflows the
context window, degrading gracefully instead of erroring.

See docs/PIPELINE_REFACTOR.md §1. This module is the provider-aware *planner*: given
the parts of a prompt, the model's budget (from batch_budget/resource_budget), and the
backend's capabilities, it decides the num_ctx / num_predict to request and which
strategy the caller must use — a single call, shrinking the evidence/history, or a
multi-pass split. Execution of multi-pass lives with the callers (batch_processor,
step_executor) that already know how to split their specific payloads; the governor
tells them *whether* and *how hard* to split, with one consistent, CJK-aware measure.

Why a planner and not just "raise num_ctx": raising only works on Ollama. On LM Studio
the window is fixed at model-load (services/providers/base.py ProviderCapabilities),
so the governor must instead fit the work to what's already loaded — the same ladder,
different branch (PIPELINE_REFACTOR.md §1.3).
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Token estimation. A single chars/token ratio is wrong across scripts: Latin text is
# ~4 chars/token, but CJK is ~1–1.5 chars/token (often one token per character). The
# old code used 2.5–3.5 everywhere and silently undercounted Chinese, so zh_tw/zh_cn
# workloads overflowed first. We count CJK characters separately at a conservative
# ~1 token/char and the rest at ~4 chars/token.
_LATIN_CHARS_PER_TOKEN = 4.0
_CJK_TOKENS_PER_CHAR = 1.0
_SAFETY = 1.12  # estimates are approximate — bias high so we reserve, not overflow


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (
        0x3040 <= o <= 0x30FF      # Hiragana/Katakana
        or 0x3400 <= o <= 0x4DBF   # CJK Ext A
        or 0x4E00 <= o <= 0x9FFF   # CJK Unified
        or 0xF900 <= o <= 0xFAFF   # CJK Compatibility
        or 0xAC00 <= o <= 0xD7A3   # Hangul syllables
        or 0xFF00 <= o <= 0xFFEF   # Fullwidth forms
    )


def estimate_tokens(text: str) -> int:
    """Conservative, script-aware token estimate. Biased slightly high so the governor
    reserves enough headroom rather than overflowing on an underestimate."""
    if not text:
        return 0
    cjk = sum(1 for ch in text if _is_cjk(ch))
    other = len(text) - cjk
    raw = cjk * _CJK_TOKENS_PER_CHAR + other / _LATIN_CHARS_PER_TOKEN
    return int(raw * _SAFETY) + 1


@dataclass
class PromptParts:
    """A prompt described in parts so the governor knows what it may shrink/split.
    Callers pass whichever parts apply; empty strings are fine."""

    fixed: str = ""        # system prompt + instructions — never cut
    evidence: str = ""     # sources / retrieved chunks — may be selected down
    history: str = ""      # prior turns / prior output — may be summarised/dropped
    payload: str = ""      # the document being processed — may be split into passes
    expected_output_chars: int = 0  # rough size of the deliverable, so output isn't truncated

    def prompt_tokens(self) -> int:
        return (
            estimate_tokens(self.fixed)
            + estimate_tokens(self.evidence)
            + estimate_tokens(self.history)
            + estimate_tokens(self.payload)
        )


@dataclass
class CallPlan:
    """The governor's decision for one logical LLM call."""

    strategy: str                 # "single" | "shrink" | "multipass"
    num_ctx: int                  # context window to request (ignored if can_set_ctx=False)
    num_predict: int              # output token budget
    ceiling: int                  # hard token ceiling used for the decision
    can_set_ctx: bool = True      # mirror of provider cap, so callers know if num_ctx bites
    passes: int = 1               # suggested number of payload passes for "multipass"
    notes: list[str] = field(default_factory=list)

    @property
    def needs_split(self) -> bool:
        return self.strategy == "multipass"


_NUM_CTX_TIERS = (4096, 8192, 16384, 32768, 65536, 131072)
_NUM_PREDICT_TIERS = (2048, 4096, 8192, 16384, 32768, 65536)
_RESERVE = 256  # framing/template overhead not in the measured parts

# Substrings that mark a backend context/length overflow, across Ollama (llama.cpp)
# and LM Studio (OpenAI-compatible) error bodies. Used by the llm_bridge safety net.
_CTX_ERROR_MARKERS = (
    "context length",
    "context window",
    "n_ctx",
    "maximum context",
    "maximum length",
    "too long",
    "exceed",
    "context_length_exceeded",
)


def is_context_overflow_error(exc: BaseException) -> bool:
    """True when an exception looks like a context/length overflow from either backend."""
    parts = [str(exc)]
    for attr in ("error", "body", "message"):
        val = getattr(exc, attr, None)
        if val is not None:
            parts.append(str(val))
    msg = " ".join(parts).lower()
    return any(m in msg for m in _CTX_ERROR_MARKERS)


def _messages_text(messages) -> str:
    out: list[str] = []
    for msg in messages or []:
        if isinstance(msg, dict):
            c = msg.get("content")
            if isinstance(c, str):
                out.append(c)
            elif isinstance(c, list):  # OpenAI content-array
                for part in c:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        out.append(part["text"])
        elif isinstance(msg, str):
            out.append(msg)
    return "\n".join(out)


def govern_options(
    *, text: str, options: dict | None, model: str = "", caps=None, loaded_ctx: int | None = None
) -> dict:
    """Return an options dict whose num_ctx is raised (never lowered) to fit `text`
    plus the requested output, within the machine's ceiling — the universal "raise"
    rung applied at llm_bridge so no call overflows for want of a wider window.

    Raise-only and fully defensive: if anything goes wrong, the caller's options are
    returned unchanged. On a fixed-window backend (can_set_ctx=False) num_ctx is left
    as the caller set it (it isn't sent anyway) — overflow there is handled by the
    error surface / multi-pass, not by resizing.
    """
    opts = dict(options or {})
    try:
        from pipeline.direct.batch_budget import resolve_batch_budget

        budget = resolve_batch_budget(None, model or "")
        if caps is None:
            try:
                from services.providers.registry import get_active_provider

                caps = get_active_provider().capabilities()
            except Exception:
                caps = None
        can_set = True if caps is None else bool(getattr(caps, "can_set_ctx", True))
        if not can_set:
            return opts

        out_chars = 0
        num_predict = opts.get("num_predict")
        parts = PromptParts(payload=text or "")
        plan = plan_call(parts, budget=budget, caps=caps, loaded_ctx=loaded_ctx)

        caller_ctx = int(opts.get("num_ctx") or 0)
        # Raise only: honor the caller's preference as a floor, take the larger.
        opts["num_ctx"] = max(caller_ctx, plan.num_ctx)
        if num_predict is None:
            opts["num_predict"] = plan.num_predict
        return opts
    except Exception:
        return dict(options or {})


def _tier_at_least(n: int, tiers: tuple[int, ...]) -> int:
    return next((t for t in tiers if t >= n), tiers[-1])


def plan_call(
    parts: PromptParts,
    *,
    budget: dict,
    caps=None,
    loaded_ctx: int | None = None,
) -> CallPlan:
    """Decide how to run one call without overflowing.

    budget: a resolve_batch_budget()-style dict (num_ctx, num_ctx_raw, max_tokens).
    caps:   ProviderCapabilities (None => assume full/Ollama-like capability).
    loaded_ctx: provider.loaded_context_length(model) when known; caps a fixed-window
                backend (LM Studio) to what's actually loaded.

    Ladder (PIPELINE_REFACTOR.md §1.3):
      - can_set_ctx: raise num_ctx within the hardware ceiling to fit; only split if
        even the ceiling can't hold prompt+output.
      - fixed ctx (LM Studio): ceiling = loaded window; never raise — shrink/split to
        fit, and if one minimal pass still won't fit, say so (the caller/error surface
        surfaces the actionable message).
    """
    can_set_ctx = True if caps is None else bool(getattr(caps, "can_set_ctx", True))

    hw_ceiling = int(budget.get("num_ctx_raw") or budget.get("num_ctx", 4096))
    current_ctx = int(budget.get("num_ctx", 4096))
    base_out = int(budget.get("max_tokens", 2048))

    prompt_tokens = parts.prompt_tokens()
    out_tokens = base_out
    if parts.expected_output_chars > 0:
        out_tokens = max(base_out, estimate_tokens("x" * parts.expected_output_chars))

    notes: list[str] = []

    if can_set_ctx:
        ceiling = hw_ceiling
    else:
        # Fixed window: the ceiling is whatever is loaded (if known), else the current
        # configured ctx — we cannot grow it.
        ceiling = int(loaded_ctx) if loaded_ctx and loaded_ctx > 0 else current_ctx
        notes.append("provider cannot resize context — fitting to the loaded window")

    needed = prompt_tokens + out_tokens + _RESERVE

    # Output budget: never promise more output than the ceiling can hold after the prompt.
    num_predict = _tier_at_least(out_tokens, _NUM_PREDICT_TIERS)
    room_for_out = ceiling - prompt_tokens - _RESERVE // 2

    if can_set_ctx:
        if needed <= current_ctx:
            strategy, num_ctx = "single", current_ctx
        elif needed <= ceiling:
            strategy = "single"
            num_ctx = min(_tier_at_least(needed, _NUM_CTX_TIERS), ceiling)
            notes.append(f"raised num_ctx to {num_ctx:,} to fit the workload")
        else:
            # Prompt+output exceeds even the hardware ceiling: split the payload.
            strategy, num_ctx = "multipass", ceiling
    else:
        num_ctx = ceiling  # informational; not sent to a fixed-window backend
        if needed <= ceiling:
            strategy = "single"
        else:
            strategy = "multipass"

    if strategy == "multipass":
        # How many payload passes: reserve room for fixed+output+reserve each pass, the
        # rest holds a slice of payload (+evidence, which shrink tries first).
        per_pass_overhead = (
            estimate_tokens(parts.fixed)
            + estimate_tokens(parts.history)
            + min(num_predict, max(512, room_for_out if room_for_out > 0 else 512))
            + _RESERVE
        )
        payload_room = max(256, ceiling - per_pass_overhead)
        payload_tokens = estimate_tokens(parts.payload) + estimate_tokens(parts.evidence)
        passes = max(2, -(-payload_tokens // payload_room))  # ceil-div
        # If even one minimal slice won't fit (fixed prompt alone ~ ceiling), flag it.
        if payload_room <= 256:
            notes.append("window too small even for one pass — increase context / reduce instructions")
        num_predict = min(num_predict, max(512, room_for_out)) if room_for_out > 0 else 512
        return CallPlan(
            strategy="multipass",
            num_ctx=num_ctx,
            num_predict=num_predict,
            ceiling=ceiling,
            can_set_ctx=can_set_ctx,
            passes=passes,
            notes=notes,
        )

    # single / shrink: clamp output to the room actually available.
    if room_for_out > 0:
        num_predict = min(num_predict, room_for_out)
    if prompt_tokens + _RESERVE >= ceiling:
        # Prompt alone (no payload split possible by caller) nearly fills the window —
        # mark shrink so the caller trims evidence/history before sending.
        strategy = "shrink"
        notes.append("prompt near the ceiling — trim evidence/history before sending")

    return CallPlan(
        strategy=strategy,
        num_ctx=num_ctx,
        num_predict=max(256, num_predict),
        ceiling=ceiling,
        can_set_ctx=can_set_ctx,
        passes=1,
        notes=notes,
    )
