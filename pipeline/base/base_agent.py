# -*- coding: utf-8 -*-
"""Template for LOMA agent workers."""
from __future__ import annotations

from abc import ABC, abstractmethod


class BaseAgent(ABC):
    """Agents participate in high-complexity orchestrated workflows."""

    role: str = ""

    @abstractmethod
    def execute(self, task_description: str, context, memory_text: str = "") -> str:
        """Run the agent task and return text output."""
