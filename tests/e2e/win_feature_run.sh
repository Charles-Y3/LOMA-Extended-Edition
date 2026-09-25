#!/bin/bash
# Windows twin of mac_feature_run.sh: run ONE feature end-to-end test against the shipped Windows
# app on a windows-latest runner (Git Bash).
#   bash tests/e2e/win_feature_run.sh <name> <script.py> <engine> [wizard=0|1]
# Runs the exe from a write-denied folder (see windows-e2e.yml), fresh %APPDATA%, seeded settings
# (tiny local Ollama model), forwards LOMA_E2E_* hooks, then file-system checks.
set -o pipefail
NAME="$1"; SCRIPT="$2"; ENGINE="$3"; WIZARD="${4:-0}"
EXE="$PWD/shipped/LOMA Extended Edition/LOMA Extended Edition.exe"
H="$RUNNER_TEMP/home-$NAME"; ROOT="$H/LOMA Extended Edition"
rm -rf "$H"; mkdir -p "$ROOT/data"

python - "$ROOT/data/settings.json" "$CHAT_MODEL" "$WIZARD" "${LOMA_E2E_IMAGE_MODEL:-}" <<'PY'
import json, sys
path, model, wizard, img = sys.argv[1], sys.argv[2], sys.argv[3] == "1", sys.argv[4]
data = {
    "language": "en",
    "assignments": {r: model for r in ("Orchestrator", "Specialist", "General", "Verifier")},
    "setup": {"completed": not wizard, "provider": "ollama", "provider_url": "http://127.0.0.1:11434",
              "skipped_steps": [], "installed_assets": {}, "language_picked": not wizard},
}
if img:
    data["image_model_prefs"] = {img: {"quality_mode": "high", "resolution_preset": "square"}}
json.dump(data, open(path, "w"), indent=4)
PY

echo "=== $NAME ($SCRIPT, $ENGINE) ==="
APPDATA="$(cygpath -w "$H")" LOMA_PORT=8765 LOMA_BROWSER_OPENED=1 \
  LOMA_E2E_PICK_FOLDER="${LOMA_E2E_PICK_FOLDER:-}" LOMA_E2E_WIZARD_STEP="${LOMA_E2E_WIZARD_STEP:-}" \
  LOMA_E2E_IMAGE_MODEL="${LOMA_E2E_IMAGE_MODEL:-}" \
  "$EXE" > "$RUNNER_TEMP/app-$NAME.log" 2>&1 &
for i in $(seq 1 120); do curl -sf http://127.0.0.1:8765/ >/dev/null && break; sleep 1; done

fail=0
python "tests/e2e/$SCRIPT" "$NAME" "$ENGINE" || fail=1

echo "--- app stdout/stderr (tail) ---"; tail -25 "$RUNNER_TEMP/app-$NAME.log" | grep -v "Downloading bytes" || true
echo "--- loma.log (tail) ---"; tail -30 "$ROOT/logs/loma.log" 2>/dev/null || true
echo "--- ollama log (tail) ---"; tail -15 "$RUNNER_TEMP/ollama.log" 2>/dev/null || true

echo "--- file-system checks ---"
case "$NAME" in
  image)
    find "$ROOT/data" -path '*generated*' -name '*.txt' -exec sh -c 'echo "--- fallback file $1 ---"; head -70 "$1"' _ {} \; 2>/dev/null
    m="$(find "$H/.cache/huggingface" -iname '*tiny-stable-diffusion*' 2>/dev/null | head -1)"
    if [ -n "$m" ]; then echo "ok: tiny model downloaded by the app: $m"; else echo "!! tiny model not in the Hugging Face cache"; fail=1; fi
    g="$(find "$ROOT/data" -iname '*.png' 2>/dev/null | head -1)"
    if [ -n "$g" ]; then echo "ok: generated image $g ($(du -h "$g" | cut -f1))"; else echo "!! no generated .png under $ROOT/data"; fail=1; fi ;;
  *) echo "(none for $NAME)" ;;
esac

taskkill //F //T //IM "LOMA Extended Edition.exe" > /dev/null 2>&1 || true
sleep 3
if [ "$fail" = "0" ]; then echo "RESULT $NAME: PASS"; else echo "RESULT $NAME: FAIL"; fi
exit $fail
