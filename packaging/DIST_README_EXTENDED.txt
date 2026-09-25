LOMA Extended Edition
Platform: Windows 10/11 (64-bit) and macOS (Apple Silicon)
Version: 1.0.1

============================================================
OVERVIEW
============================================================

LOMA Extended Edition is a privacy-first, offline AI workspace — chat,
document generation, image generation, and multimodal file understanding
(documents, presentations, spreadsheets, audio, images, video), all running
locally on your own machine via Ollama. Extended Edition adds image
generation, Knowledge Vault (document RAG), Research, News Brief, and other
extensions on top of the shared LOMA pipeline.

============================================================
INSTALLATION & FIRST-TIME RUN
============================================================

Unzip this folder anywhere and double-click "LOMA Extended Edition.exe"
inside it (or "LOMA Extended Edition.app" on macOS). It opens in your
default web browser at http://127.0.0.1:8000 — that browser tab is the
app's interface; the executable itself just runs the local server behind it.

Windows Defender SmartScreen
------------------------------------------------------------
Because this application is unsigned, Windows may show:

  "Windows protected your PC"

This is normal for small independent tools. To run the program:

1. Click "More info"
2. Click "Run anyway"

Ollama (local model backend)
------------------------------------------------------------
LOMA Extended Edition needs Ollama installed and running locally to serve
the language/vision models it uses. If it isn't detected, the app will
guide you through installing it and pulling a model the first time you run
it.

============================================================
PRIVACY AND OFFLINE USE
============================================================

LOMA Extended Edition runs entirely on your own machine:

- No internet required for normal use (only for the initial model
  download via Ollama, or if you explicitly enable web grounding)
- No data uploaded anywhere
- No telemetry, no analytics
- All documents, chats, and generated files stay on your machine

============================================================
WHERE YOUR DATA LIVES
============================================================

Your chats, settings, and ingested documents (Knowledge Vault) are NOT
stored in this folder — they live in a per-user AppData folder instead, so
they survive you replacing this folder with a newer version later (no
re-ingesting your documents after every update).

A shortcut named "Open Data Folder" sits right next to this exe — double-
click it any time to jump straight to where your data actually lives.

Updating to a newer version
------------------------------------------------------------
Just download the new version's folder and run its exe — your data is
picked up automatically from the same AppData location. You do not need to
copy anything between the old and new folders.

Uninstalling
------------------------------------------------------------
Delete this folder to remove the app itself. Your data in AppData is left
untouched unless you also delete it via the "Open Data Folder" shortcut
above.

============================================================
LICENSING AND OPEN-SOURCE COMPONENTS
============================================================

LOMA Extended Edition is built on the following open-source projects,
among others listed in requirements.txt:

Ollama
- Local LLM inference runtime
- See ollama.com for its own license terms

NiceGUI
- Application UI framework
- Licensed under the MIT License

Diffusers / PyTorch
- Local image generation
- Licensed under the Apache License 2.0 / BSD License respectively

faster-whisper / FunASR (SenseVoice)
- Local speech-to-text
- Licensed under the MIT License

edge-tts
- Local text-to-speech
- Licensed under the LGPL-3.0 License

Third-party licenses remain the property of their respective authors and
are not altered or superseded by this application's own license.

============================================================
LICENSE (this application)
============================================================

See LICENSE.txt in this folder for the full text. In short: personal,
non-commercial use only — see LICENSE.txt for the complete permitted uses,
restrictions, acknowledgements, and disclaimer.

============================================================
DISCLAIMER
============================================================

LOMA Extended Edition is an AI-assisted system designed to support your own
work, not replace your judgment.
- Provided "as is" without warranty of any kind.
- AI-generated results (text and images) may contain inaccuracies or
  unexpected content.
- You are responsible for verifying outputs before relying on them.
- The author assumes no liability for decisions made based on this
  software's outputs.
