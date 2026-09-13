# -*- coding: utf-8 -*-
"""Context manager alias for resource governor."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Generator


@contextmanager
def acquire(workload: str) -> Generator[None, None, None]:
    from services.resource_governor import ResourceGovernor

    with ResourceGovernor.acquire(workload):
        yield


__all__ = ["acquire"]