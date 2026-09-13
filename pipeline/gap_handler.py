# -*- coding: utf-8 -*-
"""Detect missing vision / image-generation deps and open on-demand installers (pipeline/UI)."""
from __future__ import annotations

from typing import Any, Callable

from services.model_router import (
    image_generation_deps_available,
    needs_vision,
    resolve_vision_model,
)
from ui.components.asset_downloader import subsystem_asset_status

GAP_VISION = "__LOMA_GAP_VISION__"
GAP_IMAGE_GEN = "__LOMA_GAP_IMAGE_GEN__"
GAP_TRANSCRIPTION = "__LOMA_GAP_TRANSCRIPTION__"
GAP_FFMPEG = "__LOMA_GAP_FFMPEG__"
GAP_PLAYWRIGHT = "__LOMA_GAP_PLAYWRIGHT__"

# Extension Library "requires" registry — maps a requirement key (declared in an
# extension's config/extension_catalog.json entry, e.g. "requires": ["image_generation"])
# to (is_satisfied_fn, offer_installer_fn) so the library can check + prompt to download
# BEFORE enabling, instead of the extension only discovering the gap on first use.
# Add an entry here whenever a new extension needs an optional add-on the "must have"
# bundled tools don't already cover — see extensions/EXTENSION_TEMPLATE.md.


def _schedule_ui(callback: Callable[[], None]) -> None:
    from services.session.workflow_control import schedule_on_ui

    schedule_on_ui(callback)


def _notify_next_action(message_key: str) -> None:
    """Surface a single, actionable sentence when a gap installer opens."""
    from pipeline.i18n import t as tr
    from ui.components.loma_notify import notify

    def _show() -> None:
        try:
            notify(tr(message_key), color="warning")
        except Exception:
            pass

    _schedule_ui(_show)


def vision_gap(llm_type: str, images: list) -> bool:
    return needs_vision(llm_type, images) and resolve_vision_model() is None


def image_gen_gap(output_type: str) -> bool:
    if (output_type or "").lower() != "image":
        return False
    if subsystem_asset_status("image_generation") == "Ready":
        return False
    ok, _ = image_generation_deps_available()
    return not ok


def offer_chat_model_installer(on_success: Callable[[], None] | None = None) -> None:
    from ui.components.capability_installer import OnDemandInstaller

    _notify_next_action("gap.chat_model_next")

    def _open() -> None:
        OnDemandInstaller(on_success=on_success or (lambda: None)).show_chat_model_installer()

    _schedule_ui(_open)


def offer_vision_installer(on_success: Callable[[], None] | None = None) -> None:
    from ui.components.capability_installer import OnDemandInstaller

    _notify_next_action("gap.action_install")

    def _open() -> None:
        OnDemandInstaller(on_success=on_success or (lambda: None)).show_vision_installer()

    _schedule_ui(_open)


def offer_image_gen_installer(on_success: Callable[[], None] | None = None) -> None:
    from ui.components.capability_installer import OnDemandInstaller

    _notify_next_action("gap.image_gen_retry")

    def _open() -> None:
        OnDemandInstaller(on_success=on_success or (lambda: None)).show_image_gen_installer()

    _schedule_ui(_open)


def offer_background_removal_installer(on_success: Callable[[], None] | None = None) -> None:
    from ui.components.capability_installer import OnDemandInstaller

    def _open() -> None:
        OnDemandInstaller(on_success=on_success or (lambda: None)).show_background_removal_installer()

    _schedule_ui(_open)


def offer_rag_installer(on_success: Callable[[], None] | None = None) -> None:
    from ui.components.capability_installer import OnDemandInstaller

    def _open() -> None:
        OnDemandInstaller(on_success=on_success or (lambda: None)).show_rag_installer()

    _schedule_ui(_open)


def offer_voice_reply_installer(on_success: Callable[[], None] | None = None) -> None:
    from ui.components.capability_installer import OnDemandInstaller

    def _open() -> None:
        OnDemandInstaller(on_success=on_success or (lambda: None)).show_voice_reply_installer()

    _schedule_ui(_open)


def offer_whisper_installer(on_success: Callable[[], None] | None = None) -> None:
    from ui.components.capability_installer import OnDemandInstaller

    _notify_next_action("gap.voice_next")

    def _open() -> None:
        OnDemandInstaller(on_success=on_success or (lambda: None)).show_hybrid_whisper_installer()

    _schedule_ui(_open)


def offer_sensevoice_installer(on_success: Callable[[], None] | None = None) -> None:
    from ui.components.capability_installer import OnDemandInstaller

    _notify_next_action("gap.voice_next")

    def _open() -> None:
        OnDemandInstaller(on_success=on_success or (lambda: None)).show_sensevoice_installer()

    _schedule_ui(_open)


