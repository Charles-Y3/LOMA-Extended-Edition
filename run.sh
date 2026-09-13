#!/usr/bin/env bash
# LOMA launcher (macOS / Linux).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

echo "LOMA launcher"
echo

# --- Find a Python 3.10-3.12 interpreter --------------------------------------
PYTHON_CMD=""
for candidate in python3.12 python3.11 python3.10 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        ver="$("$candidate" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || true)"
        case "$ver" in
            3.10|3.11|3.12)
                PYTHON_CMD="$candidate"
                break
                ;;
        esac
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "ERROR: No Python 3.10-3.12 interpreter was found on this machine."
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "Install one with Homebrew:  brew install python@3.11"
        echo "or download from:           https://www.python.org/downloads/"
    else
        echo "Install Python 3.10-3.12 via your distro's package manager, e.g.:"
        echo "  sudo apt install python3.11 python3.11-venv"
        echo "or download from: https://www.python.org/downloads/"
    fi
    exit 1
fi

echo "Using $PYTHON_CMD ($("$PYTHON_CMD" -c 'import sys; print(sys.version.split()[0])'))"

# --- Create the virtual environment if missing --------------------------------
if [ ! -x "venv/bin/python" ]; then
    echo "Creating virtual environment in ./venv ..."
    "$PYTHON_CMD" -m venv venv
    if [ ! -x "venv/bin/python" ]; then
        echo "ERROR: Failed to create the virtual environment."
        echo "On Debian/Ubuntu you may need: sudo apt install python3-venv"
        exit 1
    fi
fi

# --- Optional system dependency hint (macOS) ----------------------------------
if [[ "$OSTYPE" == "darwin"* ]] && ! command -v ffmpeg >/dev/null 2>&1; then
    echo "Note: ffmpeg not found on PATH — LOMA can still install a bundled copy"
    echo "      itself from Settings, or: brew install ffmpeg"
fi

# --- Install/update core dependencies (fast no-op if already satisfied) -------
echo "Checking dependencies (first run downloads ~1-2 GB, this can take a while)..."
venv/bin/python -m pip install --upgrade pip >/dev/null
venv/bin/python -m pip install -r requirements.txt

# --- Launch --------------------------------------------------------------
echo
echo "Starting LOMA..."
venv/bin/python main.py
