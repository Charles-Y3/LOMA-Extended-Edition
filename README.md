# LOMA Extended Edition — Local Orchestrated Multimodal Automation

**Version 1.0.2** — available for Windows and macOS (Apple Silicon).

Privacy-first local agentic workstation. Extended Edition builds on Core Edition with
guided web research, a live news brief, and a general-purpose web viewer, so LOMA can ground
answers in the open web when you choose to — while your files and models stay local by
default. See [EDITIONS.md](EDITIONS.md) for the full edition comparison.

LOMA is not a simple chat app. It is a modular offline AI workspace combining multimodal
file understanding, service-first routing, plugin-based extensibility, local LLM inference,
interactive workspace tooling, and persistent context and artifacts — all running on your
own machine.

**Core principles**

- Local-first execution (cloud APIs optional, never required)
- Nothing leaves your machine unless you explicitly enable web grounding or an online
  provider

---

## What LOMA Extended Edition can do

- **Chat and quick Q&A** — LOMA decides on its own whether to reply, draft a deliverable, or
  edit a file you've attached.
- **Generate documents and images** from plain-language requests.
- **Understand your files** — read documents, PDFs, presentations, spreadsheets, images,
  video frames, and audio as context.
- **Run models locally** through [Ollama](https://ollama.com) or another provider, with
  optional web grounding for chat (off by default).
- **Eight bundled extensions**, switched on or off from the Extension Library:
  - **Research** — guided, multi-step research: answer LOMA's clarifying questions, let it
    search the web and your files, and export a cited report.
  - **News Brief** — filter by category, region, and timeline; LOMA searches, reads, and
    summarizes articles into a brief with numbered references.
  - **Web Viewer** — open a page by URL, highlight text, then ask about it, summarize it, or
    extract key points. Respects robots.txt, rate-limits, and caches pages.
  - **Document Editor** — view, edit, export, and highlight Office documents and PDFs in
    place (editing works on .docx).
  - **Knowledge Vault** — search, ask, and analyze across folders and document collections
    (PDF, DOCX, DOC, TXT), with hybrid keyword + semantic search and bilingual tables
    recognised as translation pairs.
  - **Chat Archive** — save conversations, organize them into folders, and continue later.
  - **Token Usage** — track token usage and estimated cost per model and per day.
  - **History Events** — pick an era, answer an encounter from the setup alone, and get
    graded A–F against what actually happened.
- **Image generation models** — choose a checkpoint (Realistic Vision, DreamShaper, SDXL
  Lightning, or Flux2), with quality and shape (square, portrait, landscape) presets.
- **Multilingual UI** and voice-reply options, configurable from Settings.

**Editions at a glance:** *Core* — simplified UI, offline only, four extensions.
*Extended* (this one) — adds Research, Web Viewer, and News Brief. *Complete* — full
multi-panel UI, every extension, and planner mode for multi-step tasks.

A full walkthrough ships with the app as `LOMA_User_Guide_EN.docx`.

---

## Getting started

### Option A — Download the app (easiest, no Python needed)

Download the latest build from this repository's
[**Releases**](https://github.com/Charles-Y3/LOMA-Extended-Edition/releases) page. Download
the `.zip` straight from there and unzip it once — don't re-zip or re-upload it elsewhere
(large re-hosted copies can get corrupted in transit).

| Platform | File | Notes |
|---|---|---|
| Windows 10/11 (64-bit) | `LOMA-Extended-Edition-Windows.zip` | Unzip anywhere, run `LOMA Extended Edition.exe` inside the folder |
| macOS | `LOMA-Extended-Edition-macOS.zip` | **Apple Silicon (M1/M2/M3/M4) only** — unzip and open `LOMA Extended Edition.app`; see below for Intel Macs |

Both downloads are about 1 GB (they bundle the local AI stack and the embedding model).

**Installing on macOS (recommended):** paste this one line into Terminal. It downloads the
latest release straight into `~/Applications` and opens it — no Gatekeeper prompt and no
extra steps, because files fetched this way are never quarantined by macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/Charles-Y3/LOMA-Extended-Edition/main/packaging/install_macos.sh | bash
```

**Installing from the downloaded zip instead:** the app isn't notarized by Apple (that
needs a paid developer certificate), so macOS blocks a browser-downloaded copy the first
time you open it. Allow it once — no Terminal needed:

0. Drag `LOMA Extended Edition.app` out of Downloads into **Applications** (or any normal
   folder) first. LOMA works even when opened straight from Downloads, but macOS runs an
   app opened from there from a temporary read-only copy, so moving it first is the
   cleanest way to avoid surprises.
1. Double-click `LOMA Extended Edition.app`. macOS says it can't verify the app — click
   **Done** (not "Move to Trash").
2. Open **System Settings → Privacy & Security**, scroll down to the message about
   "LOMA Extended Edition" being blocked, and click **Open Anyway**. Confirm with your
   password if asked.
3. Open the app again and click **Open**. It opens normally from then on.

(On older macOS versions, right-click / Control-click the app → **Open** → **Open** does the
same thing.) Terminal alternative: `xattr -cr "/path/to/LOMA Extended Edition.app"`, then
open it. If macOS says the app is "damaged and can't be opened", that's the same block —
use one of these methods, or the one-line install above.

**Intel Mac:** the packaged build is Apple Silicon only. Use Option B (run from source)
instead.

### Option B — Run from source

Requires [Python 3.10–3.12](https://www.python.org/downloads/) installed first (the
launcher scripts below check this for you and tell you clearly if something's missing).

**Windows** — double-click [run.bat](run.bat), or from a terminal:

```powershell
run.bat
```

**macOS / Linux** — from a terminal:

```bash
chmod +x run.sh   # first time only
./run.sh
```

Either script: finds a compatible Python, creates a `venv/` folder next to LOMA if one
doesn't exist yet, installs everything LOMA needs, and starts the app. The first run
downloads roughly 1–2 GB of dependencies, so it can take a few minutes — every run after
that starts in seconds.

Once it's running, open <http://localhost:8080> in your browser if it doesn't open
automatically.

## First run

A setup wizard walks you through everything on first launch: checking your internet
connection, connecting to a local model backend ([Ollama](https://ollama.com) or another
provider), picking starter models sized to your machine's RAM/GPU, and optional voice
input. You can skip any step and revisit it later from **Settings → Configuration**.

Anything not installed yet (image generation, deeper voice cloning, web page
reading, ffmpeg) shows up in **Settings → Configuration** with a one-click **Install**
button — no terminal, no manual pip commands. LOMA also offers the same install prompt
automatically the moment a feature you try to use needs something that isn't there yet.

## Where your data lives

| Platform | Location |
|---|---|
| Windows (packaged) | `%APPDATA%\LOMA Extended Edition` |
| macOS (packaged) | `~/Library/Application Support/LOMA Extended Edition` |
| Linux (packaged) | `~/.loma-extended-edition` |
| Running from source | the project folder's own `data/` directory |

This is where your chats, generated documents/images, and settings are stored — nothing is
sent anywhere else.

---

Building LOMA yourself or contributing code? See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
