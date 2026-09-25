# -*- coding: utf-8 -*-
"""Block fetches that would reach this computer or private networks (SSRF).

URLs come from search results, web pages and models -- all untrusted. A fetch must only reach
PUBLIC internet hosts: never loopback (Ollama, the LOMA UI), RFC1918/private ranges, link-local
(cloud metadata 169.254.169.254), or non-http(s) schemes. Applied to the first URL and to every
redirect/request the headless browser makes.

Escape hatch for people who deliberately browse intranet pages: LOMA_ALLOW_PRIVATE_FETCH=1.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import time
from urllib.parse import urlsplit

_cache: dict[str, tuple[float, bool]] = {}
_TTL = 60.0


def private_fetch_allowed() -> bool:
    return os.environ.get("LOMA_ALLOW_PRIVATE_FETCH", "").strip().lower() in ("1", "true", "yes")


def _ip_is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    return not (
        addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
        or addr.is_reserved or addr.is_unspecified
    ) and addr.is_global


def host_is_public(host: str) -> bool:
    host = (host or "").strip().strip("[]").lower().rstrip(".")
    if not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return False
    try:
        ipaddress.ip_address(host)
        return _ip_is_public(host)
    except ValueError:
        pass
    now = time.time()
    hit = _cache.get(host)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    try:
        infos = socket.getaddrinfo(host, None)
        addrs = {i[4][0] for i in infos}
        ok = bool(addrs) and all(_ip_is_public(a) for a in addrs)
    except Exception:
        ok = False
    _cache[host] = (now, ok)
    return ok


def check_public_url(url: str) -> tuple[bool, str]:
    """(allowed, reason)."""
    parts = urlsplit((url or "").strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False, "Only http(s) URLs can be fetched."
    if private_fetch_allowed():
        return True, ""
    if not host_is_public(parts.hostname):
        return False, "This address is on your own computer or a private network, so LOMA won't fetch it."
    return True, ""


def install_route_guard(context) -> None:
    """Playwright (sync) browser context: abort every request -- including redirects and
    sub-resources -- that would reach a non-public host."""

    def _handler(route) -> None:
        url = route.request.url
        if url.split(":", 1)[0].lower() in ("data", "blob", "about"):
            route.continue_()
            return
        ok, _reason = check_public_url(url)
        if ok:
            route.continue_()
        else:
            route.abort()

    context.route("**/*", _handler)
