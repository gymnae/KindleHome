# Shared helpers, sourced by the other scripts.
EXT=/mnt/us/extensions/vikunja
PIDFILE=/tmp/kindle-vikunja-watchdog.pid
LOG=/tmp/kindle-vikunja.log
. "$EXT/config.sh"

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

open_page() {
    log "opening $KV_URL"
    lipc-set-prop com.lab126.appmgrd start "app://com.lab126.browser?view=$KV_URL"
}

stop_watchdog() {
    if [ -f "$PIDFILE" ]; then
        kill "$(cat "$PIDFILE")" 2>/dev/null
        rm -f "$PIDFILE"
    fi
}
