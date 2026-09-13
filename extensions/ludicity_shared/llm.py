# -*- coding: utf-8 -*-
"""Shared LLM helper for Ludicity extensions."""
from __future__ import annotations

from typing import Callable


def _ensure_chat_model_available() -> None:
    """Guard for every LLM entry point in this module. Extensions (Document
    Intelligence, Formslator, and any future one built on this helper) call straight
    into the LLM without going through the main chat handler's has_usable_chat_model()
    gate, so without this an extension with no provider/model would only discover the
    gap via a raw client-library error. This opens the same install dialog the main
    chat path shows (branching on provider-missing vs model-missing) and raises a
    clean, translated error so the extension's own error surface stays readable —
    build_chat_request() also backstops this for any caller that skips this helper."""
    from services.model_assignments import has_usable_chat_model

    if has_usable_chat_model():
        return
    from pipeline.gap_handler import offer_chat_model_installer
    from pipeline.i18n import t as tr
    from services.model_router import NoChatModelError

    offer_chat_model_installer()
    raise NoChatModelError(tr("chat.no_models_installed"))


def ludicity_chat(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    log_fn: Callable[[str], None] | None = None,
    extra_options: dict | None = None,
    locale: str | None = None,
    apply_locale: bool = True,
) -> str:
    _ensure_chat_model_available()
    from pipeline.base.profile_pack import load_profile
    from pipeline.capability_runtime.chat_runner import generate_text_sync
    from pipeline.direct.batch_budget import llm_extra_options, resolve_batch_budget
    from pipeline.state_machine import extension_processing
    from services.model_router import resolve_general_model
    from services.session import state

    prof = load_profile((state.current_settings or {}).get("active_profile", "none")) or {}
    use_model = (model or "").strip() or resolve_general_model(prof)
    if extra_options:
        extra = extra_options
    else:
        from pipeline.direct.batch_budget import fit_budget_to_prompt

        prompt_chars = sum(len(m.get("content") or "") for m in messages)
        budget = fit_budget_to_prompt(resolve_batch_budget(prof, use_model), prompt_chars)
        extra = llm_extra_options(budget)
    sink = None
    if log_fn:

        class _Sink:
            def log(self, msg: str) -> None:
                log_fn(msg)

        sink = _Sink()
    # Without this, the model picks its own reply language per-call (varies with
    # prompt content/sampling) instead of a consistent one. apply_locale_to_messages
    # only covers the app's fixed UI-language set (en/zh_tw/zh_cn/es/de) — callers
    # that need to match arbitrary query languages (e.g. Document Intelligence,
    # where a question can be in any language) should pass apply_locale=False and
    # put a generic "reply in the question's language" instruction in their own
    # system prompt instead, since that generalizes to languages this rule doesn't
    # enumerate.
    if apply_locale:
        from pipeline.i18n import apply_locale_to_messages

        messages = apply_locale_to_messages(messages, locale=locale)
    with extension_processing():
        return generate_text_sync(
            prof,
            use_model,
            messages,
            disable_thinking=True,
            sink=sink,
            extra_options=extra,
        )


def ludicity_chat_stream(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    on_chunk: Callable[[str], None] | None = None,
    should_abort: Callable[[], bool] | None = None,
    extra_options: dict | None = None,
    locale: str | None = None,
    apply_locale: bool = True,
) -> str:
    """Stream tokens; call on_chunk(accumulated_text) as text arrives.

    apply_locale mirrors ludicity_chat's parameter of the same name — pass False when
    the caller's own system prompt already handles reply language (e.g. Document
    Intelligence matching the question's language rather than the fixed UI locale)."""
    _ensure_chat_model_available()
    from pipeline.base.profile_pack import load_profile
    from pipeline.direct.batch_budget import llm_extra_options, resolve_batch_budget
    from pipeline.state_machine import extension_processing
    from services.inference.ollama_chat import build_chat_request
    from services.llm_bridge import chat as llm_chat
    from services.model_router import resolve_general_model
    from services.resource_governor import ResourceGovernor
    from services.session import state

    prof = load_profile((state.current_settings or {}).get("active_profile", "none")) or {}
    use_model = (model or "").strip() or resolve_general_model(prof)
    if extra_options:
        extra = extra_options
    else:
        from pipeline.direct.batch_budget import fit_budget_to_prompt

        prompt_chars = sum(len(m.get("content") or "") for m in messages)
        budget = fit_budget_to_prompt(resolve_batch_budget(prof, use_model), prompt_chars)
        extra = llm_extra_options(budget)
    if apply_locale:
        from pipeline.i18n import apply_locale_to_messages

        messages = apply_locale_to_messages(messages, locale=locale)
    kwargs, _ = build_chat_request(
        prof,
        model=use_model,
        messages=messages,
        stream=True,
        extra_options=extra,
        disable_thinking=True,
    )
    acc = ""
    with extension_processing(), ResourceGovernor.acquire("llm_chat"):
        stream_resp = llm_chat(**kwargs)
        for chunk in stream_resp:
            if should_abort and should_abort():
                break
            msg = chunk.get("message") or {}
            token = msg.get("content") or chunk.get("response") or ""
            if not token:
                continue
            acc += token
            if on_chunk:
                on_chunk(acc)
    return acc.strip()
