# v1.0.2 Mac user feedback — analysis + fixes applied (2026-09-28)

Local work only — nothing pushed to GitHub or any remote. Testing note: the Windows
side of these comparisons was the shipped `.exe` from the GitHub Release (not a dev
run). Charles confirmed both the dev test and the `.exe` test used right-click
(conversation mode) for the mic; dev worked, the `.exe` didn't, despite
`git diff v1.0.2 HEAD -- ui/components/voice_controls.py` being empty (identical
source) — see #5 below, this turned out to be a real bug in that file, not explained
by a gesture difference after all.

**Status: #1, #2, #3, #5 fixed and compiled clean. #4 fixed for the safe half only
(see its section) — needs a real Mac to confirm. #6 was misdiagnosed in the first pass
(see below) and is now understood but not fixed — needs a repro. #7 investigated,
no bug found. #8 still needs a repro.**

## 1. "Skip for now" missing on first-time Step 6 (image model) — FIXED
`ui/components/setup_wizard.py` — Skip previously only rendered on a rerun, when a
model was already installed, or when the catalog was empty (none true for a
first-time user; Step 5 renders Skip unconditionally, Step 6 had inverted the
condition). Now always renders, matching every other step. Also removed the
now-dead `already_installed` computation.

Re-prompting later when image generation is needed: **not implemented** — I did not
find an image-generation equivalent of voice's `offer_voice_reply_installer` hook
that re-prompts when a feature needing a model is used and none is installed. That's
a separate, larger feature, out of scope for this pass — flagging so it isn't assumed
done.

## 2. No download progress bar for the image model — FIXED
Same file, `_show_image_model_step`: the progress bar and status label were created
hidden (`set_visibility(False)`) and wired to a real `on_percent` callback, but the
download flow never called `set_visibility(True)` on them (unlike the voice-install
step, which does). Added the two `set_visibility(True)` calls in `_download()` right
before the download thread starts.

## 3. Drag-and-drop broken (works once then never again; totally broken for Sources) — FIXED
Root cause was two different bugs, both now fixed:
- **Sources** (`ui/components/attachments_hub.py`): the real, functioning `ui.upload`
  overlay had `.loma-sources-uploader-overlay { pointer-events: none !important; }`
  applied to it (`ui/themes/assets.py`) — meaning it could never receive a drop (or a
  click) at all; only the decorative div's synthetic `pickFiles()` call worked.
- **Local dropzone & Document Editor** (`ui/components/local_dropzone.py`,
  `extensions/document_editor/extension.py`): the real `ui.upload` was `.classes("hidden")`
  (`display:none`), and the visible decorative `<div>` on top had only a `click`
  handler — no `dragover`/`drop` listeners at all, so a browser drop could never reach
  a working target. Document Editor additionally had a **second**, undocumented
  `ui.upload` instance layered as a transparent absolute overlay that *could* accept a
  real drop — but nothing ever called `.reset()` on it after a file was cleared, so
  once its `max-files=1` cap was hit, every later drop/click on it was silently
  ignored — that's the precise "works once, then never again."

**Fix applied:** added `wire_native_drop(zone, upload)` in `local_dropzone.py` — real
`dragenter`/`dragover`/`dragleave`/`drop` listeners on the visible div (toggling a new
`.loma-dropzone-active` highlight class), and on drop, populates the hidden
`<input type=file>` via the `DataTransfer` API and fires a `change` event, which
Quasar's uploader picks up exactly like a normal file-picker selection. Reused from
`file_dropzone.py` (Sources) and `document_editor/extension.py`, which also had its
redundant second uploader removed (one real, hidden uploader now, wired for both
click and drop; the existing `.reset()` call on clear now correctly applies to it).
Highlight CSS added to `ui/themes/assets.py`.

**Second bug found and fixed during testing:** Charles reported dragging files
**one at a time** (not one multi-file drop) stopped working after the first file —
click could still add up to 5. Root cause: `render_sources_hub()`
(`ui/components/attachments_hub.py:104-106`) only renders the empty-state dropzone div
`if total_count == 0` — the moment one file is added, that div is replaced by the
file-card list and there's nothing left to drop onto for a second, separate drag
(click still works because the upload-icon button is outside that conditional). Fixed
by moving the `wire_native_drop()` call from the transient empty-state div to the
**persistent** wrapper `ui.element("div")` in `ui/layouts/nav_panel.py` that survives
`render_sources_hub.refresh()` regardless of whether Sources is empty or full — so the
whole Sources panel accepts a drop at any file count, not just when empty.

