# -*- coding: utf-8 -*-
"""Deliver generated software to the Sandbox panel (not data/generated)."""
from __future__ import annotations

import re

from services.sandbox.interactive import run_python_script, stop_sandbox_run
from services.sandbox.runner import run_sandbox


def extract_python_code(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    if "```" in raw:
        blocks = re.findall(r"```(?:python)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
        if blocks:
            return blocks[-1].strip()
    return raw


def deliver_software_content(
    state,
    sink,
    content: str,
    *,
    workflow_instruction: str = "",
    run_after_load: bool = False,
    demo_stdin: str = "",
    user_request: str = "",
) -> str:
    """Populate sandbox state, optionally run, refresh UI. Returns extracted code."""
    from services.session import state as st

    if (workflow_instruction or "").strip() != (st.last_user_instruction or "").strip():
        sink.log("Skipped stale software delivery.")
        return ""

    code = extract_python_code(content)
    if not code:
        sink.log("No Python code found in output.")
        sink.set_assistant_content(
            "I could not extract runnable Python from the model output. "
            "Try again or switch to **Software** output format and rephrase your request."
        )
        return ""

    stop_sandbox_run()
    state.sandbox_output = ""
    state.sandbox_status = "idle"
    state.sandbox_running = False
    state.last_generated_file_path = None
    state.artifact_ready = False
    state.live_workspace_output_type = "software"
    state.preview_dirty = False

    check = run_sandbox(code, entry_file="script.py")
    if check.get("status") != "pass":
        state.sandbox_status = "fail"
        state.sandbox_output = str(check.get("output") or "Compile check failed")
        sink.log(f"Sandbox compile check: {state.sandbox_output[:200]}")
        sink.set_assistant_content(
            "**Software delivery failed compile check.**\n\n"
            f"Details are in the **Sandbox** tab under Output.\n\n"
            f"```\n{state.sandbox_output[:800]}\n```"
        )
        _refresh_sandbox_ui()
        _focus_sandbox_tab()
        return code

    state.sandbox_status = "ready"
    state.sandbox_code = code
    sink.log("Sandbox compile check passed.")
    _refresh_sandbox_ui()

    if run_after_load:
        _run_and_capture(state)

    summary = _completion_message(user_request, ran=run_after_load, state=state)
    sink.set_assistant_content(summary)
    sink.log("Software ready in Sandbox tab.")
    _notify_done()
    _refresh_sandbox_ui()
    _focus_sandbox_tab()
    return code


def _completion_message(user_request: str, *, ran: bool, state) -> str:
    topic = (user_request or "").strip()
    if len(topic) > 80:
        topic = topic[:77] + "…"
    head = "✅ **Your software is ready.**"
    if topic:
        head = f"✅ **Done — software for:** {topic}"

    lines = [
        head,
        "",
        "Open the **Output → Sandbox** tab to view and edit the code.",
        "- **Run** — execute with optional stdin (for `input()` prompts)",
        "- **Stop** — cancel a running script (also stops when you hit **Stop** on chat)",
    ]
    if ran and state.sandbox_output:
        preview = str(state.sandbox_output)[:600]
        lines.extend(["", "**First run output:**", f"```\n{preview}\n```"])
    elif not ran:
        lines.append("")
        lines.append("Click **Run** in Sandbox when you want to try it.")
    return "\n".join(lines)


def run_sandbox_from_state(state) -> dict:
    code = getattr(state, "sandbox_code", "") or ""
    stdin_text = getattr(state, "sandbox_stdin", "") or ""
    if not code.strip():
        return {"status": "fail", "output": "No code in sandbox editor."}
    return _run_and_capture(state, stdin_text=stdin_text)


def stop_sandbox_from_ui(state) -> None:
    stopped = stop_sandbox_run()
    state.sandbox_running = False
    state.sandbox_status = "stopped" if stopped else getattr(state, "sandbox_status", "idle")
    if stopped:
        state.sandbox_output = (state.sandbox_output or "") + "\n[Stopped by user]"
        state.add_log("Sandbox run stopped.")


def _run_and_capture(state, stdin_text: str | None = None) -> dict:
    stdin_text = stdin_text if stdin_text is not None else getattr(state, "sandbox_stdin", "") or ""
    try:
        result = run_python_script(
            state.sandbox_code,
            stdin_text=stdin_text,
            filename="script.py",
        )
    except Exception as exc:
        result = {"status": "error", "output": str(exc), "stopped": False}

    state.sandbox_output = str(result.get("output") or "")
    if result.get("stopped"):
        state.sandbox_status = "stopped"
    else:
        state.sandbox_status = str(result.get("status") or "fail")
    return result


def _refresh_sandbox_ui() -> None:
    try:
        from services.session.workflow_control import schedule_on_ui
        from ui.components import sandbox_workspace
        from ui.themes import registry as ui_registry
        from services.session import state as session_state

        code = getattr(session_state, "sandbox_code", "") or ""

        def _sync() -> None:
            if ui_registry.sandbox_code_ta is not None:
                sandbox_workspace.load_sandbox_code(code)
            else:
                sandbox_workspace.sync_display()

        schedule_on_ui(_sync)
    except Exception:
        pass


def _focus_sandbox_tab() -> None:
    try:
        from services.session.workflow_control import schedule_on_ui

        schedule_on_ui(
            lambda: __import__("nicegui", fromlist=["ui"]).ui.run_javascript(
                "const t=document.querySelector('[data-loma-sandbox-tab]'); if(t) t.click();"
            )
        )
    except Exception:
        pass


def _notify_done() -> None:
    try:
        from services.session.workflow_control import schedule_on_ui
        from nicegui import ui

        def _toast() -> None:
            from pipeline.i18n import t as tr
            from ui.components import sandbox_workspace

            ui.notify(tr("sandbox.done_toast"), type="positive", timeout=5000)
            sandbox_workspace.activate_output_tab()

        schedule_on_ui(_toast)
    except Exception:
        pass
