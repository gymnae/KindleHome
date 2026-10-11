#!/bin/sh
# Keep the Kindle awake and show the todo page.
# Usage: start.sh [browser|fullscreen]   (default: browser)
. /mnt/us/extensions/kindlehome/bin/common.sh

MODE="${1:-browser}"
log "start.sh $MODE"
echo "$MODE" > "$MODEFILE"
[ "$MODE" = "fullscreen" ] && install_app

stop_watchdog
stop_light
lipc-set-prop com.lab126.powerd preventScreenSaver 1
if [ "${KV_LIGHT_AUTO:-1}" = "1" ]; then
    start_light
elif [ -n "$KV_FRONTLIGHT" ]; then
    lipc-set-prop com.lab126.powerd flIntensity "$KV_FRONTLIGHT"
fi

start_detached "$EXT/bin/watchdog.sh"

open_page
