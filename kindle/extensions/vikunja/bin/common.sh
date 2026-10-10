# Shared helpers, sourced by the other scripts.
EXT=/mnt/us/extensions/vikunja
PIDFILE=/tmp/kindle-vikunja-watchdog.pid
LOG=/tmp/kindle-vikunja.log
. "$EXT/config.sh"

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

# The top bar ("pillow") is the status line and browser toolbar.
hide_bars() {
    log "hiding top bar"
    lipc-set-prop com.lab126.pillow disableEnablePillow disable
}

show_bars() {
    log "showing top bar"
    lipc-set-prop com.lab126.pillow disableEnablePillow enable
}

open_page() {
    log "opening $KV_URL"
    lipc-set-prop com.lab126.appmgrd start "app://com.lab126.browser?view=$KV_URL"
    if [ "${KV_HIDE_BARS:-1}" = "1" ]; then
        sleep 4   # let the browser come up first, it redraws the bar on start
        hide_bars
    fi
}

stop_watchdog() {
    if [ -f "$PIDFILE" ]; then
        kill "$(cat "$PIDFILE")" 2>/dev/null
        rm -f "$PIDFILE"
    fi
}
