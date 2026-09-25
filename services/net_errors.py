# -*- coding: utf-8 -*-
"""Turn raw connectivity failures (DNS/connection/timeout tracebacks) into one clear,
translated "you're offline" message for download UIs."""
from __future__ import annotations

_OFFLINE_MARKERS = (
    "nameresolutionerror",
    "getaddrinfo failed",
    "failed to resolve",
    "name or service not known",
    "temporary failure in name resolution",
    "max retries exceeded",
    "connectionerror",
    "network is unreachable",
    "no route to host",
    "connection refused",
    "connection aborted",
    "connect timeout",
    "timed out",
    "nodename nor servname",
)


def is_offline_error(err: object) -> bool:
    text = str(err).lower()
    return any(m in text for m in _OFFLINE_MARKERS)


def friendly_net_error(err: object) -> str:
    """Translated "internet needed" message for connectivity failures; the original text otherwise."""
    if is_offline_error(err):
        from pipeline.i18n import t as tr

        return tr("assets.offline")
    return str(err)
