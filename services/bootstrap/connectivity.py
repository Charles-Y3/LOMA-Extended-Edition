# -*- coding: utf-8 -*-
"""Lightweight network connectivity probe for setup downloads."""
from __future__ import annotations

import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

import config

logger = logging.getLogger(__name__)


@dataclass
class ConnectivityResult:
    online: bool
    reason: str = ""


def check_connectivity(*, timeout: float = 3.0) -> ConnectivityResult:
    probe_url = getattr(config, "CONNECTIVITY_PROBE_URL", "https://1.1.1.1")
    try:
        req = urllib.request.Request(probe_url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status < 500:
                return ConnectivityResult(online=True, reason="probe_ok")
    except urllib.error.HTTPError as exc:
        if exc.code < 500:
            return ConnectivityResult(online=True, reason=f"http_{exc.code}")
    except Exception as exc:
        logger.info("Connectivity probe failed (%s): %s", probe_url, exc)

    fallback = "https://www.google.com/generate_204"
    try:
        req = urllib.request.Request(fallback, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout):
            return ConnectivityResult(online=True, reason="fallback_ok")
    except Exception as exc:
        logger.info("Connectivity fallback failed: %s", exc)
        return ConnectivityResult(online=False, reason=str(exc)[:120])
