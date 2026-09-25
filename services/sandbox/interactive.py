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


MAX_OUTPUT_BYTES = int(__import__("os").environ.get("LOMA_SANDBOX_MAX_OUTPUT", str(20 * 1024 * 1024)))
_SHOWN_CHARS = 200_000


def _output_watchdog(proc, out_f, err_f) -> None:
    """Kill the script if it writes more than MAX_OUTPUT_BYTES (resource limit, guideline rule 10)."""
    import os
    import time

    while proc.poll() is None:
        try:
            if os.fstat(out_f.fileno()).st_size > MAX_OUTPUT_BYTES or os.fstat(err_f.fileno()).st_size > MAX_OUTPUT_BYTES:
                proc.kill()
                return
        except Exception:
            return
        time.sleep(0.25)


def _read_capped(f) -> str:
    try:
        f.flush()
        f.seek(0)
        data = f.read(_SHOWN_CHARS + 1)
        f.close()
    except Exception:
        return ""
    text = data.decode("utf-8", errors="replace")
    return text[:_SHOWN_CHARS] + (" … (output truncated)" if len(text) > _SHOWN_CHARS else "")


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
            # stdout/stderr go to capped files (not memory pipes): a runaway `print` loop is killed at
            # MAX_OUTPUT_BYTES instead of exhausting RAM.
            out_f = open(script_dir / ".stdout.txt", "w+b")
            err_f = open(script_dir / ".stderr.txt", "w+b")
            _active_proc = subprocess.Popen(
                [sys.executable, str(script_path)],
                stdin=subprocess.PIPE,
                stdout=out_f,
                stderr=err_f,
                text=True,
                cwd=str(script_dir),
            )
            proc = _active_proc
        threading.Thread(target=_output_watchdog, args=(proc, out_f, err_f), daemon=True).start()

        try:
            proc.communicate(input=stdin_text or "", timeout=timeout_s)
            stdout, stderr = _read_capped(out_f), _read_capped(err_f)
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
