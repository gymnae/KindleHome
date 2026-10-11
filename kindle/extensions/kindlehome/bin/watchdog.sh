#!/bin/sh
# Runs in the background while the display is active (started by start.sh):
# keeps the screensaver off, wifi on, periodically re-opens the page, and
# re-opens the fullscreen app if it closed without its Exit link.
. /mnt/us/extensions/kindlehome/bin/common.sh

trap '' HUP
echo $$ > "$PIDFILE"
log "watchdog started (pid $$)"
hide_bars_if_configured
last=$(date +%s)
active=""
minutes=0
reopened=0
while true; do
    sleep 60
    minutes=$((minutes + 1))
    [ $((minutes % 60)) -eq 0 ] && log "status: $(status_line)"
    now_app=$(active_app)
    if [ "$now_app" != "$active" ]; then
        log "active app: ${now_app:-?} ($(status_line))"
        # The fullscreen app was showing and now the home screen is, but nobody tapped Exit:
        # it crashed (or was unloaded). Without a readable app state, do nothing (fail safe).
        if [ "$active" = "$APP_ID" ] && [ "$now_app" = com.lab126.booklet.home ] \
           && [ "$(cat "$MODEFILE" 2>/dev/null)" = fullscreen ] && [ "$(app_state)" = r ]; then
            crash_report "fullscreen app closed unexpectedly"
            if [ "$reopened" -lt 10 ]; then
                reopened=$((reopened + 1))
                log "fullscreen app closed unexpectedly, reopening ($reopened); details in $CRASHFILE"
                open_page
            else
                log "fullscreen app closed unexpectedly again, giving up after $reopened reopens"
            fi
        fi
        active=$now_app
    fi
    lipc-set-prop com.lab126.powerd preventScreenSaver 1
    if [ "${KV_LIGHT_AUTO:-1}" = "1" ] && ! light_running; then
        log "light control not running, restarting"
        start_light
    fi
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
