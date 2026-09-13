# -*- coding: utf-8 -*-
"""Shared Lite / Pro role execution for capabilities and extensions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pipeline.capability_runtime.chat_runner import (
    build_chat_contract_text,
    build_merged_roles_system_instruction,
    generate_text_sync,
    stream_chat_response,
    validate_or_repair_prompt,
)
from pipeline.contracts.registry import Contract, get_contract_for_role
from pipeline.execution_modes.constants import normalize_execution_mode
from pipeline.roles.registry import get_role


@dataclass
class RolePipelineConfig:
    mode: str
    base_system_instruction: str
    profile: dict
    model: str
    user_query: str
    role_ids: list[str]
    messages: list[dict[str, Any]]
    sink: Any
    is_cancelled: Callable[[], bool]
    stream_final: bool = True
    capability_id: str = "chat"
    disable_thinking: bool = False
    presentation_style: str = ""
    presentation_grounded: bool = False


def _step_contract(role_id: str, capability_id: str) -> Contract:
    return get_contract_for_role(role_id, capability_id)


def _validate_step(text: str, role_id: str, capability_id: str, sink: Any) -> tuple[bool, str]:
    contract = _step_contract(role_id, capability_id)
    valid, hint = validate_or_repair_prompt(text, contract)
    if not valid:
        sink.log(f"Validation failed ({role_id}): {hint or 'contract mismatch'}")
    return valid, hint


def _run_step_sync(
    *,
    cfg: RolePipelineConfig,
    messages: list[dict],
    role_id: str,
    step_user_prompt: str,
) -> str:
    role = get_role(role_id, cfg.capability_id)
    contract = _step_contract(role_id, cfg.capability_id)
    system = build_merged_roles_system_instruction(
        cfg.base_system_instruction,
        [role],
        [contract],
    )
    step_messages = [{"role": "system", "content": system}]
    for m in messages[1:]:
        if m.get("role") != "system":
            step_messages.append(m)
    step_messages.append({"role": "user", "content": step_user_prompt})
    return generate_text_sync(
        cfg.profile,
        cfg.model,
        step_messages,
        disable_thinking=cfg.disable_thinking,
        sink=cfg.sink,
    )


def _repair_step_sync(
    cfg: RolePipelineConfig,
    messages: list[dict],
    role_id: str,
    content: str,
    repair_hint: str,
) -> str:
    prompt = (
        f"Repair the output for role '{role_id}' to satisfy the contract.\n"
        f"{repair_hint}\n\n"
        f"Current output:\n{content}"
    ).strip()
    return _run_step_sync(cfg=cfg, messages=messages, role_id=role_id, step_user_prompt=prompt)


def run_lite(cfg: RolePipelineConfig) -> str:
    """Lite mode: sequential role steps with validation (former Pro pipeline)."""
    return run_pro(cfg)


def run_pro(cfg: RolePipelineConfig) -> str:
    """Sequential role steps with validation (and one repair) per step."""
    working = ""
    context_messages = list(cfg.messages)
    last_index = len(cfg.role_ids) - 1

    for idx, role_id in enumerate(cfg.role_ids):
        if cfg.is_cancelled():
            break
        role = get_role(role_id, cfg.capability_id)
        mode_label = normalize_execution_mode(cfg.mode).capitalize()
        cfg.sink.log(f"{mode_label} step {idx + 1}/{len(cfg.role_ids)}: {role_id}")
        from pipeline.direct.prompt_hygiene import user_step_content

        step_prompt = user_step_content(user_query=cfg.user_query, working_text=working)

        stream_this = cfg.stream_final and idx == last_index
        if stream_this:
            system = build_merged_roles_system_instruction(
                cfg.base_system_instruction,
                [role],
                [_step_contract(role_id, cfg.capability_id)],
            )
            step_messages = [{"role": "system", "content": system}]
            for m in context_messages[1:]:
                if m.get("role") != "system":
                    step_messages.append(m)
            step_messages.append({"role": "user", "content": step_prompt})
            stream_chat_response(
                profile=cfg.profile,
                model=cfg.model,
                messages=step_messages,
                sink=cfg.sink,
                is_cancelled=cfg.is_cancelled,
                disable_thinking=cfg.disable_thinking,
            )
            try:
                from services.session import state

                working = (state.messages[-1].get("content") or "").strip() if state.messages else ""
            except Exception:
                working = ""
            if working:
                valid, hint = _validate_step(working, role_id, cfg.capability_id, cfg.sink)
                if not valid and not cfg.is_cancelled():
                    working = _repair_step_sync(cfg, context_messages, role_id, working, hint)
                    repair_messages = [{"role": "system", "content": system}]
                    for m in context_messages[1:]:
                        if m.get("role") != "system":
                            repair_messages.append(m)
                    repair_messages.append(
                        {
                            "role": "user",
                            "content": (
                                f"Repair output for role '{role_id}'.\n{hint}\n\n{working}"
                            ),
                        }
                    )
                    stream_chat_response(
                        profile=cfg.profile,
                        model=cfg.model,
                        messages=repair_messages,
                        sink=cfg.sink,
                        is_cancelled=cfg.is_cancelled,
                        disable_thinking=cfg.disable_thinking,
                    )
            else:
                working = ""
        else:
            working = _run_step_sync(
                cfg=cfg,
                messages=context_messages,
                role_id=role_id,
                step_user_prompt=step_prompt,
            )
            valid, hint = _validate_step(working, role_id, cfg.capability_id, cfg.sink)
            if not valid:
                working = _repair_step_sync(cfg, context_messages, role_id, working, hint)

    return working


def run_role_pipeline(cfg: RolePipelineConfig) -> str:
    from pipeline.progress_stages import ensure_lite_stages_before_synthesis, on_synthesis_complete

    mode = normalize_execution_mode(cfg.mode)
    labels = ", ".join(cfg.role_ids)
    cfg.sink.log(f"Execution mode: {mode} | roles: {labels}")
    ensure_lite_stages_before_synthesis(cfg.sink, mode)
    result = run_lite(cfg)
    if result and not cfg.is_cancelled():
        on_synthesis_complete(cfg.sink, mode)
    return result