def offer_doc_intel_installer(on_success: Callable[[], None] | None = None) -> None:
    from ui.components.capability_installer import OnDemandInstaller

    def _open() -> None:
        OnDemandInstaller(on_success=on_success or (lambda: None)).show_doc_intel_installer()

    _schedule_ui(_open)


def offer_rembg_installer(on_success: Callable[[], None] | None = None) -> None:
    from ui.components.capability_installer import OnDemandInstaller

    def _open() -> None:
        OnDemandInstaller(on_success=on_success or (lambda: None)).show_rembg_installer()

    _schedule_ui(_open)


def offer_ffmpeg_installer(on_success: Callable[[], None] | None = None) -> None:
    from ui.components.capability_installer import OnDemandInstaller

    _notify_next_action("gap.ffmpeg_next")

    def _open() -> None:
        OnDemandInstaller(on_success=on_success or (lambda: None)).show_ffmpeg_installer()

    _schedule_ui(_open)


def offer_playwright_installer(on_success: Callable[[], None] | None = None) -> None:
    from ui.components.capability_installer import OnDemandInstaller

    def _open() -> None:
        OnDemandInstaller(on_success=on_success or (lambda: None)).show_playwright_installer()

    _schedule_ui(_open)


def _image_generation_ready() -> bool:
    ok, _ = image_generation_deps_available()
    return ok


def _rag_ready() -> bool:
    from services.rag_embeddings import (
        E5_HF_MODEL_ID,
        _is_sentence_transformer_dir,
        rag_dependencies_available,
        resolve_e5_model_path,
    )

    ok, _ = rag_dependencies_available()
    if not ok:
        return False
    path = resolve_e5_model_path()
    if path != E5_HF_MODEL_ID and _is_sentence_transformer_dir(path):
        return True
    # HF hub cache — E5_HF_MODEL_ID's own cache folder name, in case it was downloaded
    # rather than found bundled (see resolve_e5_model_path in services/rag_embeddings.py)
    try:
        import os

        from huggingface_hub.constants import HF_HUB_CACHE

        folder = os.path.join(
            HF_HUB_CACHE, "models--" + E5_HF_MODEL_ID.replace("/", "--")
        )
        snap = os.path.join(folder, "snapshots")
        if os.path.isdir(snap) and any(os.scandir(snap)):
            return True
    except Exception:
        pass
    return False


def _whisper_ready() -> bool:
    from services.media_transcription import whisper_ready

    return whisper_ready()


def _sensevoice_ready() -> bool:
    from services.voice_input import sensevoice_ready

    return sensevoice_ready()


def _doc_intel_ready() -> bool:
    try:
        import rank_bm25  # noqa: F401
        import msoffcrypto  # noqa: F401

        return True
    except Exception:
        return False


def _rembg_ready() -> bool:
    from services.image_composite import rembg_available

    return rembg_available()


def _ffmpeg_ready() -> bool:
    from services.ffmpeg_util import ffmpeg_available

    return ffmpeg_available()


def _playwright_ready() -> bool:
    from services.web_fetch import browser_automation_ready

    return browser_automation_ready()


# Requirement key -> (is-satisfied check, installer opener). Also drives the Settings ->
# Configuration "Optional add-ons" panel (see REQUIREMENT_CATALOG below) and the default
# disabled-extensions list for fresh installs (services/session/settings._default_disabled_extensions).
_REQUIREMENT_CHECKS: dict[str, tuple[Callable[[], bool], Callable[[Callable[[], None] | None], None]]] = {
    "image_generation": (_image_generation_ready, offer_image_gen_installer),
    "rag": (_rag_ready, offer_rag_installer),
    "whisper": (_whisper_ready, offer_whisper_installer),
    "sensevoice": (_sensevoice_ready, offer_sensevoice_installer),
    "doc_intel": (_doc_intel_ready, offer_doc_intel_installer),
    "rembg": (_rembg_ready, offer_rembg_installer),
    "ffmpeg": (_ffmpeg_ready, offer_ffmpeg_installer),
    "playwright": (_playwright_ready, offer_playwright_installer),
}

# Display metadata for the Settings -> Configuration "Optional add-ons" panel — one entry
# per key above. `used_by` are extension_catalog.json ids the panel shows next to the tooltip.
REQUIREMENT_CATALOG: list[dict[str, Any]] = [
    {
        "key": "image_generation",
        "label": "Image generation",
        "tooltip": "PyTorch + Diffusers for local Stable Diffusion image generation.",
        "used_by": ["artwork_studio"],
    },
    {
        "key": "rembg",
        "label": "Background removal (rembg)",
        "tooltip": "AI cut-out for Artwork Studio Composite mode.",
        "used_by": ["artwork_studio"],
    },
    {
        "key": "playwright",
        "label": "Web page reading (Playwright)",
        "tooltip": (
            "Browser automation for grounded chat and Research. Downloads Playwright's "
            "Chromium unless Chrome/Edge is already installed."
        ),
        "used_by": ["research"],
    },
]


