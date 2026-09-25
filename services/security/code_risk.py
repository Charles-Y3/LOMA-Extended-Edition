# -*- coding: utf-8 -*-
"""Plain-code (AST) risk scan for MODEL-WRITTEN Python before the user runs it.

Not a sandbox and not a proof of safety: it flags the obvious dangerous operations so LOMA can show
a short notice ONLY when a script does more than compute/print. Clean scripts run with no extra
step. Findings are keys (see ``security.risk.<key>`` in the i18n strings), never model text.
"""
from __future__ import annotations

import ast

NETWORK_MODULES = frozenset({
    "socket", "ssl", "requests", "urllib", "urllib3", "http", "httpx", "aiohttp", "ftplib", "smtplib",
    "poplib", "imaplib", "telnetlib", "xmlrpc", "websocket", "websockets", "paramiko", "pexpect",
    "webbrowser", "asyncssh",
})
COMMAND_MODULES = frozenset({"subprocess", "pty", "ctypes", "multiprocessing", "winreg"})
COMMAND_CALLS = frozenset({
    "system", "popen", "startfile", "execv", "execve", "execl", "execlp", "execvp", "spawnl", "spawnv",
    "spawnlp", "spawnvp", "posix_spawn", "fork", "kill", "killpg",
})
DELETE_CALLS = frozenset({
    "remove", "unlink", "rmdir", "removedirs", "rmtree", "rename", "renames", "replace", "truncate",
    "chmod", "chown", "move",
})
DYNAMIC_CALLS = frozenset({"eval", "exec", "compile", "__import__"})
DYNAMIC_MODULES = frozenset({"importlib", "pickle", "marshal", "shelve", "runpy"})
WRITE_MODES = ("w", "a", "x", "+")
WRITE_METHODS = frozenset({"write_text", "write_bytes"})


def _root_module(name: str) -> str:
    return (name or "").split(".", 1)[0]


def _is_safe_relative_const(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        v = node.value.replace("\\", "/")
        return bool(v) and not v.startswith(("/", "~")) and ".." not in v.split("/") and ":" not in v
    return False


def _call_name(node: ast.Call) -> tuple[str, str]:
    f = node.func
    if isinstance(f, ast.Name):
        return "", f.id
    if isinstance(f, ast.Attribute):
        base = f.value
        b = base.id if isinstance(base, ast.Name) else ""
        return b, f.attr
    return "", ""


def scan_code(code: str) -> list[str]:
    """Ordered, de-duplicated risk keys found in ``code`` (empty list = nothing flagged)."""
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return []
    found: list[str] = []

    def add(key: str) -> None:
        if key not in found:
            found.append(key)

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for n in names:
                root = _root_module(n)
                if root in NETWORK_MODULES:
                    add("network")
                if root in COMMAND_MODULES:
                    add("system_command")
                if root in DYNAMIC_MODULES:
                    add("dynamic_code")
        elif isinstance(node, ast.Call):
            base, name = _call_name(node)
            if name in DYNAMIC_CALLS and base == "":
                add("dynamic_code")
            if name in COMMAND_CALLS or base == "subprocess":
                add("system_command")
            if name in DELETE_CALLS and base in ("os", "shutil", "pathlib", "Path", "p", "path", "self", ""):
                # bare remove()/replace() etc. on str/list are common; only flag module/Path-style calls
                if base in ("os", "shutil", "pathlib", "Path") or name in ("rmtree", "unlink", "rmdir", "removedirs"):
                    add("delete_files")
            if name == "open" and base in ("", "io", "codecs"):
                mode = ""
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    mode = str(node.args[1].value)
                for kw in node.keywords:
                    if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                        mode = str(kw.value.value)
                if any(m in mode for m in WRITE_MODES):
                    if not node.args or not _is_safe_relative_const(node.args[0]):
                        add("write_outside")
            if name in WRITE_METHODS:
                add("write_outside")
            if name in ("copy", "copy2", "copyfile", "copytree") and base == "shutil":
                if len(node.args) < 2 or not _is_safe_relative_const(node.args[1]):
                    add("write_outside")
    return found
