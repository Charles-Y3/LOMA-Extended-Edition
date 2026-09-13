# LM Studio backend — known issues & future work

LOMA supports two local LLM backends via the `BaseProvider` abstraction
(`services/providers/base.py`, `ollama_provider.py`, `lmstudio_provider.py`,
selected/pinned through `services/providers/registry.py`). Ollama has a native API
LOMA can fully drive (pull, delete, dynamic per-request context sizing). LM Studio is
OpenAI-compatible only — no download/delete endpoint, and no way to change context
size per request; the context window is fixed by however the model was loaded in
LM Studio's own UI.

Because of that gap, the wizard/Settings/on-demand chat-model dialogs currently
promote Ollama as the default install path and only surface LM Studio when it's
already detected on the machine, with an upfront caveat
(`setup.provider.lmstudio_caveats`) — see `ui/components/setup_wizard.py`'s
`_render_provider_step`, `ui/components/capability_installer.py`'s
`_render_no_provider`/`_render_chat_tier_picker`, and
`ui/components/model_library_panel.py`'s provider banner.

## Fixed this pass

- **Vision/image input was silently dropped for LM Studio.** `LMStudioProvider.chat()`
  forwarded Ollama's `images` field verbatim into an OpenAI-compatible request body,
  which doesn't recognize that field — it was silently ignored, so the model answered
  with no image ever received (no error, just a plausible-sounding wrong answer).
  Fixed: `services/vision_input.py` now has `to_openai_content()`/
  `image_item_to_data_uri()`, which convert a message's `images` list (file paths,
  raw base64, or already-a-data-URI — LOMA's own pipelines mostly produce file paths
  via `prepare_vision_image_path`) into OpenAI's `content: [{"type":"text",...},
  {"type":"image_url","image_url":{"url":"data:..."}}]` shape.
  `LMStudioProvider.chat()`/`_to_openai_messages()` now applies this before sending.
  A non-image payload (see next item) is dropped rather than mis-sent.
- **`services/media_transcription.py`'s audio-via-images trick is Ollama-only and is
  now explicitly excluded**, not translated — it smuggles raw WAV bytes through the
  same `images` field for omni/audio-capable Ollama models. `image_item_to_data_uri()`
  magic-byte-sniffs the payload and returns `None` for anything that isn't a
  recognized image format (JPEG/PNG/GIF/WEBP), so this now safely falls back to a
  text-only message under LM Studio instead of being sent as a broken image.
- **`top_p` was silently dropped.** `LMStudioProvider.chat()` only read
  `temperature`/`num_predict` out of the `options` dict. Now also reads `top_p` if
  present — this incidentally fixes `services/formslator/translator.py`'s own
  `top_p=0.9` sampling setting too, since it builds its own options dict but goes
  through the same `chat()`.
- **`keep_alive` is now explicitly dropped** (was already effectively a no-op, now
  documented as intentional rather than an oversight — Ollama-only, no equivalent).
- **`model_supports_thinking()` always returned `False` for a non-Ollama backend**
  (no name-based fallback, unlike the vision/audio capability checks). Fixed by
  reusing the existing `_name_suggests_thinking()` heuristic (moved from
  `services/inference/ollama_chat.py` to `services/model_router.py`, next to
  `_name_looks_vision`) as the fallback when `ollama.show()` fails. Low real-world
  impact even before the fix — every extension call already passes
  `disable_thinking=True`, and LM Studio ignores the `think` kwarg regardless — but
  the capability-detection layer itself was wrong and other code could reasonably
  come to depend on it later.
- **Context-overflow errors are now translated into an actionable message**
  (`chat.lmstudio_context_exceeded`) instead of a raw HTTP 400. See "Known limitation"
  below for why this is a translation, not a real fix.
- **Wizard/Settings UX**: LM Studio no longer appears as a promoted "download this"
  option when no backend is detected yet (Ollama is default/recommended); it still
  appears normally once detected, alongside an upfront caveat message
  (`setup.provider.lmstudio_caveats`) so the user can make an informed choice rather
  than discovering the limitations later. Same caveat also shown persistently in
  Settings → Model Library when LM Studio is the active provider.

## Known limitation — not fixable via LOMA-side code

**`num_ctx` (context window) cannot be set per-request for LM Studio.** Ollama has to
reload a model whenever a call's `num_ctx` differs from what's currently loaded
(`pipeline/direct/batch_budget.py`'s `bump_run_ctx_floor`/`fit_budget_to_prompt`
dynamically widen `num_ctx` to fit a large prompt/expected output, and Ollama honors
that per-request). LM Studio's OpenAI-compatible API has no equivalent parameter —
context size is fixed by however the model was loaded in LM Studio's own UI. This
means:

- The per-model "Context Length" tuning control in Settings → Model Library
  (`services/inference/defaults.py`) has no effect under LM Studio.
- `fit_budget_to_prompt()`'s auto-widening — the mechanism that exists specifically to
  avoid context overflow on large documents (Document Intelligence, Formslator,
  presentation/report generation) — silently can't do anything for LM Studio. If the
  document is bigger than whatever context LM Studio's model was loaded with, the
  request will fail or get truncated server-side, and previously with no explanation
  surfaced to the user.
- This pass added a **best-effort mitigation**: `LMStudioProvider` catches an HTTP
  error whose body mentions context/length after a call where `num_ctx` had to be
  widened, and re-raises with `chat.lmstudio_context_exceeded` — a clear, translated
  "increase Context Length in {label} and reload" message — instead of a raw
  traceback. This is a **detection/messaging improvement, not a fix**: LOMA still
  cannot make the request succeed, it can only explain why it failed. A user running
  large-document workflows under LM Studio should be advised to load their model with
  a generously large context window from the start.
- No further code fix is possible here without either (a) LM Studio/llama.cpp adding
  a per-request context override to its API, or (b) LOMA querying the model's actual
  loaded context size ahead of time to warn before sending (not currently exposed by
  LM Studio's `/v1/models` response either, so also blocked).

## Lower-priority / deferred

- **Structured output / tool calling**: not used anywhere in the codebase today for
  either backend, so not a current gap — but if LOMA ever adds JSON-mode or
  function-calling, `LMStudioProvider` will need `response_format`/`tools` support
  added (OpenAI-standard fields, straightforward) — Ollama's `format: "json"` is a
  different parameter name and would need its own translation.
- **`services/formslator/translator.py`** bypasses the friendlier
  `extensions/ludicity_shared/llm.py` "no chat model" guard (calls
  `services.llm_bridge.chat` directly) — not a routing bug, just relies solely on the
  `build_chat_request()` backstop for its error message. Low priority; noted for
  consistency if that file is touched again.
- **Full MLX support** (`services/providers/base.py`'s `supports_remote_pull` pattern
  would extend cleanly to a generic OpenAI-compatible provider — llama.cpp-server,
  koboldcpp, or a custom endpoint — if ever added; LM Studio's own MLX engine option
  on Apple Silicon is already covered transparently since it's the same
  `/v1/chat/completions` surface).
