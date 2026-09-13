"""Validation result models."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)

    @property
    def repair_hint(self) -> str:
        if self.valid or not self.errors:
            return ""
        return "Fix these issues: " + "; ".join(self.errors[:3])

