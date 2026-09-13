# -*- coding: utf-8 -*-
"""Best-effort launch of Ollama when installed but not running."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

import config

logger = logging.getLogger(__name__)

_START_WAIT_S = 10.0
_POLL_INTERVAL_S = 0.45


def ollama_api_reachable(*, timeout: float = 1.5) -> bool:
    base = (config.OLLAMA_BASE_URL or "http://127.0.0.1:11434").rstrip("/")
    url = f"{base}/api/tags"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status < 500
    except Exception:
        return False


def _popen(cmd: list[str]) -> None:
    kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(cmd, **kwargs)


def _launch_commands() -> list[list[str]]:
    cmds: list[list[str]] = []
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            app = os.path.join(local, "Programs", "Ollama", "Ollama.exe")
            if os.path.isfile(app):
                cmds.append([app])
        cli = shutil.which("ollama")
        if cli:
            cmds.append([cli, "serve"])
    elif sys.platform == "darwin":
        cmds.append(["open", "-a", "Ollama"])
        cli = shutil.which("ollama")
        if cli:
            cmds.append([cli, "serve"])
    else:
        cli = shutil.which("ollama")
        if cli:
            cmds.append([cli, "serve"])
    return cmds


def try_start_ollama() -> tuple[bool, str]:
    """Start Ollama if installed; wait briefly for the API. Returns (ok, status)."""
    if ollama_api_reachable():
        return True, "running"

    cmds = _launch_commands()
    if not cmds:
        return False, "not_installed"

    for cmd in cmds:
        try:
            _popen(cmd)
            logger.info("Launched Ollama: %s", " ".join(cmd))
        except Exception as exc:
            logger.debug("Launch failed (%s): %s", cmd, exc)
            continue

        deadline = time.monotonic() + _START_WAIT_S
        while time.monotonic() < deadline:
            if ollama_api_reachable():
                return True, "started"
            time.sleep(_POLL_INTERVAL_S)

    return False, "start_timeout"
