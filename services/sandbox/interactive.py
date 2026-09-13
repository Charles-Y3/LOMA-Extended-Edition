# -*- coding: utf-8 -*-
"""Run Python scripts in a subprocess (stdout/stderr, optional stdin, stop)."""
from __future__ import annotations

import subprocess
import sys
import threading
from services.sandbox.paths import active_script_dir

DEFAULT_TIMEOUT = int(__import__("os").environ.get("LOMA_SANDBOX_RUN_TIMEOUT", "30"))

_lock = threading.Lock()
_active_proc: subprocess.Popen[str] | None = None


def is_sandbox_running() -> bool:
    with _lock:
        return _active_proc is not None and _active_proc.poll() is None


def stop_sandbox_run() -> bool:
    """Terminate the current sandbox subprocess if any. Returns True if one was stopped."""
    global _active_proc
    with _lock:
        proc = _active_proc
        _active_proc = None
    if proc is None:
        return False
    if proc.poll() is not None:
        return False
    try:
        proc.kill()
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass
    return True


def run_python_script(
    code: str,
    *,
    stdin_text: str = "",
    filename: str = "script.py",
    timeout_s: int = DEFAULT_TIMEOUT,
) -> dict:
    """Write code to temp sandbox dir, execute, return captured output."""
    stop_sandbox_run()

    script_dir = active_script_dir()
    script_path = (script_dir / filename).resolve()
    script_path.write_text(code or "", encoding="utf-8")

    global _active_proc
    try:
        with _lock:
            _active_proc = subprocess.Popen(
                [sys.executable, str(script_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(script_dir),
            )
            proc = _active_proc

        try:
            stdout, stderr = proc.communicate(input=stdin_text or "", timeout=timeout_s)
        except subprocess.TimeoutExpired:
            stop_sandbox_run()
            return {
                "status": "fail",
                "output": f"Execution timed out after {timeout_s}s (stopped).",
                "exit_code": None,
                "path": str(script_path),
                "stopped": True,
            }
        finally:
            with _lock:
                if _active_proc is proc:
                    _active_proc = None

        out = (stdout or "") + (("\n" + stderr) if stderr else "")
        status = "pass" if proc.returncode == 0 else "fail"
        return {
            "status": status,
            "output": out.strip() or f"(exit {proc.returncode})",
            "exit_code": proc.returncode,
            "path": str(script_path),
            "stopped": False,
        }
    except Exception as exc:
        with _lock:
            _active_proc = None
        return {
            "status": "error",
            "output": str(exc),
            "exit_code": None,
            "path": str(script_path),
            "stopped": False,
        }
