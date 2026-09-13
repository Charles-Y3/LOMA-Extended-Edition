# -*- coding: utf-8 -*-
"""Smoke test: language gate -> workspace shell (no settings.json)."""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SETTINGS = os.path.join(ROOT, "data", "settings.json")
MAIN = os.path.join(ROOT, "main.py")


def _load_main_module():
    spec = importlib.util.spec_from_file_location("loma_main", MAIN)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


async def _run() -> None:
    if os.path.isfile(SETTINGS):
        os.remove(SETTINGS)

    import httpx
    from nicegui import core
    from nicegui.testing.general import nicegui_reset_globals, prepare_simulation
    from nicegui.testing.user import User

    with nicegui_reset_globals():
        os.environ["LOMA_BROWSER_OPENED"] = "1"
        os.environ["NICEGUI_USER_SIMULATION"] = "true"
        prepare_simulation()
        _load_main_module()
        async with core.app.router.lifespan_context(core.app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(core.app), base_url="http://test"
            ) as client:
                user = User(client)
                await user.open("/")
                await user.should_see("Welcome to LOMA")
                await user.should_see("繁體中文")
                await user.should_see("简体中文")
                btn = user.find("Continue") or user.find("繼續") or user.find("继续")
                assert btn is not None, "Continue button not found"
                btn.click()
                await user.should_see("First-Time Setup", retries=120)
    print("STARTUP_SMOKE_OK")


def main() -> int:
    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
