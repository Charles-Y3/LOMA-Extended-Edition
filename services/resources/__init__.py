# -*- coding: utf-8 -*-
# -*- coding: utf-8 -*-
"""Shim package — prefer services.resource_governor."""
from services.resources.context import acquire

__all__ = ["acquire"]


def __getattr__(name: str):
    if name == "ResourceGovernor":
        from services.resource_governor import ResourceGovernor
        return ResourceGovernor
    raise AttributeError(name)