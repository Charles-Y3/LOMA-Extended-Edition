# -*- coding: utf-8 -*-
"""First-run setup orchestrator — delegates to SetupWizard UI."""
from __future__ import annotations


class SetupBootstrap:
    @staticmethod
    def maybe_run() -> None:
        from ui.components.setup_wizard import SetupWizard

        SetupWizard.maybe_run()
