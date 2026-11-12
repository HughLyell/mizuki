#!/usr/bin/env bash
# Headless-ish GUI smoke test for Mizuki.
#
# Run this on a machine with a real display (Wayland or X11) and GTK4:
#
#   bash scripts/gui-check.sh
#   bash scripts/gui-check.sh --screenshot
#
# It exits non-zero if the app crashes or the smoke-test marker is missing.
# Logs and screenshots are written to ${XDG_CACHE_HOME:-~/.cache}/mizuki/gui-check.
set -uo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

OUT="${XDG_CACHE_HOME:-$HOME/.cache}/mizuki/gui-check"
mkdir -p "$OUT"
SMOKE_LOG="$OUT/smoke.log"

echo "== Mizuki GUI smoke test =="
if command -v timeout >/dev/null 2>&1; then
    timeout 40 ./run.sh --smoke-test >"$SMOKE_LOG" 2>&1
    rc=$?
else
    ./run.sh --smoke-test >"$SMOKE_LOG" 2>&1
    rc=$?
fi

cat "$SMOKE_LOG"
if [ "$rc" -ne 0 ] || ! grep -q "MIZUKI GUI SMOKE: OK" "$SMOKE_LOG"; then
    echo "GUI smoke test FAILED (exit $rc). Log: $SMOKE_LOG" >&2
    exit 1
fi
echo "GUI smoke test OK. Log: $SMOKE_LOG"

if [ "${1:-}" != "--screenshot" ]; then
    exit 0
fi

echo "== Launching the real app for a screenshot =="
APP_LOG="$OUT/app.log"
shot="$OUT/mizuki.png"
rm -f "$shot"
MIZUKI_SCREENSHOT="$shot" MIZUKI_SCREENSHOT_DELAY="${MIZUKI_SCREENSHOT_DELAY:-6}" \
    ./run.sh >"$APP_LOG" 2>&1

if [ -f "$shot" ]; then
    echo "Screenshot: $shot"
else
    echo "In-app screenshot failed; trying a desktop screenshot tool."
    ./run.sh >"$APP_LOG" 2>&1 &
    pid=$!
    sleep 6
    if [ -n "${WAYLAND_DISPLAY:-}" ] && command -v grim >/dev/null 2>&1; then
        grim "$shot" >/dev/null 2>&1 || true
    elif command -v gnome-screenshot >/dev/null 2>&1; then
        gnome-screenshot -f "$shot" >/dev/null 2>&1 || true
    elif command -v spectacle >/dev/null 2>&1; then
        spectacle -b -n -o "$shot" >/dev/null 2>&1 || true
    elif command -v import >/dev/null 2>&1; then
        import -window root "$shot" >/dev/null 2>&1 || true
    fi
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    if [ -f "$shot" ]; then
        echo "Screenshot: $shot"
    else
        echo "No screenshot tool found. Install grim (Wayland), gnome-screenshot or imagemagick."
    fi
fi
echo "App log: $APP_LOG"