**Verified live** (dev server, browser automation, synthetic `DragEvent`s carrying
real `File` objects — the same DOM path a real OS drag/drop takes): dropped 5 files
one at a time into Sources, each landed correctly (`SOURCES (5)`), and a 6th drop
correctly triggered "Document context overflow! Max 5 files allowed." This is the one
piece of today's work that's actually been run and confirmed, not just compiled.

## 4. Mac image generation ~30 min vs <1 min on Windows, GPU <20% — PARTIALLY FIXED, needs a real Mac to confirm
`services/image_generation.py`'s `_load_pipeline()` and `place_edit_pipeline_on_device()`
(plus the same pattern duplicated in `services/image_edit.py` and
`services/image_inpaint.py`) branched only on `device == "cuda"`. Two separate things
were wrong for `mps`:
1. **dtype forced to fp32** (same as cpu). **Left as-is** — fp16 on Apple's `mps`
   backend has a real history of producing black/NaN output on some macOS/PyTorch
   versions, and `DESKTOP_APP_LESSONS.md` explicitly says to keep an fp32 fallback for
   Apple GPU. I have no Mac to verify fp16 is safe here, so I did not change it —
   changing precision blind is exactly the kind of guess that lesson warns against.
2. **Attention/VAE slicing forced on unconditionally** (the `else` branch, commented
   `# CPU: always minimize memory footprint`, which `mps` fell into even though
   Apple Silicon's unified memory doesn't need VRAM-style slicing). **Fixed** — `mps`
   is now grouped with `cuda` for this decision (never measures "VRAM" the way cuda
   does, so in practice it just skips forced slicing unless `CPU_OFFLOAD_MODE` is
   explicitly forced on). This has no correctness/precision risk, only a speed effect.

So Mac image generation should get faster from the slicing fix alone, but the
bigger lever (fp16) is intentionally not touched. **Recommend a real Mac test (or a
CI `macos-latest` run) to time Realistic Vision before/after, and only then decide
whether fp16-on-mps is worth risking.** Project memory
`project_mac_device_dispatch_debt` updated to reflect this split.

## 5. Chat-mode mic icon reverts to mic while LOMA is still replying — FIXED
`ui/components/voice_controls.py` had two mic code paths:
- One-shot mic (left-click): `_run_record()` reverted the icon to `mic`/default
  immediately after transcription, before the reply streamed — no "replying"
  indicator at all.
- Conversation mode (right-click, hands-free): `_conversation_loop()` already
  correctly showed `graphic_eq`/orange while waiting for the reply.

Charles confirmed both his dev test and the `.exe` test used right-click
(conversation mode) — so this was **not** explained by a one-shot-vs-conversation
gesture difference as I'd first guessed; it needs more thought as to why dev and the
`.exe` differed given identical source (see "Still open" below). While fixing #5's
one-shot path (which was a real, separate bug regardless), I also added a `replying`
guard so the mic button can't be clicked to start a new recording, or right-clicked
into conversation mode, while a one-shot reply is still in flight — it now waits on
`services.voice_reply.wait_for_turn_to_finish()`, the same helper conversation mode
uses, so both paths behave consistently.

**Still open:** why dev conversation-mode worked and the `.exe`'s didn't, given
byte-identical source. Possibilities not yet checked: a stale/cached frontend bundle
inside the packaged app (NiceGUI's static JS is bundled once at build time — if the
`.exe` was built before some *other* file changed shared state this code depends on,
the Python would match but compiled JS assets might not), or a timing/race in
`wait_for_turn_to_finish()`/generation tracking that only manifests under the
`.exe`'s different performance profile. Needs a repro on the actual `.exe` with the
in-app console/log open to see if an error or a generation-mismatch is logged.

"Sometimes does nothing" from the original report is still unexplained — needs a
repro (after an error? after switching extensions? after dismissing a mic permission
prompt?).

