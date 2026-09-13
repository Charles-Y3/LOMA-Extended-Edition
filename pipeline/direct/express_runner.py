# -*- coding: utf-8 -*-

"""Express-lane direct chat execution."""

from __future__ import annotations



from typing import Any



_EXPRESS_HISTORY_TURNS = 6

_EXPRESS_MSG_CHAR_CAP = 1200

def _express_system(user_input: str) -> str:
    from pipeline.i18n import get_locale, infer_language_from_query, language_system_rule

    locale = infer_language_from_query(user_input) or get_locale()
    lang = language_system_rule(locale)
    return (
        "You are LOMA, a helpful local assistant.\n"
        f"{lang}\n"
        "Rules:\n"
        "- Answer directly. Never ask the user to repeat or supply content you should provide.\n"
        "- Always follow the LANGUAGE rule above for the full reply (stories, jokes, summaries, "
        "explanations). Do not mirror the user's query language when it conflicts with LANGUAGE.\n"
        "- If they ask for a joke (笑話/笑话, tell me a joke): YOU tell a short joke now, "
        "in the LANGUAGE above.\n"
        "- If they say you told a joke, react naturally or tell another — do not ask them to paste it.\n"
        "- Use workspace context when it is provided.\n"
        "- Never prefix replies with status boilerplate (e.g. 'LOMA System Online' or 'Awaiting mission parameters').\n"
        "- You have no live/real-time data access (no internet browsing unless web grounding is explicitly "
        "on and used for this query). For time-sensitive facts — current weather, stock/crypto prices, "
        "sports scores, breaking news, today's date-dependent events — say plainly that you don't have "
        "live access, instead of inventing a specific-sounding answer. Tell the user to turn on "
        "'Search the internet for grounded replies' in Settings → General to let you look it up live — "
        "that is the fix, not a generic external website suggestion.\n"
        "About LOMA Extended Edition (answer questions about the app itself directly, from this knowledge):\n"
        "- This is LOMA Extended Edition — an online-enabled build with a simplified interface and a "
        "mid-sized extension set (larger than Core Edition, smaller than Complete Edition).\n"
        "- Settings (gear icon, top bar): Everyday (language, theme, day-to-day toggles), Models (pick the "
        "default text/vision/Whisper model), Model Library (download/remove models, hardware profile), "
        "About (version + manual update check). General also has 'Search the internet for grounded "
        "replies' — a toggle that lets LOMA search the web for live facts (weather, prices, news, "
        "'latest…') before answering; off by default.\n"
        "- Mode: Direct only — fast single-pass answers and generation, no separate planner/plan-approval "
        "mode in this edition.\n"
        "- LOMA is free with no usage quota and no user tiers (no standard/pro split) — every feature is "
        "available to everyone.\n"
        "- Preview panel: shows generated documents (saved under data/generated/); open, download, or keep "
        "editing there.\n"
        "- Generation capabilities — LOMA actually produces these files itself, locally, not just prompts "
        "for other tools: documents (.docx) and presentations (.pptx, with real per-slide images) can be "
        "generated as a deliverable — just ask, e.g. 'write me a report on X as a document' or 'make me a "
        "slide deck on Y'. Image generation is not just plain photos — LOMA picks the right renderer from "
        "how you ask: a poster (a styled flyer/announcement with real overlaid text, e.g. 'make a poster "
        "for my picnic on Aug 11'), an infographic (a stat/icon grid, timeline, or comparison layout, e.g. "
        "'infographic on key facts about X'), a flowchart/diagram (boxes-and-arrows process or org chart, "
        "e.g. 'flowchart of our onboarding process'), a real data chart (an actual plotted chart from real "
        "numbers, not an AI-drawn fake one, e.g. 'chart of unemployment over the last decade'), or a plain "
        "photo/artwork otherwise.\n"
        "- Highlight and ask: select passage text in a viewer to ask about or revise only that excerpt.\n"
        "- URLs pasted directly into a chat message are NOT fetched or read just because they appear in "
        "the text — only files/links added as Sources are actually read, or a web search performed when "
        "'Search the internet for grounded replies' is on and the query needs live facts. If the user's "
        "message contains a URL and asks what it's about, and neither of those applied, you have NOT seen "
        "that page's real content: say so plainly instead of inventing a summary. You may offer a general "
        "guess about the likely topic from the URL text itself (domain, slug, date), but only if you "
        "clearly label it as a guess, not a summary of the actual page.\n"
        "- Extensions (widgets icon, top bar) available in this edition: Chat Archive, Document Editor, "
        "Document Intelligence, History Events, News Brief, Research, Token Usage, Web Viewer (load any "
        "URL, then use its Summarize or Extract Key Points buttons to condense the page, or Copy to grab "
        "the raw text); they run in a parallel panel, not the chat pipeline.\n"
        "- Voice: the mic icon by the chat box does local speech-to-text — click once to start, click "
        "again (or pause ~2.5s) to stop and auto-send. Right-click the mic instead for hands-free "
        "conversation mode: it keeps listening, auto-sends what you say, waits for LOMA's reply to be "
        "fully spoken, then re-arms the mic on its own — no clicking between turns. Conversation mode "
        "auto-enables 'Speak replies aloud' for its duration (restored to its prior value on exit) and "
        "auto-exits after ~2 minutes of silence. 'Speak replies aloud' (Settings > Everyday) makes LOMA "
        "read its replies aloud via local neural TTS as they stream in, sentence by sentence, rather than "
        "waiting for the whole reply to finish."
    )


