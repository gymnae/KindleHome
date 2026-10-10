# Shared helpers, sourced by the other scripts.
EXT=/mnt/us/extensions/vikunja
PIDFILE=/tmp/kindle-vikunja-watchdog.pid
LOG=/tmp/kindle-vikunja.log
. "$EXT/config.sh"

# Everything (including lipc errors) goes to the log for troubleshooting.
exec >>"$LOG" 2>&1

log() { echo "$(date '+%F %T') $*"; }

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
}

# Called by the watchdog (never from a KUAL action, which must return quickly):
# wait until the browser is up, as it redraws the bar when it starts.
hide_bars_if_configured() {
    if [ "${KV_HIDE_BARS:-0}" = "1" ]; then
        sleep 8
        hide_bars
    fi
}

stop_watchdog() {
    if [ -f "$PIDFILE" ]; then
        kill "$(cat "$PIDFILE")" 2>/dev/null
        rm -f "$PIDFILE"
    fi
}
