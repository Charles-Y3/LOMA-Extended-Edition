# -*- coding: utf-8 -*-
"""Express-style chat with optional web grounding."""
from __future__ import annotations

from typing import Any

from pipeline.direct.express_runner import _express_history_messages
from services.grounded_chat import gather_grounded_context, grounded_system_prompt, refine_grounded_answer


def run_grounded_chat(inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    state = inputs["state"]
    sink = inputs["sink"]
    request = inputs["request"]
    bundle = inputs["bundle"]
    prof = inputs.get("prof") or {}

    from services.session.workflow_control import is_cancelled
    from services.model_router import resolve_chat_model, needs_vision
    from pipeline.capability_runtime.chat_runner import stream_chat_response

    llm_type = (config.get("llm_type") or "text").strip().lower()
    vision = needs_vision(llm_type, bundle.images)

    gen_model = str(inputs.get("gen_model") or config.get("model") or "").strip()
    if gen_model:
        chat_model, vision_error = gen_model, None
    else:
        chat_model, vision_error = resolve_chat_model(
            prof, llm_type, bundle.images, context_files=getattr(request, "context_files", None) or []
        )
    if vision_error:
        sink.set_assistant_content(vision_error)
        sink.refresh_chat()
        return {"content": "", "output_type": "chat"}

    from pipeline.i18n import t as tr

    sink.ensure_assistant_message()
    sink.set_assistant_content(tr("chat.searching_web"))
    sink.refresh_chat()

    web_ctx, sources = gather_grounded_context(request.user_input, log_fn=sink.log)

    if not web_ctx:
        sink.set_assistant_content(tr("chat.grounded_fetch_failed"))
        sink.refresh_chat()
        return {"content": state.messages[-1].get("content", "") if state.messages else "", "output_type": "chat"}

    sink.set_assistant_content("")
    sink.refresh_chat()

    history = _express_history_messages(state)
    parts: list[str] = [web_ctx, f"User question:\n{request.user_input}"]
    if bundle.unified_text:
        ctx = bundle.unified_text.strip()[:4000]
        parts.insert(0, f"Workspace context:\n{ctx}")
    last_user = "\n\n".join(parts)

    stream_messages: list[dict] = [{"role": "system", "content": grounded_system_prompt()}]
    stream_messages.extend(history)
    stream_messages.append({"role": "user", "content": last_user})

    sink.log(f"Grounded chat: model={chat_model}, {len(sources)} source(s)")

    stream_chat_response(
        profile=prof,
        model=chat_model,
        messages=stream_messages,
        sink=sink,
        is_cancelled=is_cancelled,
        disable_thinking=True,
        # num_ctx intentionally omitted — resolved from Settings > Configuration via
        # build_model_options() so it stays consistent with express/highlight/warm-up
        # (Ollama reloads the model whenever num_ctx changes between calls).
        extra_options={"num_predict": 1024},
    )

    content = state.messages[-1].get("content", "") if state.messages else ""
    if content.strip():
        sink.log("Grounded chat: verifying answer against web snippets…")
        verified = refine_grounded_answer(
            request.user_input,
            web_ctx,
            content,
            model=chat_model,
            log_fn=sink.log,
        )
        if verified != content:
            content = verified
            state.messages[-1]["content"] = content
            sink.set_assistant_content(content)
            sink.refresh_chat()

    if sources and content:
        refs = "\n".join(f"- [{s['title'][:72]}]({s['url']})" for s in sources[:5])
        if refs and refs not in content:
            content = f"{content.rstrip()}\n\n**Sources**\n{refs}"
            state.messages[-1]["content"] = content
            sink.set_assistant_content(content)
            sink.refresh_chat()

    return {"content": content, "output_type": "chat"}
