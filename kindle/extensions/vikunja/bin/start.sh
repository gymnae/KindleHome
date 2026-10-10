#!/bin/sh
# Keep the Kindle awake and show the todo page in the browser.
. /mnt/us/extensions/vikunja/bin/common.sh

stop_watchdog
lipc-set-prop com.lab126.powerd preventScreenSaver 1
[ -n "$KV_FRONTLIGHT" ] && lipc-set-prop com.lab126.powerd flIntensity "$KV_FRONTLIGHT"

nohup /bin/sh "$EXT/bin/watchdog.sh" >/dev/null 2>&1 &
echo $! > "$PIDFILE"

open_page
