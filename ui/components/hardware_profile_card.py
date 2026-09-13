# -*- coding: utf-8 -*-
"""Hardware summary card for setup wizard and configuration tab."""
from __future__ import annotations

from nicegui import ui

from pipeline.i18n import t
from services.system.profiler import (
    SystemProfile,
    effective_memory_gb,
    hardware_budget_gb,
    hardware_tier,
    refresh_system_profile,
)


def build_hardware_profile_card(
    container,
    profile: SystemProfile,
    *,
    theme_tokens: dict | None = None,
    on_refresh=None,
) -> None:
    muted = (theme_tokens or {}).get("muted", "text-gray-500")
    tier = hardware_tier(profile)
    effective = effective_memory_gb(profile)
    budget = hardware_budget_gb(profile)

    with container:
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(t("config.hardware_title")).classes("text-xs font-bold tracking-widest")
            if on_refresh:
                ui.button(icon="refresh", on_click=on_refresh).props("flat dense round")

        ui.markdown(
            t(
                "config.hardware_summary",
                ram=profile.total_ram_gb,
                free_ram=profile.available_ram_gb,
                vram=profile.vram_gb or "—",
                gpu=profile.gpu_name or t("setup.no_gpu"),
                budget=round(budget, 1),
                effective=round(effective, 1),
                tier=tier,
            )
        ).classes(f"text-xs {muted}")

        # A CUDA GPU exists but torch can't use it (CPU-only build) — offer a one-click
        # fix so the user isn't silently stuck on slow CPU inference (image generation,
        # vision models, etc. all fall back to CPU otherwise, with no UI hint why it's slow).
        if profile.gpu_present_but_unused:
            with ui.row().classes(
                "w-full items-center justify-between gap-2 mt-2 p-2 rounded "
                "border border-amber-400/40 bg-amber-400/10"
            ):
                ui.label(
                    t("config.gpu_unused_notice", gpu=profile.gpu_name or "NVIDIA GPU")
                ).classes("text-[11px] text-amber-300 flex-1 min-w-0 leading-snug")

                def _enable_gpu() -> None:
                    from ui.components.capability_installer import OnDemandInstaller

                    OnDemandInstaller().show_gpu_torch_installer()

                ui.button(t("config.gpu_enable"), icon="bolt", on_click=_enable_gpu).props(
                    "dense color=amber"
                ).classes("shrink-0").tooltip(t("config.gpu_enable_tooltip"))
