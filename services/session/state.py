# core/state.py
# -*- coding: utf-8 -*-
import hashlib
import os
import json
from nicegui import ui
import config

SETTINGS_FILE = config.SETTINGS_FILE


def artifact_fingerprint(*parts: str) -> str:
    """Identity of a would-be artifact, from the inputs that determine its content
    (mode/output_type/source filename/instruction-or-draft-text). Two requests with
    the same fingerprint would generate the same file; a mismatch means a cached
    last_generated_file_path belongs to a different request and must not be reused."""
    h = hashlib.sha256()
    for part in parts:
        h.update((part or "").encode("utf-8", "ignore"))
        h.update(b"\x00")
    return h.hexdigest()

# Startup / routing warmup (General model for InputRouter)
loma_ready = False
routing_model_ready = False
routing_warmup_status = "pending"  # pending | loading | ready | failed
routing_model_name = ""
routing_warmup_error = ""

# Central Reactive State Containers
messages = [{"role": "assistant", "content": "Hi, I'm LOMA — what can I help you with today?", "bootstrap": True}]
progress_state = {"Intent": "⚪", "Planner": "⚪", "Execution": "⚪", "Synthesis": "⚪"}
ORCHESTRA_LOG_MAX = 500
orchestra_log = ["System initialized...", "Awaiting User Input"]
active_context_files = []
# Bumped once at the end of every completed workflow turn (pipeline/workflow.py).
# Files attached to active_context_files are stamped with the counter's value at
# attach time (services/session/handlers.py) — a stamp equal to the CURRENT value
# means "attached since the last completed turn" (this message's own attachments),
# distinguishing a fresh batch of uploads from files left over in the basket from
# an earlier, unrelated turn.
context_attach_turn = 0
active_web_links = []
# Append-only record of every file/link attached this session — {"kind": "file"|"link",
# "name": ..., "turn": ...}. Unlike active_context_files/active_web_links (the live
# "basket" fed to the current request, which end_workflow() deliberately clears after
# each turn so an old upload doesn't leak into a later, unrelated request), this list
# is never read when building a request — only the session summary (chat_summarize.py)
# reads it, so it needs to survive the per-turn clearing. Reset only on a real new
# session (reboot_workspace/clear_context), alongside `messages`.
session_sources_log: list[dict] = []
web_scrape_cache = {}  # url -> scraped markdown (avoid re-scraping on follow-up questions)
# Parsed sources + media resolution cache (invalidated on file/link change or reset)
context_bundle_cache_key = None
context_parsed_sources_cache = None
context_images_cache: list = []
context_media_blocks_cache: list = []
context_source_digests_cache = None
chart_artifacts = []  # ChartArtifact list from graph_generation for ordered report embed
dataset_overview_md = ""  # Deterministic 'Dataset Overview' facts (date range, correlations, outliers)
dataset_computed_stats = []  # compute_stats() entries per profiled dataset — ground truth for fact-checking

# Dynamic reference holder for settings bound at system startup
current_settings = {}

# Resolved once per client connection when theme == "system" (browser prefers-color-scheme
# via ui.run_javascript — see ui/layouts/main_layout.py's _resolve_system_theme). Kept
# separate from current_settings, not inside it, so it never gets written to
# data/settings.json by save_settings()'s whole-dict dump — it's a live per-session
# detection, not a persisted preference, and would otherwise go stale across restarts.
system_theme_is_dark: bool | None = None

# Last user-facing routing explanation (Why this path?)
last_routing_summary = ""
# Human stage line under the progress bulbs
progress_detail = ""

# Last artifact path produced by the engine (preview/download)
last_generated_file_path = None
# Fingerprint (see artifact_fingerprint()) of the request that produced
# last_generated_file_path — lets reuse short-circuits verify the cached file
# actually matches the current request instead of trusting artifact_ready/
# preview_dirty alone (those said nothing about *what* was generated).
last_artifact_fingerprint = None

# Last standalone image diffusion run (export must not re-diffuse from caption markdown)
last_image_diffusion_prompt = ""
last_image_user_query = ""
last_image_generation_meta: dict = {}  # seed, steps, model_id, width, height, ...

# Live Preview workspace (Stage 1 — edit before Export)
draft_content = ""  # canonical LOMA-generated draft (Preview + Export source)
live_workspace_html = ""
live_workspace_plain = ""
live_workspace_output_type = "document"
live_workspace_mode = None  # generation | mutation | None
pending_output_type = None  # set during active workflow (for streaming sync)
mutation_map = None  # id→text from last template mutation plan
last_user_instruction = ""  # last text typed in a prompt (not composed excerpt wrappers)
active_workflow_instruction = ""  # full mission text for in-flight workflow (incl. excerpt wrappers)