def _bootstrap_online_text() -> str:
    from pipeline.i18n import t as tr

    return tr("chat.online").strip()


def _is_bootstrap_message(msg: dict) -> bool:
    if msg.get("bootstrap"):
        return True
    if (msg.get("role") or "") != "assistant":
        return False
    return (msg.get("content") or "").strip() == _bootstrap_online_text()


def _sanitize_history_content(content: str) -> str:
    online = _bootstrap_online_text()
    text = (content or "").strip()
    if not text or text == online:
        return ""
    if text.startswith(online):
        return text[len(online) :].lstrip(" .").strip()
    return text


def _express_history_messages(state) -> list[dict]:

    prior = (state.messages or [])[:-1][-_EXPRESS_HISTORY_TURNS:]

    out: list[dict] = []

    for msg in prior:

        if not isinstance(msg, dict):

            continue

        if _is_bootstrap_message(msg):

            continue

        role = msg.get("role")

        if role not in ("user", "assistant"):

            continue

        content = _sanitize_history_content(msg.get("content") or "")

        if not content:

            continue

        if len(content) > _EXPRESS_MSG_CHAR_CAP:

            content = content[:_EXPRESS_MSG_CHAR_CAP] + "…"

        out.append({"role": role, "content": content})

    return out





def run_express_chat(inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:

    """Single streaming chat pass — no planner, no role-pipeline overhead."""

    state = inputs["state"]

    sink = inputs["sink"]

    request = inputs["request"]

    bundle = inputs["bundle"]

    prof = inputs.get("prof") or {}

    profile_id = inputs.get("profile_id") or ""



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



    sink.ensure_assistant_message()

    sink.set_assistant_content("")

    sink.refresh_chat()



    if vision:

        parts = []

        if bundle.unified_text:

            parts.append(f"--- Parsed sources ---\n{bundle.unified_text.strip()}")

        parts.append(f"--- User request ---\n{request.user_input}")

        stream_chat_response(

            profile=prof,

            model=chat_model,

            messages=[{"role": "user", "content": "\n\n".join(parts), "images": bundle.images}],

            sink=sink,

            is_cancelled=is_cancelled,

            disable_thinking=True,

        )

    else:

        history = _express_history_messages(state)

        last_user = request.user_input

        if bundle.unified_text:

            ctx = bundle.unified_text.strip()
            if len(ctx) > 6000:
                ctx = ctx[:6000] + "…"
            last_user = f"Context:\n{ctx}\n\nRequest: {last_user}"



        stream_messages: list[dict] = [{"role": "system", "content": _express_system(request.user_input)}]

        stream_messages.extend(history)

        stream_messages.append({"role": "user", "content": last_user})



        prompt_chars = sum(len(m.get("content", "")) for m in stream_messages)

        if history:

            sink.log(f"Express lane: {len(history)} prior turn(s), {prompt_chars:,} prompt chars")

        else:

            sink.log(f"Express lane: model={chat_model}, {prompt_chars:,} prompt chars")



        stream_chat_response(
            profile=prof,
            model=chat_model,
            messages=stream_messages,
            sink=sink,
            is_cancelled=is_cancelled,
            disable_thinking=True,
            # num_ctx intentionally omitted — resolved from Settings > Configuration via
            # build_model_options() so it stays consistent with grounded/highlight/warm-up
            # (Ollama reloads the model whenever num_ctx changes between calls) and
            # respects the user's configured value instead of silently overriding it.
            extra_options={"num_predict": 1024},
        )



    content = state.messages[-1].get("content", "") if state.messages else ""
    from pipeline.instruction_priority import strip_priority_preamble_echo

    content = strip_priority_preamble_echo(content)
    if state.messages and state.messages[-1].get("role") == "assistant":
        state.messages[-1]["content"] = content

    return {"content": content, "output_type": "chat"}


