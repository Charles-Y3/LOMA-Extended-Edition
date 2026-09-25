# LOMA Extended Edition — security audit against SECURITY_GUIDELINES.md (2026-09-25)

Scope: first-pass, read-only audit of the sinks where model output (or content a model read) can become an
action. Static review only — nothing was exploited or run. Not a full code review (see "Not covered").

## 1. Threat model
**What the model can see (untrusted inputs):** user chat text; uploaded files (PDF/Office/CSV/audio/images);
web pages and search results (Research, Web Viewer, News Brief, grounded chat); Knowledge Vault documents;
tool/extension results; other model output.
**What it can cause:** run Python (Software/Sandbox), run pandas snippets, generate/edit files, generate images,
fetch web pages, open local files/folders in the OS, install pip packages / download models (user-clicked),
render markdown/HTML in the UI.
**Data it can reach:** the user's files (vault roots, uploads, chats), the machine (the sandbox is not isolated).
No cloud LLM providers and no API keys (providers = Ollama / LM Studio, local) → no secrets to leak, and no
model→cloud egress by design. Egress that exists: web fetch/search tools and model/pip downloads.

## 2. Findings (highest risk first)

### F1 — CRITICAL: the whole UI is exposed on the local network, unauthenticated
`main.py:567` `ui.run(native=False, …)` without `host=`. NiceGUI then binds **0.0.0.0** (confirmed in its
`ui_run.py`: default `'0.0.0.0'` unless native). Anyone on the same Wi-Fi/LAN who can reach the port can drive
the app: run Python in the sandbox (F2), read/open files, change settings. macOS/Windows firewall prompts are
the only barrier, and users click Allow. **Fix: `host="127.0.0.1"`** (one line) + reject non-loopback Origins.
Violates: rules 2, 5 (T3), 9.

### F2 — HIGH: "Sandbox" is not a sandbox; model-written code runs with full user rights
`services/sandbox/interactive.py`, `runner.py` (`subprocess.Popen/run([sys.executable, …])`, and `_run_cmd`
with `shell=True`). Isolation is only a temp cwd + timeout: the script can read/write any user file, use the
network, read env vars. Runs are user-triggered ("Run" button, or `run_after_load`) — the model does not
auto-run it — but the code itself is model output. `_run_cmd(shell=True)` has no caller today (dead but
dangerous). **Guideline: T3 (arbitrary code) — never without confirmation + isolation.** (ASK: sandbox
strength — restricted-token process / macOS `sandbox-exec` profile / container; and confirm-before-run policy.)

### F3 — HIGH: `loma-open:` links let model/injected text launch any local path and disable sanitising
- `ui/components/chat_message.py:389,406`: if the text merely contains `loma-open:`/`loma-os-open`, markdown
  is rendered with `sanitize=False` for the **entire** message → model-controlled HTML/script in the UI
  (rule 12). The pattern is matched on the raw model text, not on links the app itself generated.
- `extensions/knowledge_vault/ui/open_path.py`: `os.startfile()` / `open` / `xdg-open` on any existing path from
  the link — no root allowlist, no canonicalisation, no file-type check. A `.bat/.exe/.command/.app` path (or a
  UNC path) executes. (Rules 3, 4, 5, 7.) **Fix:** app-generated links carry an opaque id resolved server-side
  to a path under the vault/data roots; never sanitize=False for model text; deny executable types.

### F4 — MEDIUM: web fetch has no SSRF / private-address protection
`services/web_fetch_policy.py:197 check_fetch_allowed` only checks scheme, per-domain rate limit and robots.txt.
No block for `127.0.0.1`, `localhost`, RFC1918, link-local `169.254.169.254`, `file:`-via-redirect, and no
re-check after redirects (Playwright follows them). URLs come from search results (untrusted) and the user;
a prompt-injected page can steer the fetcher at local services (Ollama on 11434, the LOMA UI itself → F1).
`extensions/web_viewer/extension.py:189` also interpolates the URL into an `<iframe src="…">` HTML string
without escaping. (Rules 4, 8.) This is the "lethal trifecta" risk: untrusted web content + private data
reachable in the same app + an outbound path.

### F5 — MEDIUM: model supply chain not pinned
No `revision=`/hash pinning anywhere; `use_safetensors` not enforced — diffusers logs show "Defaulting to unsafe
serialization" (pickle `.bin`) when a repo has no safetensors. Downloads use HTTPS from named HF repos (good),
but a compromised or unpinned repo, or a pickle-only repo, can execute code on load. pip installs
(`services/pip_runner.py`) take package names from the app's own lists (good) but are unpinned/no hashes.
(Rule 11.) **Fix:** pin commit hashes in `model_catalog.py`, pass `use_safetensors=True`, refuse pickle.