_REQUIREMENT_UNINSTALL_PACKAGES: dict[str, tuple[str, ...]] = {
    "image_generation": ("diffusers", "accelerate", "safetensors", "peft"),
    "rembg": ("rembg",),
}


def requirement_uninstall_packages(key: str) -> tuple[str, ...]:
    return _REQUIREMENT_UNINSTALL_PACKAGES.get(key, ())


def offer_requirement_uninstaller(key: str, *, on_success: Callable[[], None] | None = None) -> bool:
    """Pip-uninstall packages for a requirement key. Returns False if unknown."""
    packages = requirement_uninstall_packages(key)
    if not packages:
        return False
    import importlib

    from services.pip_runner import pip_uninstall

    ok, _output = pip_uninstall(list(packages))
    if not ok:
        return False
    # Ready/Missing checks use importlib.util.find_spec() or a plain `import` try/except —
    # both can return a stale "still installed" result within the same process without this.
    importlib.invalidate_caches()
    if on_success:
        try:
            on_success()
        except Exception:
            pass
    return True


def extensions_using_requirement(key: str, *, enabled_only: bool = True) -> list[str]:
    """Catalog extension ids that declare `key` in requires (optionally only enabled ones)."""
    import json
    import os

    from services.plugins.extension_prefs import is_extension_enabled

    path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "config", "extension_catalog.json"
    )
    try:
        with open(os.path.normpath(path), encoding="utf-8") as f:
            rows = json.load(f).get("extensions") or []
    except Exception:
        rows = []
    out: list[str] = []
    for row in rows:
        eid = str(row.get("id") or "")
        reqs = row.get("requires") or []
        if key not in reqs:
            continue
        if enabled_only and not is_extension_enabled(eid):
            continue
        out.append(eid)
    return out


def orphaned_requirements_for_extension(ext_id: str) -> list[str]:
    """requires keys that no other enabled extension still needs after disabling ext_id."""
    import json
    import os

    path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "config", "extension_catalog.json"
    )
    try:
        with open(os.path.normpath(path), encoding="utf-8") as f:
            rows = json.load(f).get("extensions") or []
    except Exception:
        return []
    row = next((r for r in rows if r.get("id") == ext_id), None)
    if not row:
        return []
    orphans: list[str] = []
    for key in row.get("requires") or []:
        others = [e for e in extensions_using_requirement(key, enabled_only=True) if e != ext_id]
        if not others:
            orphans.append(str(key))
    return orphans


def requirement_status(key: str) -> bool:
    """True if the named optional extension dependency is already satisfied.

    Unknown keys return True (satisfied) rather than blocking — an extension can only
    be gated on a requirement this registry actually knows how to check and install.
    """
    entry = _REQUIREMENT_CHECKS.get(key)
    if not entry:
        return True
    check_fn, _ = entry
    try:
        return bool(check_fn())
    except Exception:
        return False


def offer_requirement_installer(key: str, *, on_success: Callable[[], None] | None = None) -> bool:
    """Open the installer dialog for a named requirement. Returns False if `key` is unknown."""
    entry = _REQUIREMENT_CHECKS.get(key)
    if not entry:
        return False
    _, offer_fn = entry
    offer_fn(on_success)
    return True


def handle_vision_error_message(
    vision_error: str | None,
    *,
    on_success: Callable[[], None] | None = None,
) -> bool:
    if vision_error == GAP_VISION:
        offer_vision_installer(on_success)
        return True
    return False


def handle_image_gen_error_message(
    error: str | None,
    *,
    on_success: Callable[[], None] | None = None,
) -> bool:
    if error == GAP_IMAGE_GEN:
        offer_image_gen_installer(on_success)
        return True
    return False


def offer_transcription_installer(on_success: Callable[[], None] | None = None) -> None:
    """Alias — transcription for media/voice uses hybrid Whisper."""
    offer_whisper_installer(on_success)


def handle_transcription_error_message(
    message: str | None,
    *,
    on_success: Callable[[], None] | None = None,
) -> bool:
    if not message:
        return False
    if message == GAP_FFMPEG:
        offer_ffmpeg_installer(on_success)
        return True
    lower = (message or "").lower()
    if message == GAP_TRANSCRIPTION or "faster-whisper" in lower or "faster_whisper" in lower:
        offer_transcription_installer(on_success)
        return True
    if "ffmpeg" in lower:
        offer_ffmpeg_installer(on_success)
        return True
    return False
