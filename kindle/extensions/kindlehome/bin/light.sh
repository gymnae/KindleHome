#!/bin/sh
# Touch-controlled front light (started by start.sh when KV_LIGHT_AUTO=1):
# KV_LIGHT_ON when the screen is touched, KV_LIGHT_OFF after KV_LIGHT_TIMEOUT seconds without touch.
# It only reads touch events (alongside the Kindle's own software), so taps work as usual.
. /mnt/us/extensions/kindlehome/bin/common.sh

trap '' HUP
DEV="${KV_TOUCH_DEV:-$(find_touch_dev)}"
if [ ! -c "$DEV" ]; then
    log "light: no touch input device found (set KV_TOUCH_DEV)"
    exit 1
fi
echo $$ > "$LIGHTPID"
log "light: watching $DEV (on=${KV_LIGHT_ON:-18} off=${KV_LIGHT_OFF:-0} after ${KV_LIGHT_TIMEOUT:-60}s)"

light() {
    lipc-set-prop com.lab126.powerd flIntensity "$1"
    echo "$2" > "$LIGHTSTATE"
}

date +%s > "$TOUCHFILE"
light "${KV_LIGHT_ON:-18}" on

# timer: lights off once the last touch is old enough
(
    while true; do
        sleep 5
        last=$(cat "$TOUCHFILE" 2>/dev/null || echo 0)
        if [ "$(cat "$LIGHTSTATE")" = "on" ] && [ $(($(date +%s) - last)) -ge "${KV_LIGHT_TIMEOUT:-60}" ]; then
            light "${KV_LIGHT_OFF:-0}" off
        fi
    done
) &

# Block until touch events arrive. One read takes everything queued (a tap is dozens of
# events); the sleep keeps this to at most one wake-up per second.
while dd if="$DEV" of=/dev/null bs=4096 count=1 2>/dev/null; do
    date +%s > "$TOUCHFILE"
    [ "$(cat "$LIGHTSTATE")" = "off" ] && light "${KV_LIGHT_ON:-18}" on
    sleep 1
done
log "light: reading $DEV failed, stopping"
