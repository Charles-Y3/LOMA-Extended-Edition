# -*- coding: utf-8 -*-
"""Run `python -m <module> <args>`-style tools (pip, playwright) in-process.

Packaged .exe builds have no standalone python.exe next to them — `sys.executable` IS
the app's own windowed exe, so `subprocess.Popen([sys.executable, "-m", "pip", ...])`
can never work there (this was the cause of on-demand installers failing with a cryptic
non-zero exit code in the packaged build, while working fine under `python main.py`
where sys.executable is a real interpreter). Running the module in-process via
``runpy`` — the same mechanism CPython itself uses for `-m` — sidesteps the problem
entirely and behaves identically whether frozen or not, which is the point: dev and
packaged builds must do the same thing.
"""
from __future__ import annotations

import contextlib
import glob
import importlib
import os
import runpy
import sys
import sysconfig
import threading
from typing import Callable

_LOCK = threading.Lock()


class _LineForwardingStream:
    """Minimal writable stream that splits writes on newlines and forwards each
    completed line to ``on_line`` — lets callers show live install progress the same
    way they already parse subprocess stdout line-by-line."""

    # pip's StreamWrapper (pip/_internal/utils/misc.py) reads `.encoding` off whatever
    # sys.stdout/stderr currently is; without it the install dies with
    # "AttributeError: ... has no attribute 'encoding'" before doing anything.
    encoding = "utf-8"
    errors = "replace"

    def __init__(self, on_line: Callable[[str], None] | None):
        self._on_line = on_line
        self._buf = ""
        self.text = ""

    def write(self, chunk: str) -> int:
        if not chunk:
            return 0
        self.text += chunk
        self._buf += chunk
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if self._on_line and line.strip():
                try:
                    self._on_line(line.strip())
                except Exception:
                    pass
        return len(chunk)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


