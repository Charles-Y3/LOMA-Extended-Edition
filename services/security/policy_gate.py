# -*- coding: utf-8 -*-
"""The single deterministic choke point where MODEL-influenced requests become actions.

    model proposes  ->  decide(kind, **args)  ->  allowed / refused (+ needs_confirmation)  ->  executor

Default deny: a kind that is not registered below is refused. Each kind has a plain-code validator (no AI)
and a risk tier (guideline rule 5): T0 read-only, T1 writes in the app's own data folder (auto), T2 leaves the
sandbox / network / runs code (needs an explicit user action), T3 never. Every decision is audited.

Legitimate features are not blocked: validators only refuse what is outside the app's own offered paths,
public internet hosts, safe model formats. Add a new action kind here (and a test) before wiring a new sink.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from services.security import audit


@dataclass
class Decision:
    allowed: bool
    tier: str = "T0"
    reason: str = ""
    needs_confirmation: bool = False
    findings: list[str] = field(default_factory=list)


def _open_path(path: str = "", **_: object) -> Decision:
    from services.security.path_guard import check_openable

    ok, reason = check_openable(path)
    return Decision(ok, "T2" if ok else "T3", reason)


def _fetch_url(url: str = "", **_: object) -> Decision:
    from services.security.url_guard import check_public_url

    ok, reason = check_public_url(url)
    return Decision(ok, "T2", reason)


def _run_code(code: str = "", **_: object) -> Decision:
    from services.security.code_risk import scan_code

    findings = scan_code(code)
    # Running is always a deliberate user click; risky scripts additionally show the notice first.
    return Decision(True, "T2" if findings else "T1", "", needs_confirmation=bool(findings), findings=findings)


def _download_model(repo_id: str = "", **_: object) -> Decision:
    from services.security.model_files import check_repo

    ok, reason = check_repo(repo_id)
    return Decision(ok, "T2", reason)


_ALLOWLIST: dict[str, Callable[..., Decision]] = {
    "open_path": _open_path,
    "fetch_url": _fetch_url,
    "run_code": _run_code,
    "download_model": _download_model,
}


def decide(kind: str, **args: object) -> Decision:
    validator = _ALLOWLIST.get(kind)
    if validator is None:
        decision = Decision(False, "T3", f"action '{kind}' is not on the allowlist")
    else:
        try:
            decision = validator(**args)
        except Exception as exc:  # a validator bug must fail closed
            decision = Decision(False, "T3", f"validator error: {type(exc).__name__}")
    summary = {k: v for k, v in args.items() if k != "code"}
    if "code" in args:
        summary["code_chars"] = len(str(args["code"]))
    outcome = "allowed" if decision.allowed else "refused"
    if decision.needs_confirmation:
        outcome = "needs_confirmation"
    audit.record(kind, outcome, decision.reason or ",".join(decision.findings), tier=decision.tier, **summary)
    return decision
