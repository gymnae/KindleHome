#!/bin/sh
# Runs in the background while the display is active (started by start.sh):
# keeps the screensaver off, wifi on, and periodically re-opens the page.
. /mnt/us/extensions/vikunja/bin/common.sh

log "watchdog started"
hide_bars_if_configured
last=$(date +%s)
while true; do
    sleep 60
    lipc-set-prop com.lab126.powerd preventScreenSaver 1
    if [ "$(lipc-get-prop com.lab126.cmd wirelessEnable 2>/dev/null)" = "0" ]; then
        log "wifi was off, enabling"
        lipc-set-prop com.lab126.cmd wirelessEnable 1
    fi
    if [ "${KV_RELAUNCH_HOURS:-0}" -gt 0 ]; then
        now=$(date +%s)
        # also catches the clock jumping backwards after an NTP sync
        if [ $((now - last)) -ge $((KV_RELAUNCH_HOURS * 3600)) ] || [ "$now" -lt "$last" ]; then
            open_page
            hide_bars_if_configured
            last=$(date +%s)
        fi
    fi
done
