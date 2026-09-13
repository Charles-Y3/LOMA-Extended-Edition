# -*- coding: utf-8 -*-
"""Isolated sandbox: compile, python -c smoke, optional pytest."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from services.sandbox.paths import sandbox_root

SANDBOX_ROOT = sandbox_root()
DEFAULT_TIMEOUT = int(os.environ.get("LOMA_SANDBOX_TIMEOUT", "60"))


@dataclass
class SandboxRequest:
    files: dict[str, str]
    commands: list[str] = field(default_factory=list)
    timeout_s: int = DEFAULT_TIMEOUT


@dataclass
class SandboxResult:
    status: Literal["pass", "fail", "error"]
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    observations: list[str] = field(default_factory=list)


def run_sandbox(code: str, *, entry_file: str = "artifact.py") -> dict:
    """Backward-compatible API for single-module code checks."""
    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    result = run_sandbox_workspace(SandboxRequest(files={entry_file: code}))
    return {
        "status": "pass" if result.status == "pass" else "fail",
        "output": (result.stdout + "\n" + result.stderr).strip() or "; ".join(result.observations),
        "observations": result.observations,
        "exit_code": result.exit_code,
    }


def run_sandbox_workspace(request: SandboxRequest) -> SandboxResult:
    mission_id = uuid.uuid4().hex[:12]
    workspace = (SANDBOX_ROOT / mission_id).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    observations: list[str] = []

    try:
        for rel, content in (request.files or {}).items():
            rel_path = Path(rel)
            if rel_path.is_absolute() or ".." in rel_path.parts:
                return SandboxResult(
                    status="error",
                    observations=["Invalid sandbox path"],
                )
            target = (workspace / rel_path).resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content or "", encoding="utf-8")

        py_files = sorted(workspace.rglob("*.py"))
        for py_file in py_files:
            ok, obs = _py_compile(py_file, request.timeout_s)
            observations.extend(obs)
            if not ok:
                return SandboxResult(status="fail", observations=observations)

        for py_file in py_files:
            ok, obs = _python_c_smoke(py_file, request.timeout_s)
            observations.extend(obs)
            if not ok:
                return SandboxResult(status="fail", observations=observations)

        if _pytest_available():
            tests_dir = workspace / "tests"
            if tests_dir.is_dir() and any(tests_dir.glob("test_*.py")):
                ok, obs = _run_pytest(workspace, request.timeout_s)
                observations.extend(obs)
                if not ok:
                    return SandboxResult(status="fail", observations=observations)

        for cmd in request.commands or []:
            ok, obs, code = _run_cmd(cmd, workspace, request.timeout_s)
            observations.extend(obs)
            if not ok:
                return SandboxResult(status="fail", observations=observations, exit_code=code)

        return SandboxResult(status="pass", observations=observations or ["sandbox checks passed"])
    except Exception as exc:
        return SandboxResult(status="error", observations=[f"sandbox error: {exc}"])
    finally:
        try:
            if os.environ.get("LOMA_SANDBOX_KEEP") != "1":
                shutil.rmtree(workspace, ignore_errors=True)
        except Exception:
            pass


def _py_compile(py_file: Path, timeout: int) -> tuple[bool, list[str]]:
    path = py_file.resolve()
    proc = subprocess.run(
        [sys.executable, "-m", "py_compile", str(path)],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(path.parent),
    )
    if proc.returncode == 0:
        return True, [f"py_compile ok: {py_file.name}"]
    return False, [f"py_compile failed: {py_file.name}", proc.stderr or proc.stdout or ""]


def _python_c_smoke(py_file: Path, timeout: int) -> tuple[bool, list[str]]:
    path = py_file.resolve()
    snippet = (
        "import ast, pathlib\n"
        f"p = pathlib.Path({str(path)!r})\n"
        "ast.parse(p.read_text(encoding='utf-8'))\n"
        "print('ast_ok')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(path.parent),
    )
    if proc.returncode == 0:
        return True, [f"python -c smoke ok: {py_file.name}"]
    return False, [f"python -c smoke failed: {py_file.name}", proc.stderr or proc.stdout or ""]


def _pytest_available() -> bool:
    try:
        import pytest  # noqa: F401

        return True
    except ImportError:
        return False


def _run_pytest(workspace: Path, timeout: int) -> tuple[bool, list[str]]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests"],
        capture_output=True,
        text=True,
        timeout=max(timeout, 120),
        cwd=str(workspace),
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0:
        return True, ["pytest passed", out[:2000]]
    return False, ["pytest failed", out[:2000]]


def _run_cmd(cmd: str, workspace: Path, timeout: int) -> tuple[bool, list[str], int | None]:
    proc = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(workspace),
    )
    obs = [f"cmd: {cmd}", (proc.stdout or "")[:1500], (proc.stderr or "")[:1500]]
    return proc.returncode == 0, obs, proc.returncode
