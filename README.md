# LOMA — Local Orchestrated Multimodal Automation

**Version 1.0.0** — available for Windows and macOS.

Privacy-first local agentic workstation.

LOMA is not a simple chat app. It is a modular offline AI workspace combining multimodal
file understanding, service-first routing, plugin-based extensibility, local LLM inference,
interactive workspace tooling, and persistent context and artifacts — all running on your
own machine.

**Core principles**

- Local-first execution (cloud APIs optional, never required)
- Nothing leaves your machine unless you explicitly enable web grounding or an online
  provider

---

## Getting started

### Option A — Download the app (easiest, no Python needed)

Grab the latest build from the project's GitHub **Releases** page.

| Platform | File | Notes |
|---|---|---|
| Windows | `LOMA-windows.zip` | Unzip anywhere, run `LOMA.exe` inside the folder |
| macOS | `LOMA-macos.zip` | **Apple Silicon (M1/M2/M3/M4) only** — see below for Intel Macs |

**First launch on macOS:** the app isn't notarized by Apple, so Gatekeeper will flag it as
from an unidentified developer the first time. Either:

```bash
xattr -dr com.apple.quarantine /path/to/LOMA.app
```

or right-click (Control-click) `LOMA.app` → **Open** → **Open** in the dialog. Either way,
it's a one-time step per download — after that it opens normally.

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
| Windows (packaged) | `%APPDATA%\LOMA` |
| macOS (packaged) | `~/Library/Application Support/LOMA` |
| Linux (packaged) | `~/.loma` |
| Running from source | the project folder's own `data/` directory |

This is where your chats, generated documents/images, and settings are stored — nothing is
sent anywhere else.

---

Building LOMA yourself or contributing code? See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