### F6 — MEDIUM: no security audit log, no risk-tier gate
There is no append-only log of proposals/decisions (rule 14) and no central policy gate: each feature makes its
own decisions (rules 1–3). Consequence: T2 actions (network fetch, opening files, pip/model downloads, running
code) are not uniformly confirmed or recorded.

### F7 — LOW/INFO
- Chat/vault text is passed to the model without a "this is untrusted data" wrapper convention (rule 7); some
  prompt building may already do this — not verified.
- Resource limits exist for the sandbox (timeouts 30/60 s) and web fetch (rate limit); no output-size cap on
  sandbox stdout capture in `interactive.py` (verify), no agent-loop depth cap verified (rule 10).
- Debug/diagnostic logs are off by default and written to the per-user folder (rule 15 ✔).

## 3. What is already good
- **Spreadsheet queries** (`services/spreadsheet_query/sandbox.py`): AST allowlist + timeout for LLM pandas
  snippets — the right pattern (rules 1, 2, 10). Verify the allowed method list excludes file-writing/reading
  methods (`to_csv`, `read_*`, `eval`, `query`).
- No cloud LLM providers/keys; local models only (rules 8, 13 largely N/A).
- Model runtime is Ollama (a separate process) — no direct file handles held by the model itself (rule 9 ✔ in part).
- Packaged app writes only to the per-user data folder; in-app self-update refuses when frozen (rules 11, 15 ✔).
- Markdown is sanitised by default (`sanitize=not allow_os_links`) — F3 is the exception path.
- Image safety gate (embedding classifier) is deterministic plain code on the prompt.

## 4. Rule-by-rule status
| # | Rule | Status |
|---|------|--------|
| 1 | Structured output only | Partial (JSON in some agents; free text → code in Software) |
| 2 | Default deny / allowlist | Missing centrally; present in spreadsheet sandbox |
| 3 | Narrow capabilities | Not met (`open_local_path(path)`, sandbox runs arbitrary code) |
| 4 | Canonicalise paths/URLs | Not met (F3, F4); sandbox file writer rejects `..` only |
| 5 | Risk tiers | Not implemented |
| 6 | Code-built confirmation dialogs | No confirmations for T2 actions found |
| 7 | Prompt injection assumed | Not verified; F3 removes sanitising on model text |
| 8 | Break the lethal trifecta | Partially (no cloud egress) but web fetch + local data coexist (F4) |
| 9 | Process separation | Model in Ollama process; executor/sandbox unrestricted (F2) |
| 10 | Resource limits | Partial |
| 11 | Model supply chain | Not met (F5) |
| 12 | Output handling | F3 exception; otherwise sanitised |
| 13 | Secrets | N/A today (no secrets) |
| 14 | Audit log | Missing (F6) |
| 15 | No writes in app folder | Met (verified by CI on Mac/Windows) |
| 16 | Adversarial CI tests | None yet |

## 5. Decisions needed from Charles (rules marked ASK)
1. **Sandbox strength for Software/Sandbox (F2):** restricted process (baseline) vs macOS sandbox profile /
   Windows AppContainer vs container. Stronger = harder to package on both OSes.
2. **Confirmation policy for T2 actions** (run code, open a file outside the vault, fetch a URL, install/download):
   every time, per-session grant, or per-folder grant.
3. **Bind host:** OK to bind 127.0.0.1 only? (Breaks anyone deliberately using LOMA from another device on the LAN.)
4. **Model pinning:** pin exact HF commits for the catalog (needs a periodic bump process).

## 6. Suggested order (smallest, highest value first)
1. F1 bind to 127.0.0.1 (+ Origin check) — one line + test.
2. F3 remove `sanitize=False`; resolve `loma-open` ids server-side under allowed roots; block executable types.
3. F4 add a URL canonicaliser/deny-list (private/loopback/link-local/metadata, re-check each redirect); escape iframe URL.
4. F5 pin revisions + `use_safetensors=True`.
5. F6/F2 policy gate + audit log + sandbox strength (depends on decisions above).
6. Rule 16: adversarial CI tests (each shown to FAIL on a build without the fix): LAN bind check, `loma-open` to an
   executable, `<script>` in model markdown, fetch of 127.0.0.1/169.254.169.254, path traversal, malformed JSON.

## 7. Not covered by this audit
Dynamic testing (nothing was run or exploited); every extension's own file/URL handling (Document Editor,
Formslator, Data Studio, Office mutation, Vault indexers/extractors — parsers of untrusted files such as PDF/Office
are a classic attack surface); prompt-building code (injection labelling); Playwright/browser download handling;
NiceGUI's own endpoints (upload/static routes) and WebSocket origin checks; dependency CVEs; the Core Edition.