# Viewer extensions (document_viewer, web_viewer) — execution policy and Lite lock
viewer_execution_policy = "lite_only"  # lite_only | lite_or_pro
viewer_lite_lock_active = False
viewer_saved_execution_mode = None
viewer_lock_owner = ""
# Legacy aliases (document viewer)
document_viewer_execution_policy = viewer_execution_policy
document_viewer_lite_lock_active = viewer_lite_lock_active
document_viewer_saved_execution_mode = viewer_saved_execution_mode

# Template mutation progress (Preview + console sync)
mutation_in_progress = False
mutation_slide_current = 0
mutation_slide_total = 0
mutation_work_path = ""
artifact_ready = False  # True when last_generated_file_path is fully mutated
mutation_span_map = None  # English phrase → translation for run-level apply
preview_dirty = False  # True when Preview edits are not yet compiled to disk
preview_selection = ""  # Text highlighted in Preview for targeted revision
preview_sel_start = -1  # Character index in draft (mouseup capture)
preview_sel_end = -1
preview_scroll_top = 0  # Textarea scroll position (preserve on in-place edits)
preview_view_mode = "viewer"  # viewer | source (legacy: markup, markdown)

# Active LLM workflow (workspace send / stop control)
workflow_active = False
workflow_cancel_requested = False

# Factory SDLC: human choice pause and extension confirm-before-register
pending_human_choice = None
pending_extension_confirm = None

# Agentic loop: plan review pause (Pro mode)
pending_plan_review = None
pending_delivery_review = None

# Current agentic run log directory (data/agentic/YYYYMMDD_HHMMSS)
agentic_log_dir = None

# Sandbox panel (software deliverable)
sandbox_code = ""
sandbox_stdin = ""
sandbox_output = ""
sandbox_status = "idle"
sandbox_running = False


# Capability definitions
LOMA_CAPABILITIES = [
    {"name": "Document Parsing", "description": "Extracts and structures uploaded files", "status": "active"},
    {"name": "Web Retrieval", "description": "Loads and analyzes web links", "status": "active"},
    {"name": "Code Generation", "description": "Generates and edits code dynamically", "status": "active"},
    {"name": "Dependency Resolution", "description": "Installs and manages required dependencies",
     "status": "experimental"},
    {"name": "Workflow Planning", "description": "Builds execution plans autonomously", "status": "active"}
]

def get_ui_module():
    """Resolves interface elements regardless of package directory changes."""
    from ui.layouts import main_layout as ui_module
    return ui_module


def add_log(msg):
    """Appends workflow status lines, persists them to the log file
    (Settings > About > Open log file), and updates matching UI frames."""
    orchestra_log.append(msg)
    if len(orchestra_log) > ORCHESTRA_LOG_MAX:
        orchestra_log.pop(0)
    try:
        from services.app_log import log as _app_log

        _app_log(msg)
    except Exception:
        pass
    ui_module = get_ui_module()
    if hasattr(ui_module, 'render_logs') and hasattr(ui_module.render_logs, 'refresh'):
        ui_module.render_logs.refresh()


def clear_pipeline():
    """Resets operational stage metrics back to standby."""
    ui_module = get_ui_module()
    progress_state.update({"Intent": "⚪", "Planner": "⚪", "Execution": "⚪", "Synthesis": "⚪"})
    if hasattr(ui_module, 'render_progress') and hasattr(ui_module.render_progress, 'refresh'):
        ui_module.render_progress.refresh()


def clear_context():
    """Flushes active working file contexts out of memory."""
    active_context_files.clear()
    session_sources_log.clear()
    context_bundle_cache_key = None
    context_parsed_sources_cache = None
    context_images_cache.clear()
    context_media_blocks_cache.clear()
    context_source_digests_cache = None
    add_log("Context memory cleared.")
    ui.notify("File context cleared.", color='info')


def log_session_source(kind: str, name: str) -> None:
    """Record a file/link in the session-long history (see session_sources_log).
    Dedupes by (kind, name) so re-attaching the same source doesn't spam the log."""
    name = (name or "").strip()
    if not name:
        return
    if any(e.get("kind") == kind and e.get("name") == name for e in session_sources_log):
        return
    session_sources_log.append({"kind": kind, "name": name, "turn": context_attach_turn})


def add_web_link(link):
    """Integrates web addresses into search evaluation targets."""
    if not link:
        return
    active_web_links.append(link)
    log_session_source("link", link)
    add_log(f"Web context added: {link}")
    ui.notify(f"Web link added to LOMA context.", color='positive')


def update_role_in_memory(role, new_model_args):
    """Modifies local setting profiles directly in system memory."""
    if isinstance(new_model_args, dict):
        model_string = new_model_args.get('label') or new_model_args.get('name') or str(new_model_args)
    else:
        model_string = str(new_model_args)
    current_settings["assignments"][role] = model_string
    if hasattr(config, 'sync_roles'):
        config.sync_roles(current_settings["assignments"])