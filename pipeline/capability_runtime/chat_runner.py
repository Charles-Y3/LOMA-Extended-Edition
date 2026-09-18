"""Shared runtime helpers for chat role execution."""
from __future__ import annotations

import time
from typing import Any

from services import llm_bridge as chat_client
from services.llm_bridge import build_chat_request
from services.resource_governor import ResourceGovernor

from pipeline.contracts.registry import Contract
from pipeline.roles.chat_roles import ChatRole
from pipeline.validate.engine import validate_output


def build_chat_contract_text(contract: Contract) -> str:
    rules = "\n".join(f"- {r}" for r in contract.output_rules)
    return f"{contract.description}\nRules:\n{rules}"


def build_role_system_instruction(
    base_system_instruction: str, role: ChatRole, contract: Contract
) -> str:
    contract_text = build_chat_contract_text(contract)
    return (
        f"{base_system_instruction}\n\n"
        f"{role.system_prompt}\n\n"
        "Contract (must follow):\n"
        f"{contract_text}"
    )


def build_merged_roles_system_instruction(
    base_system_instruction: str,
    roles: list[ChatRole],
    contracts: list[Contract],
) -> str:
    parts = [base_system_instruction, "Active roles (apply in listed order):"]
    for role, contract in zip(roles, contracts):
        parts.append(f"\n## Role: {role.id}\n{role.system_prompt}")
        parts.append("Contract:\n" + build_chat_contract_text(contract))
    return "\n".join(parts)


# Backstop for every LLM call in this module, regardless of role or budget
# sizing above: Ollama reports done_reason="length" when a reply was cut off
# by num_predict, not because it actually finished. Rather than trust sizing
# heuristics to always guess right, detect that signal directly and ask the
# model to keep going instead of silently handing back an unfinished answer.
_MAX_AUTO_CONTINUE_ROUNDS = 4
_CONTINUE_NUDGE = (
    "Continue exactly where you left off — no repetition, no new preamble, "
    "just carry on the response until it is complete."
)


def _is_thinking_sequence_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "samebatch" in msg or "numkeep" in msg or "failed to create new sequence" in msg


def _chat_call(
    profile: dict,
    model: str,
    messages: list,
    *,
    stream: bool,
    sink=None,
    disable_thinking: bool = False,
    extra_options: dict | None = None,
    response_format: dict | None = None,
):
    from pipeline.i18n import apply_locale_to_messages

    messages = apply_locale_to_messages(messages)
    kwargs, timings = build_chat_request(
        profile,
        model=model,
        messages=messages,
        stream=stream,
        disable_thinking=disable_thinking,
        extra_options=extra_options,
        response_format=response_format,
    )
    if sink is not None:
        sink.log(
            f"LLM request prep: {timings['build_ms']}ms"
            + (f" (think probe {timings['think_probe_ms']}ms)" if timings["think_probe_ms"] else "")
        )
    try:
        with ResourceGovernor.acquire("llm_chat"):
            t_call = time.perf_counter()
            return chat_client.chat(**kwargs), t_call
    except Exception as exc:
        if kwargs.get("think") is None or not _is_thinking_sequence_error(exc):
            raise
        if sink is not None:
            sink.log(
                "Reasoning mode failed (model KV cache); retrying without thinking…"
            )
        retry = dict(kwargs)
        retry.pop("think", None)
        opts = dict(retry.get("options") or {})
        opts["num_batch"] = 1
        retry["options"] = opts
        with ResourceGovernor.acquire("llm_chat"):
            t_call = time.perf_counter()
            return chat_client.chat(**retry), t_call


def generate_text_sync(
    profile: dict,
    model: str,
    messages: list[dict[str, Any]],
    *,
    disable_thinking: bool = False,
    sink=None,
    extra_options: dict | None = None,
    response_format: dict | None = None,
) -> str:
    round_messages = list(messages)
    parts: list[str] = []
    for round_idx in range(1 + _MAX_AUTO_CONTINUE_ROUNDS):
        resp, t_call = _chat_call(
            profile,
            model,
            round_messages,
            stream=False,
            sink=sink,
            disable_thinking=disable_thinking,
            extra_options=extra_options,
            response_format=response_format,
        )
        if sink is not None:
            sink.log(f"LLM response: {round((time.perf_counter() - t_call) * 1000, 1)}ms")
        text = ((resp.get("message") or {}).get("content") or "").strip()
        parts.append(text)
        if (resp.get("done_reason") or "") != "length" or not text:
            break
        if sink is not None:
            sink.log(f"Response hit the token limit — continuing (round {round_idx + 2})…")
        round_messages = round_messages + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": _CONTINUE_NUDGE},
        ]
    return "".join(parts).strip()


def stream_chat_response(
    *,
    profile: dict,
    model: str,
    messages: list[dict],
    sink,
    is_cancelled,
    disable_thinking: bool = False,
    extra_options: dict | None = None,
    stream_base: str = "",
) -> str:
    base = (stream_base or "").rstrip()
    if base:
        sink.set_assistant_content(base + "\n\n")

    round_messages = list(messages)
    total_text = ""
    for round_idx in range(1 + _MAX_AUTO_CONTINUE_ROUNDS):
        stream, t_call = _chat_call(
            profile,
            model,
            round_messages,
            stream=True,
            sink=sink,
            disable_thinking=disable_thinking,
            extra_options=extra_options,
        )
        first_token_logged = False
        round_text = ""
        done_reason = ""
        cancelled = False
        for chunk in stream:
            if is_cancelled():
                sink.log("Generation stopped by user.")
                cancelled = True
                break
            msg = chunk.get("message") or {}
            thinking = msg.get("thinking") or ""
            token = msg.get("content") or chunk.get("response") or ""
            if not first_token_logged and (thinking or token):
                sink.log(
                    f"LLM time-to-first-token: {round((time.perf_counter() - t_call) * 1000, 1)}ms"
                )
                first_token_logged = True
            if thinking:
                sink.append_assistant_thinking_token(thinking)
                sink.refresh_chat_throttled()
            if token:
                round_text += token
                sink.append_assistant_token(token)
                sink.refresh_chat_throttled()
                try:
                    from services.session.draft import mirror_streaming_draft_to_preview

                    mirror_streaming_draft_to_preview(sink)
                except Exception:
                    pass
            if chunk.get("done"):
                done_reason = chunk.get("done_reason") or done_reason
        total_text += round_text
        if first_token_logged:
            sink.log(f"LLM stream complete: {round((time.perf_counter() - t_call) * 1000, 1)}ms")
        if cancelled or done_reason != "length" or not round_text.strip():
            break
        sink.log(f"Response hit the token limit — continuing (round {round_idx + 2})…")
        round_messages = round_messages + [
            {"role": "assistant", "content": round_text},
            {"role": "user", "content": _CONTINUE_NUDGE},
        ]

    sink.refresh_chat()
    sink.scroll_chat()

    return total_text.strip()


def validate_or_repair_prompt(text: str, contract: Contract) -> tuple[bool, str]:
    result = validate_output(text, contract)
    return result.valid, result.repair_hint
