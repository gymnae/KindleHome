#!/bin/sh
# Hides the top bar for 20 seconds, then always brings it back.
# Run this while the todo page is showing to see what goes wrong.
. /mnt/us/extensions/vikunja/bin/common.sh

if [ "$1" != "run" ]; then
    # detach, so KUAL closing its menu can't kill us before the bar is restored
    nohup /bin/sh "$EXT/bin/testbars.sh" run >/dev/null 2>&1 &
    exit 0
fi

log "test: hiding top bar for 20s"
hide_bars
sleep 20
show_bars
log "test: done"
