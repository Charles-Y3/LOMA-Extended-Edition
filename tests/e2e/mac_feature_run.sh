#!/bin/bash
# Run ONE feature end-to-end test against the shipped app on a macOS runner.
#   bash tests/e2e/mac_feature_run.sh <name> <script.py> <engine> [wizard=0|1]
#
# - runs the app from the READ-ONLY mount (/Volumes/LOMARO) with a fresh user-data folder and a
#   Finder-like minimal environment
# - seeds settings to the tiny local Ollama model (setup completed unless wizard=1)
# - forwards the test hooks the caller exported (LOMA_E2E_*) to the app
# - after the test: shows log tails, checks the app never tried to write into its own folder,
#   and runs the feature's file-system checks (did the data really land in the user's folder?)
set -o pipefail   # (no -u: macOS's bash 3.2 errors on empty arrays)
NAME="$1"; SCRIPT="$2"; ENGINE="$3"; WIZARD="${4:-0}"
APP="/Volumes/LOMARO/LOMA Extended Edition.app/Contents/MacOS/LOMA Extended Edition"
H="$RUNNER_TEMP/home-$NAME"; ROOT="$H/Library/Application Support/LOMA Extended Edition"
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

extra=()
for v in LOMA_E2E_PICK_FOLDER LOMA_E2E_WIZARD_STEP LOMA_E2E_IMAGE_MODEL; do
  if [ -n "${!v:-}" ]; then extra+=("$v=${!v}"); fi
done

echo "=== $NAME ($SCRIPT, $ENGINE) ==="
env -i HOME="$H" PATH=/usr/bin:/bin:/usr/sbin:/sbin LOMA_PORT=8765 LOMA_BROWSER_OPENED=1 "${extra[@]}" \
  "$APP" > "$RUNNER_TEMP/app-$NAME.log" 2>&1 &
APP_PID=$!
for i in $(seq 1 120); do curl -sf http://127.0.0.1:8765/ >/dev/null && break; sleep 1; done

fail=0
if [ "$SCRIPT" = "voice_step_e2e.py" ]; then
  python "tests/e2e/$SCRIPT" "$ENGINE" || fail=1          # takes just the engine
else
  python "tests/e2e/$SCRIPT" "$NAME" "$ENGINE" || fail=1   # feature name, engine
fi

echo "--- app stdout/stderr (tail) ---"; tail -25 "$RUNNER_TEMP/app-$NAME.log" | grep -v "Downloading bytes" || true
echo "--- loma.log (tail) ---"; tail -30 "$ROOT/logs/loma.log" 2>/dev/null || true
if grep -a -q -E "Read-only file system|Errno 30" "$RUNNER_TEMP/app-$NAME.log" "$ROOT/logs/loma.log" 2>/dev/null; then
  echo "!! the app tried to write inside its own read-only folder"; fail=1
fi

echo "--- file-system checks ---"
case "$NAME" in
  chat_archive)
    f="$(find "$ROOT/data/chats" -name 'freeze_*.md' 2>/dev/null | head -1)"
    if [ -n "$f" ] && grep -q "E2E archive test" "$f"; then echo "ok: saved chat file $f"; else echo "!! no saved chat file with the title under $ROOT/data/chats"; fail=1; fi ;;
  voice_reply)
    v="$(find "$ROOT/tts/piper_voices" -name '*.onnx' 2>/dev/null | head -1)"
    if [ -n "$v" ]; then echo "ok: Piper voice $v ($(du -h "$v" | cut -f1))"; else echo "!! no Piper voice file under $ROOT/tts/piper_voices"; fail=1; fi
    # piper-tts is bundled in the app, so pip has nothing to install for it; just list what (if
    # anything) landed in the user's package folder.
    echo "python-packages: $(ls "$ROOT/python-packages/lib/"*/site-packages 2>/dev/null | head -8 | tr '
' ' ')" ;;
  sensevoice)
    s="$(find "$H/.cache" "$ROOT" -iname '*sensevoice*' 2>/dev/null | head -1)"
    if [ -n "$s" ]; then echo "ok: SenseVoice files at $s"; else echo "!! no SenseVoice model files found"; fail=1; fi ;;
  image)
    g="$(find "$ROOT/data" -iname '*.png' 2>/dev/null | head -1)"
    if [ -n "$g" ]; then echo "ok: generated image $g ($(du -h "$g" | cut -f1))"; else echo "!! no generated .png under $ROOT/data"; fail=1; fi ;;
  *) echo "(none for $NAME)" ;;
esac

kill $APP_PID 2>/dev/null || true
pkill -f "LOMARO/LOMA Extended Edition.app/Contents/MacOS" 2>/dev/null || true
sleep 3
if [ "$fail" = "0" ]; then echo "RESULT $NAME: PASS"; else echo "RESULT $NAME: FAIL"; fi
exit $fail
