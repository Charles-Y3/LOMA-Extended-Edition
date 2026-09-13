# -*- coding: utf-8 -*-
import re


def code_uses_stdin(code: str) -> bool:
    """True when the script likely calls input() for interactive stdin."""
    return bool(re.search(r"\binput\s*\(", code or ""))
