# Global widget references set during layout build

chat_scroll = None
chat_container = None
log_inner = None
chat_input = None
chat_send_btn = None
chat_send_tooltip = None
chat_mode_select = None
progress_bar = None

input_panel = None
extension_panel = None
workspace_panel = None
output_panel = None

profile_select = None
output_format_select = None
default_model_select = None
tts_voice_select = None
whisper_default_select = None
refresh_whisper_select_fn = None
vision_default_select = None
refresh_vision_select_fn = None
image_default_select = None
refresh_image_select_fn = None
traditional_chinese_container = None
refresh_traditional_chinese_fn = None
extension_select = None
extension_tier_badge = None
active_extension = None
extension_placeholder_label = None

# token -> zero-arg callable that reopens a still-pending poster/presentation
# style-picker dialog with its ORIGINAL on_select callback (see
# pipeline/direct/entry.py::_run_presentation_style_picker and
# pipeline/direct/step_executor.py::_run_poster_style_picker). Lets the "Reopen
# style picker" chat button (ui/components/chat_message.py) just reshow the
# same dialog instead of resubmitting the request as a brand-new chat turn.
style_picker_reopeners: dict = {}

input_open_btn = None
output_open_btn = None

panel_splitter = None
ext_ws_splitter = None
center_row = None
extension_content = None
edit_select = None
suppress_extension_change = False
extension_panel_open = False
sources_uploader = None

preview_tab_inner = None
sandbox_panel_inner = None
sandbox_tab_ref = None
sandbox_code_ta = None
sandbox_stdin_ta = None
sandbox_stdin_section = None
sandbox_output_ta = None
sandbox_status_label = None
sandbox_run_btn = None
sandbox_stop_btn = None
sandbox_inner_tabs = None
sandbox_code_tab = None
sandbox_output_tab = None
preview_editor = None
preview_status_label = None
preview_char_label = None
preview_view_mode_btn = None
preview_markup_holder = None
preview_hint_label = None
