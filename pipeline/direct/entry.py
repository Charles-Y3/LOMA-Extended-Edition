# -*- coding: utf-8 -*-
"""Direct-mode entry: express lane or unified planner + step executor."""
from __future__ import annotations

import re
from typing import Any

from pipeline.direct.express_lane import can_use_express_lane
from pipeline.direct.express_runner import run_express_chat
from pipeline.direct.query_planner import plan_direct_query
from pipeline.direct.step_executor import run_direct_pipeline as execute_plan
from pipeline.input_metadata import build_input_metadata
from pipeline.schemas.task_schema import InputMetadata

ROUTE_KEY = "direct_pipeline"


def _metadata_from_inputs(inputs: dict[str, Any]) -> InputMetadata:
    meta = inputs.get("metadata")
    if isinstance(meta, InputMetadata):
        return meta
    state = inputs["state"]
    return build_input_metadata(state)


def _run_presentation_generation(inputs: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """Build and execute a presentation plan — the LLM-based planning step the
    fast pre-check below deliberately skips until AFTER a style is known
    (either named explicitly in the request, or picked via the style picker).
    `cfg` already carries `presentation_style` when one was chosen."""
    state = inputs["state"]
    sink = inputs["sink"]
    bundle = inputs["bundle"]
    prof = inputs.get("prof") or {}

    from services.model_router import resolve_general_model

    gen_model = inputs.get("gen_model") or cfg.get("model") or resolve_general_model(prof)
    has_charts = bool(getattr(bundle, "chart_artifacts", None))
    plan = plan_direct_query(
        _metadata_from_inputs(inputs),
        profile=prof,
        has_charts=has_charts,
        model=gen_model,
        log_fn=sink.log,
        bundle=bundle,
    )
    cfg = dict(cfg)
    cfg["output_type"] = plan.output_type
    cfg["mode"] = plan.mode
    return execute_plan(inputs, cfg, plan)


def _run_presentation_style_picker(
    inputs: dict[str, Any], cfg: dict[str, Any], *, sink: Any, state: Any,
) -> dict[str, Any]:
    """Show the deck style picker instead of generating immediately. The
    click handler resumes with the chosen style stashed in a fresh copy of
    `cfg` — via run_generation_on_client so progress, streaming, the busy
    send-button state, and the final ready notification all still work (see
    that function's docstring for why a bare background thread silently
    breaks all of that). Building the actual plan (the LLM planner call) is
    deferred to _run_presentation_generation, called only after the pick —
    that's the whole point: showing this picker doesn't wait on an LLM call
    that has nothing to do with which style gets used."""
    import uuid

    from pipeline.i18n import t as tr
    from ui.components.style_picker import run_generation_on_client, show_presentation_style_picker
    from ui.themes import registry

    token = uuid.uuid4().hex
    msg_ref: dict | None = None

    def _clear_reopen_button() -> None:
        registry.style_picker_reopeners.pop(token, None)
        if msg_ref is not None and msg_ref.get("style_picker_token"):
            msg_ref["style_picker_token"] = None
            sink.refresh_chat()

    def _on_select(style_id: str) -> None:
        _clear_reopen_button()

        def _resume() -> None:
            cfg2 = dict(cfg)
            cfg2["presentation_style"] = style_id
            _run_presentation_generation(inputs, cfg2)

        run_generation_on_client(_resume, sink=sink)

    def _reopen() -> None:
        show_presentation_style_picker(on_select=_on_select)

    registry.style_picker_reopeners[token] = _reopen
    show_presentation_style_picker(on_select=_on_select)
    sink.set_assistant_content(tr("presentation.style_picker.chat_prompt"))
    # Tags this message so chat_message.py can render a "reopen picker" button
    # — if the user clicks away from the dialog instead of picking a style,
    # there was previously no way to get it back short of retyping the
    # request from scratch. Clicking it calls `_reopen` above directly (same
    # dialog, same on_select) rather than resubmitting the request as a new
    # chat turn — _clear_reopen_button() removes the token once a style is
    # actually picked, so the button can't be clicked again afterward and
    # doesn't linger once this message becomes the "presentation ready" card.
    if state.messages:
        msg_ref = state.messages[-1]
        msg_ref["style_picker_token"] = token
    sink.refresh_chat()
    return {"content": inputs["request"].user_input, "path": "", "output_type": "presentation"}


def _try_fast_poster_or_presentation(
    inputs: dict[str, Any], config: dict[str, Any], metadata: InputMetadata,
) -> dict[str, Any] | None:
    """Fast, pattern-only pre-check for a plain "poster about X" / "presentation
    about X" generation request. `resolve_output_type_for_direct` /
    `infer_mode_for_direct` are the SAME deterministic (no-LLM) classifiers
    `plan_direct_query`'s own fallback path already uses — reusing them here
    lets the style picker (or, when a style/template is already named, the
    generation itself) start immediately instead of waiting on the LLM-based
    planner call, which determines multi-step structure the poster/
    presentation paths don't even use (a poster/presentation intent short-
    circuits the plan immediately once it's classified — the plan built for
    it was always discarded, just slowly).

    Returns None — falling through to the normal LLM-planned path unchanged —
    for anything more complex: multi-step/compound requests, mutations, or
    any output type other than poster/presentation."""
    request = inputs["request"]
    sink = inputs["sink"]
    state = inputs["state"]
    bundle = inputs["bundle"]
    prof = inputs.get("prof") or {}
    query = request.user_input

    from pipeline.direct.query_planner import (
        _TASK_VERBS,
        _split_intents,
        infer_mode_for_direct,
        resolve_output_type_for_direct,
    )

    if len(_split_intents(query)) > 1:
        return None
    # _split_intents' regex-based compound-request detection isn't as reliable as
    # the full LLM-based planner's own multi-intent splitting — it missed "make a
    # poster about X and also translate it to Spanish" in testing. Second, blunter
    # safety net: if a clause after "and"/"then"/"also" names an actual task verb
    # (translate/summarize/rewrite/...), treat it as a real second task and bail
    # to the full planner, even though _split_intents didn't catch it — silently
    # dropping a second task is worse than losing the fast path on the rarer
    # compound request. Doesn't fire on a plain subject like "a poster about salt
    # and pepper", since "pepper" isn't a task verb.
    and_split = re.split(r"[;\n]|\b(?:and|then|also)\b", query, flags=re.IGNORECASE)
    if len(and_split) > 1 and any(_TASK_VERBS.search(part) for part in and_split[1:]):
        return None

    output_type = resolve_output_type_for_direct(query, metadata.preferred_output_format, prof)
    mode = infer_mode_for_direct(output_type, metadata, query)
    if mode != "generation":
        return None

    cfg = dict(config)
    cfg["route_key"] = ROUTE_KEY
    cfg["mode"] = mode

    if output_type == "presentation":
        from pipeline.deliverables.presentation_theme import resolve_explicit_presentation_style

        cfg["output_type"] = "presentation"
        explicit_style = resolve_explicit_presentation_style(query) or config.get("presentation_style")
        if explicit_style:
            cfg["presentation_style"] = explicit_style
            return _run_presentation_generation(inputs, cfg)
        return _run_presentation_style_picker(inputs, cfg, sink=sink, state=state)

    if output_type == "image":
        from pipeline.direct.image_intent import classify_image_request

        if classify_image_request(query) != "poster":
            return None

        from pipeline.direct.step_executor import _run_poster_generation, _run_poster_style_picker
        from services.poster_generation import resolve_explicit_poster_template

        cfg["output_type"] = "image"
        explicit_template = resolve_explicit_poster_template(query) or config.get("poster_template")
        if explicit_template:
            return _run_poster_generation(
                user_query=query, prof=prof, config=cfg, sink=sink, state=state,
                bundle=bundle, template=explicit_template,
            )
        return _run_poster_style_picker(
            user_query=query, prof=prof, config=cfg, sink=sink, state=state, bundle=bundle,
        )

    return None


def run_direct(inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    state = inputs["state"]
    sink = inputs["sink"]
    request = inputs["request"]
    bundle = inputs["bundle"]
    prof = inputs.get("prof") or {}
    metadata = _metadata_from_inputs(inputs)

    fast_result = _try_fast_poster_or_presentation(inputs, config, metadata)
    if fast_result is not None:
        return fast_result

    from services.model_router import resolve_general_model

    gen_model = inputs.get("gen_model") or config.get("model") or resolve_general_model(prof)

    has_charts = bool(getattr(bundle, "chart_artifacts", None))
    plan = plan_direct_query(
        metadata,
        profile=prof,
        has_charts=has_charts,
        model=gen_model,
        log_fn=sink.log,
        bundle=bundle,
    )

    if plan.express and can_use_express_lane(metadata):
        sink.log("Direct pipeline: express lane")
        return run_express_chat(inputs, config)

    sink.log(
        f"Direct plan: steps={plan.step_count} mode={plan.mode} "
        f"output={plan.output_type.upper()}"
    )
    for i, step in enumerate(plan.steps):
        sink.log(f"  step {i + 1}: {step.intent[:80]} → {', '.join(step.roles)}")

    cfg = dict(config)
    cfg["route_key"] = ROUTE_KEY
    cfg["output_type"] = plan.output_type
    cfg["mode"] = plan.mode

    if plan.output_type == "presentation" and plan.mode == "generation":
        from pipeline.deliverables.presentation_theme import resolve_explicit_presentation_style

        explicit_style = resolve_explicit_presentation_style(request.user_input)
        if explicit_style is None:
            return _run_presentation_style_picker(inputs, cfg, sink=sink, state=state)
        cfg["presentation_style"] = explicit_style

    return execute_plan(inputs, cfg, plan)
