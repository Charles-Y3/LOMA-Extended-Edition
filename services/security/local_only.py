# -*- coding: utf-8 -*-
"""Only the local machine may talk to the LOMA UI server.

NiceGUI binds 0.0.0.0 by default (reachable from the whole LAN, unauthenticated, and it
can run sandbox code / open files). LOMA binds loopback and, as defence in depth against DNS
rebinding and cross-site requests from a page in the user's own browser, rejects any HTTP or
WebSocket request whose Host / Origin is not a loopback name.

Escape hatch for people who deliberately use LOMA from another device: LOMA_ALLOW_LAN=1.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


def lan_allowed() -> bool:
    return os.environ.get("LOMA_ALLOW_LAN", "").strip().lower() in ("1", "true", "yes")


def bind_host() -> str:
    return "0.0.0.0" if lan_allowed() else "127.0.0.1"


def _hostname(value: str) -> str:
    value = (value or "").strip().lower()
    if not value:
        return ""
    if "://" in value:
        return (urlsplit(value).hostname or "").lower()
    if value.startswith("["):  # [::1]:8765
        return value.split("]")[0] + "]"
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def is_loopback_host(value: str) -> bool:
    return _hostname(value) in LOOPBACK_NAMES


def request_allowed(host_header: str, origin_header: str) -> bool:
    """Pure decision used by the middleware (and unit-tested)."""
    if lan_allowed():
        return True
    if not is_loopback_host(host_header):
        return False
    if origin_header and origin_header.lower() != "null" and not is_loopback_host(origin_header):
        return False
    return True


class LocalOnlyMiddleware:
    """Pure ASGI middleware (covers http AND websocket scopes)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
            if not request_allowed(headers.get("host", ""), headers.get("origin", "")):
                if scope["type"] == "http":
                    await send({"type": "http.response.start", "status": 403,
                                "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
                    await send({"type": "http.response.body",
                                "body": b"LOMA only accepts requests from this computer."})
                else:
                    await send({"type": "websocket.close", "code": 1008})
                return
        await self.app(scope, receive, send)
