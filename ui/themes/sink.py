# -*- coding: utf-8 -*-
import os
import time

from nicegui import ui

from pipeline.state_machine import UISink
from services.session import state
from pipeline.i18n import t as _tr  # noqa: E402


class NiceGUIStateSink(UISink):
    def __init__(self):
        self._state = state
        self._last_chat_refresh = 0.0
        self._last_preview_sync = 0.0
        self._last_preview_panel_refresh = 0.0

    def _ui(self):
        return self._state.get_ui_module()

    def log(self, msg: str) -> None:
        self._state.add_log(msg)

    def set_progress(self, stage: str, status: str) -> None:
        self._state.progress_state[stage] = status

    def refresh_progress(self) -> None:
        def _do() -> None:
            ui_mod = self._ui()
            if hasattr(ui_mod, "render_progress") and hasattr(ui_mod.render_progress, "refresh"):
                ui_mod.render_progress.refresh()

        from services.session.workflow_control import schedule_on_ui

        schedule_on_ui(_do)

    def set_capability(self, name: str, status: str) -> None:
        for cap in self._state.LOMA_CAPABILITIES:
            if cap["name"] == name:
                cap["status"] = status

    def refresh_capabilities(self) -> None:
        pass

    def refresh_chat(self) -> None:
        def _do() -> None:
            ui_mod = self._ui()
            if hasattr(ui_mod, "render_chat") and hasattr(ui_mod.render_chat, "refresh"):
                ui_mod.render_chat.refresh()
            self.scroll_chat()

        from services.session.workflow_control import schedule_on_ui

        schedule_on_ui(_do)

    def refresh_chat_throttled(self, interval: float = 0.1) -> None:
        now = time.time()
        if now - self._last_chat_refresh >= interval:
            self._last_chat_refresh = now
            self.refresh_chat()
        else:
            self.scroll_chat()

    def scroll_chat(self) -> None:
        try:
            from ui.themes.assets import schedule_scroll_chat

            schedule_scroll_chat(0.02)
        except Exception:
            pass

    def ensure_assistant_message(self) -> None:
        if not self._state.messages or self._state.messages[-1]["role"] != "assistant":
            self._state.messages.append({"role": "assistant", "content": "", "thinking": ""})

    def append_assistant_thinking_token(self, token: str) -> None:
        self.ensure_assistant_message()
        self._state.messages[-1].setdefault("thinking", "")
        self._state.messages[-1]["thinking"] += token

    def append_assistant_token(self, token: str) -> None:
        self.ensure_assistant_message()
        if token:
            self._state.messages[-1].pop("processing", None)
        self._state.messages[-1]["content"] += token
        if token:
            try:
                from services.voice_reply import feed_stream_token

                feed_stream_token(token, self._state.current_settings)
            except Exception:
                pass

    def set_assistant_content(self, content: str) -> None:
        self.ensure_assistant_message()
        self._state.messages[-1]["content"] = content
        self._state.messages[-1].pop("processing", None)

    def append_assistant_content(self, extra: str) -> None:
        self.ensure_assistant_message()
        self._state.messages[-1]["content"] += extra

    def attach_images_to_last_user(self, images: list) -> None:
        if images and self._state.messages and self._state.messages[-1]["role"] == "user":
            self._state.messages[-1]["images"] = images

    def sync_preview(self, *, scroll_to_bottom: bool = False) -> None:
        try:
            from services.session.workflow_control import schedule_on_ui
            from ui.components.preview_workspace import sync_preview_editor

            schedule_on_ui(
                lambda: sync_preview_editor(
                    preserve_scroll=not scroll_to_bottom,
                    scroll_to_bottom=scroll_to_bottom,
                )
            )
        except Exception:
            pass

    def sync_preview_throttled(self, interval: float = 0.12) -> None:
        now = time.time()
        if now - self._last_preview_sync >= interval:
            self._last_preview_sync = now
            self.sync_preview(scroll_to_bottom=True)

    def refresh_preview_panel_throttled(self, interval: float = 0.45) -> None:
        if not hasattr(self, "_last_preview_panel_refresh"):
            self._last_preview_panel_refresh = 0.0
        now = time.time()
        if now - self._last_preview_panel_refresh < interval:
            return
        self._last_preview_panel_refresh = now
        try:
            from services.session.workflow_control import schedule_on_ui
            from ui.components.preview_workspace import refresh_preview_panel

            schedule_on_ui(refresh_preview_panel)
        except Exception:
            pass

    def notify_preview_ready(self) -> None:
        try:
            from ui.components.loma_notify import notify

            notify(_tr("notify.draft_ready"), color="positive")
        except Exception:
            pass

    def notify_artifact_ready(self, path: str) -> None:
        if path and self._state.messages and self._state.messages[-1].get("role") == "assistant":
            self._state.messages[-1]["artifact_path"] = path
        try:
            from ui.components.loma_notify import notify

            notify(_tr("notify.file_ready", name=os.path.basename(path) if path else _tr("notify.file_generic")), color="positive")
        except Exception:
            pass

    def set_mutation_progress(self, slide: int, total: int) -> None:
        self._state.mutation_slide_current = slide
        self._state.mutation_slide_total = total
        self._state.mutation_in_progress = True

    def pulse_stages(self, stages: list[str], delay: float = 0.08) -> None:
        for stage in stages:
            self.set_progress(stage, "🟡")
            self.refresh_progress()
            time.sleep(delay)
            self.set_progress(stage, "🟢")
            self.refresh_progress()
