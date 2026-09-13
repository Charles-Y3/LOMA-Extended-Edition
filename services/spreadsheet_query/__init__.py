# -*- coding: utf-8 -*-
"""Answer arbitrary natural-language questions over uploaded tabular data by
generating and sandbox-executing a pandas snippet, instead of having the LLM
eyeball a text dump of the sheet."""
from __future__ import annotations

from services.spreadsheet_query.query import answer_spreadsheet_question

__all__ = ["answer_spreadsheet_question"]