## 6. Mac: downloaded voice models but no audio; "connection lost" at "Preparing output" — RECLASSIFIED, not fixed
**Correction to my first pass:** "Preparing output…" is NOT a TTS/audio-synthesis
string — it's `progress.detail.synthesis`, the label for the pipeline's **Synthesis**
stage (`pipeline/progress_stages.py`), i.e. the stage right before the local LLM
generates the actual reply text. So the sequence is: mic input → Intent → Planner →
Execution → **Synthesis** (LLM call about to run) → "connection lost" on Mac. This is
a potential stall/crash in the **chat-reply LLM call itself** on Mac, not in Piper TTS
— the "no audio" symptom is likely just a consequence of the turn never completing,
so voice-reply never gets a finished message to speak.

I did not chase this further — it points into the local-LLM invocation path
(`pipeline/direct/express_runner.py` / `run_direct()`), a different and larger
subsystem than TTS, and I have no Mac logs to diagnose a stall/crash from. Needs a
repro with the app's log file captured at the moment of disconnect (or a Mac CI run
reproducing it) before attempting a fix — guessing at this one risks the exact
"two wrong guesses cost an hour" mistake `DESKTOP_APP_LESSONS.md` warns about.

## 7. Mac: missing image for History Events "COVID lockdown" scenario — INVESTIGATED, no bug found
Checked directly: `extensions/history_events/assets/encounters/covid_lockdown_2020.png`
exists in the repo, opens as a valid 768x432 RGB PNG (not corrupt, not a placeholder).
The PyInstaller spec (`packaging/loma_extended.spec:106`) bundles the entire
`extensions/` folder as one `datas` tree — no platform-specific filtering that would
exclude this one file, or images generally. `extensions/history_events/images.py`'s
`get_encounter_image_path()` resolves the path via `os.path.dirname(os.path.abspath(__file__))`,
which PyInstaller rewrites correctly for frozen modules on both platforms.

I could not find a code or packaging bug specific to this one encounter or to macOS.
My original hypothesis (image-safety gate blocking generation of this file at build
time) is very likely wrong since the file demonstrably exists and is bundled whole.
**Needs from Charles:** exact symptom (blank space where the image should be? a
broken-image icon? did other encounters' images work in the same session?) — without
that I'd be guessing at an invisible bug.

## 8. Doesn't work on Safari, fine on Chrome — STILL NOT DIAGNOSED, need a repro
Unchanged from the first pass — a webkit e2e test exists (`tests/e2e/mac_chat_e2e.py`)
so Safari was considered, but "doesn't work" is too broad to chase blind.
**Needs from Charles:** what exactly fails (blank page? chat hangs? mic/audio silent?
layout broken?).

---

## Files changed this pass
- `ui/components/setup_wizard.py` — #1, #2
- `ui/components/local_dropzone.py`, `ui/components/file_dropzone.py`,
  `ui/layouts/nav_panel.py`, `extensions/document_editor/extension.py`,
  `ui/themes/assets.py` — #3
- `services/image_generation.py`, `services/image_edit.py`, `services/image_inpaint.py` — #4 (slicing only)
- `ui/components/voice_controls.py` — #5

All touched Python files pass `python -m py_compile` and `scripts/verify_imports.py`.
**#3's Sources fix (both the drop mechanism and the one-at-a-time-up-to-5 fix) was run
live** in the dev server via browser automation and confirmed working (5 sequential
single-file drops all landed, a 6th correctly hit the cap). **#1, #2, #5 and #4 were
not run** — no unit or e2e test exercises the wizard's Step 6 buttons, the mic-icon
states, or the mps device-dispatch branch, so those are verified by compiling and
manual code review only. Recommend running the app for #1/#2/#5, and a real Mac or a
`macos-latest` CI run for #4, before calling those done.

## Confidence summary
- **Fixed and verified live in the running app:** #3.
- **Fixed, code-verified, compiles clean, not yet run:** #1, #2, #5.
- **Partially fixed (safe half only), needs real Mac hardware to verify or extend:** #4.
- **Investigated and reclassified/ruled out, not fixed — needs a repro:** #6, #7.
- **Not diagnosed — needs a repro:** #8.
