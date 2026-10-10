#!/bin/sh
# Keep the Kindle awake and show the todo page.
# Usage: start.sh [browser|fullscreen]   (default: browser)
. /mnt/us/extensions/vikunja/bin/common.sh

MODE="${1:-browser}"
log "start.sh $MODE"
echo "$MODE" > "$MODEFILE"
[ "$MODE" = "fullscreen" ] && install_app

stop_watchdog
lipc-set-prop com.lab126.powerd preventScreenSaver 1
[ -n "$KV_FRONTLIGHT" ] && lipc-set-prop com.lab126.powerd flIntensity "$KV_FRONTLIGHT"

start_detached "$EXT/bin/watchdog.sh"

open_page