def run_module(
    module_name: str,
    argv: list[str],
    *,
    on_line: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    """Run ``python -m <module_name> <argv>`` in-process. Returns (ok, tail_output)."""
    with _LOCK:
        old_argv = sys.argv
        sys.argv = [module_name, *argv]
        stream = _LineForwardingStream(on_line)
        try:
            with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
                try:
                    runpy.run_module(module_name, run_name="__main__", alter_sys=True)
                    return True, stream.text[-4000:]
                except SystemExit as exc:
                    code = exc.code
                    ok = code is None or code == 0
                    return ok, stream.text[-4000:]
        except Exception as exc:
            return False, f"{stream.text[-3000:]}\n{exc}"
        finally:
            sys.argv = old_argv


# pip >=24.2 always builds its SSL context via pip._vendor.truststore, which reaches
# into the OS certificate store through ctypes/OS APIs that don't survive being frozen
# and run in-process via runpy inside a PyInstaller bundle (surfaces as an AttributeError
# on `_session` deep in truststore's SSLContext). `--use-deprecated=legacy-certs` is
# pip's own escape hatch back to the old certifi-based SSL context, which works fine here.
_LEGACY_CERTS_ARGS = ["--use-deprecated=legacy-certs"]

# When a dependency has no prebuilt wheel for this exact platform/Python combo, pip's
# default "isolated build" creates a temporary build environment by spawning
# `subprocess.Popen([sys.executable, ...])` — same class of bug as the module docstring's
# `-m pip` case, just one layer deeper: `sys.executable` in this frozen build IS the app's
# own windowed exe, so that "subprocess" is actually a second full LOMA instance launching,
# which immediately tries to bind its own web server to the port the first instance already
# holds and crashes with "only one usage of each socket address is normally permitted" —
# surfaced by pip as an opaque "This error originates from a subprocess" failure. Confirmed
# account-dependent: it only reproduced on a Windows account whose pip cache had never
# built that dependency before (a warm cache, like the one built up on this machine from
# repeated testing, skips the build entirely by reusing pip's own cached wheel from a prior
# build) — a fresh account/PC always has an empty cache and hits this every time.
# `--no-build-isolation` skips creating that temporary environment, building instead with
# whatever's already importable in the current process — no subprocess, no self-relaunch.
# Requires setuptools/wheel to already be present, which they are (bundled alongside pip
# itself; see the `_pip_dir` bundling note in packaging/loma_core.spec).
_NO_BUILD_ISOLATION_ARGS = ["--no-build-isolation"]


def _fixup_site_packages_path() -> None:
    """A frozen PyInstaller build never runs normal CPython site-packages
    auto-discovery (there's no `site.py` scan of `sys.prefix/Lib/site-packages` at
    startup), so a package pip just installed there is invisible to `import` even
    though it's genuinely, completely on disk. Confirmed via isolated repro: pip
    installs cleanly either way, but the frozen process's import system had already
    cached "not found" for that name and never had the install dir on `sys.path` to
    begin with — surfacing as either ModuleNotFoundError, or (once something else's
    import machinery happens to touch the path as a side effect, e.g. funasr's
    heavier post-install step) a *worse* half-imported module whose __file__ points
    nowhere real, e.g. FileNotFoundError reading a data file that demonstrably exists
    on disk right next to it. Both symptoms are the same missing-path-plus-stale-cache
    bug; this is the fix for both, and it's a no-op if the path is already present."""
    for key in ("purelib", "platlib"):
        path = sysconfig.get_paths().get(key)
        if path and path not in sys.path:
            sys.path.insert(0, path)
    importlib.invalidate_caches()


def user_packages_prefix() -> str | None:
    """Writable install prefix for on-demand pip installs — packaged builds only.

    pip's default target is the running interpreter's own site-packages, which in a frozen
    build is INSIDE the app folder. That folder is read-only when a browser-downloaded macOS
    app runs under App Translocation ("[Errno 30] Read-only file system"), is code-signed
    (writing into it invalidates the signature), and is lost on every update. A prefix under
    the per-user data folder is writable wherever the app itself sits. `--prefix` (unlike
    `--target`) still lets pip see packages already bundled, so it doesn't re-download them.
    Dev runs (a normal venv) return None and keep pip's default behaviour."""
    from services.platform_paths import is_frozen, writable_root

    if not is_frozen():
        return None
    return os.path.join(writable_root(), "python-packages")


def _user_site_dirs(prefix: str) -> list[str]:
    patterns = (
        os.path.join(prefix, "lib", "python*", "site-packages"),
        os.path.join(prefix, "Lib", "site-packages"),
        os.path.join(prefix, "lib", "site-packages"),
    )
    found: dict[str, str] = {}
    for pat in patterns:
        for p in glob.glob(pat):
            if os.path.isdir(p):
                found.setdefault(os.path.normcase(os.path.abspath(p)), p)  # "Lib" == "lib" on Windows
    return list(found.values())


def register_user_packages() -> None:
    """Put packages installed by earlier runs (see user_packages_prefix) on sys.path.
    Call at startup and after every install; a no-op outside packaged builds."""
    prefix = user_packages_prefix()
    if not prefix:
        return
    for path in _user_site_dirs(prefix):
        if path not in sys.path:
            sys.path.insert(0, path)
    importlib.invalidate_caches()


def _prefix_args() -> list[str]:
    prefix = user_packages_prefix()
    if not prefix:
        return []
    os.makedirs(prefix, exist_ok=True)
    return ["--prefix", prefix, "--no-warn-script-location"]


def pip_install(
    packages: list[str],
    *,
    extra_args: list[str] | None = None,
    on_line: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    args = [
        "install",
        *_LEGACY_CERTS_ARGS,
        *_NO_BUILD_ISOLATION_ARGS,
        *_prefix_args(),
        *(extra_args or []),
        *packages,
    ]
    ok, output = run_module("pip", args, on_line=on_line)
    if ok:
        register_user_packages()
        _fixup_site_packages_path()
    return ok, output


def pip_install_requirements(
    req_path: str,
    *,
    on_line: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    ok, output = run_module(
        "pip",
        ["install", *_LEGACY_CERTS_ARGS, *_NO_BUILD_ISOLATION_ARGS, *_prefix_args(), "-r", req_path],
        on_line=on_line,
    )
    if ok:
        register_user_packages()
    return ok, output


def pip_uninstall(
    packages: list[str],
    *,
    on_line: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    return run_module("pip", ["uninstall", "-y", *packages], on_line=on_line)
