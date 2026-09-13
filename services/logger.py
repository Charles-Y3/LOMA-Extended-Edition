# -*- coding: utf-8 -*-
"""Central logging helper (console + optional file)."""


def log(msg: str) -> None:
    from services.session import state

    state.add_log(msg)
