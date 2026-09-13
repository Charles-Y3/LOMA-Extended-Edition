# -*- coding: utf-8 -*-
"""Locate ffmpeg/ffprobe for LOMA (PATH, settings, bundled tools dir)."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_BUNDLED_DIR = _PROJECT_ROOT / "data" / "tools" / "ffmpeg"


def _settings_ffmpeg() -> str | None:
    try:
        from services.session import settings as session_settings

        path = (session_settings.load_settings().get("ffmpeg_path") or "").strip()
        if path and os.path.isfile(path):
            return os.path.abspath(path)
    except Exception:
        pass
    return None


def _bundled_candidates() -> list[Path]:
    names = ("ffmpeg.exe", "ffmpeg") if os.name == "nt" else ("ffmpeg",)
    out: list[Path] = []
    for root in (_BUNDLED_DIR, _PROJECT_ROOT / "tools" / "ffmpeg"):
        if not root.is_dir():
            continue
        for name in names:
            out.append(root / "bin" / name)
            out.append(root / name)
        try:
            for p in root.rglob("ffmpeg.exe" if os.name == "nt" else "ffmpeg"):
                if p.is_file():
                    out.append(p)
        except OSError:
            pass
    return out


def resolve_ffmpeg() -> str | None:
    cached = os.environ.get("LOMA_FFMPEG_PATH", "").strip()
    if cached and os.path.isfile(cached):
        return cached
    configured = _settings_ffmpeg()
    if configured:
        os.environ["LOMA_FFMPEG_PATH"] = configured
        return configured
    found = shutil.which("ffmpeg")
    if found:
        os.environ["LOMA_FFMPEG_PATH"] = found
        return found
    try:
        import imageio_ffmpeg

        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled and os.path.isfile(bundled):
            os.environ["LOMA_FFMPEG_PATH"] = bundled
            return bundled
    except Exception:
        pass
    for candidate in _bundled_candidates():
        if candidate.is_file():
            path = str(candidate.resolve())
            os.environ["LOMA_FFMPEG_PATH"] = path
            return path
    return None


def ffmpeg_available() -> bool:
    return resolve_ffmpeg() is not None


def subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    ff = resolve_ffmpeg()
    if ff:
        bin_dir = str(Path(ff).parent)
        env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
        env["LOMA_FFMPEG_PATH"] = ff
    return env


def run_ffmpeg(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    ff = resolve_ffmpeg()
    if not ff:
        raise FileNotFoundError("ffmpeg not found")
    cmd = [ff, *args]
    return subprocess.run(
        cmd,
        env=subprocess_env(),
        **kwargs,
    )
