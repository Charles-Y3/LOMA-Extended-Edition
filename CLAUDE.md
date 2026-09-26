PROJECT RULES (TOKEN EFFICIENCY MODE)

You are working in a large codebase. Optimize for minimal token usage.

## 0. template
- must always use template setup for new service and extension!
- direct pipeline: pipeline/direct/entry.py + step_executor; plan: pipeline/agentic/runner.py
- new extension: EXTENSION_TEMPLATE.md — extension.py + __init__.py + extension_catalog.json + BUILTIN_EXTENSION_IDS in catalog.py.
- new service: SERVICE_TEMPLATE.md
- update relevant registry; run scripts/verify_imports.py

## 1. Context control
- Only open files explicitly mentioned in the request.
- Never scan entire folders or “explore architecture”.
- Do not re-read files unless they are being actively modified.

## 2. Execution style
- Prefer direct code edits over explanations.
- Do not repeat or restate existing code.
- Avoid multi-pass reasoning unless the task fails.

## 3. Agent behavior
- Make the smallest correct change.
- Do not refactor unrelated code.
- Do not “improve structure” unless explicitly requested.

## 4. Task handling
- If a task is large, break it into steps and ask for confirmation before continuing.
- Execute only ONE step per action cycle.

## 5. Anti-loop rules
- Do not re-verify unchanged code.
- Do not re-run analysis of files already read.
- Avoid iterative “fix → re-check → fix” cycles unless errors are shown.

## 6. Safety constraint
- If unclear, ask a question instead of exploring codebase.

## 7. Localization (i18n)
- All user-visible strings MUST use `from pipeline.i18n import t as tr` and `tr("key")`.
- Add keys for en, zh_tw, and zh_cn in `pipeline/i18n.py` or `pipeline/i18n_extensions.py`.
- No hardcoded English (or other language) in UI labels, buttons, notifications, dialogs, or placeholders.
- Also applies to chat messages, generated deck/document text (slide titles, filler, headings) and chart titles/captions.
- `tests/test_no_hardcoded_ui_strings.py` fails on any English literal passed to a user-facing call (notify/label/set_assistant_content...). Log lines (log/sink.log) are diagnostics and may stay English.
- Deck titles: recognise standard slides (agenda, closing) via `pipeline/deck_i18n.py`, never by comparing to English words.

## 8. Query intent classification (i18n)
- Any check that inspects the user's own typed query/instruction to make a
  routing/intent decision (task verb, deliverable format, edit type, color, live-data
  need, etc.) MUST go through `pipeline/query_intent_i18n.py`'s `CONCEPTS`/`matches()`
  — never a new hardcoded English-only tuple or regex against user text.
- Add new concepts there, with phrases for all `SUPPORTED_LOCALES` in `pipeline/i18n.py`
  (currently en, zh_tw, zh_cn, es, de), not just English.
- This does NOT apply to regexes that parse LLM-generated output or our own fixed
  markdown conventions (e.g. `[IMAGE_PROMPT:...]`, `--- Slide N ---`) — those are
  controlled by our own system prompts and stay English/fixed-format by design.

GOAL:
Minimize file reads, minimize context expansion, maximize direct edits.