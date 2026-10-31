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

# The touch controller's interrupt count (/proc/interrupts), empty if not found. It goes up
# with every tap, also those on the on-screen keyboard, which don't always reach our reader.
touch_irqs() {
    awk '{ l = tolower($0) }
         l ~ /zforce|touch|cyttsp|elan|pixcir|cypress/ {
             for (i = 2; i <= NF && $i ~ /^[0-9]+$/; i++) s += $i; print s; exit
         }' /proc/interrupts 2>/dev/null
}

# timer: lights off once the last touch is old enough
(
    irq=$(touch_irqs)
    irq_at=$(date +%s)
    [ -n "$irq" ] && log "light: also counting touch interrupts ($(awk 'tolower($0) ~ /zforce|touch|cyttsp|elan|pixcir|cypress/' /proc/interrupts | head -n 1))"
    while true; do
        sleep 5
        [ "$(cat "$LIGHTSTATE")" = "on" ] || continue
        now=$(date +%s)
        last=$(cat "$TOUCHFILE" 2>/dev/null || echo 0)
        if [ -n "$irq" ]; then
            n=$(touch_irqs)
            [ "$n" != "$irq" ] && { irq=$n; irq_at=$now; }
        fi
        [ $((now - last)) -ge "${KV_LIGHT_TIMEOUT:-60}" ] || continue
        # Touches seen only as interrupts count too, but for at most 10 minutes after the last
        # real one (in case the controller also interrupts without being touched).
        if [ -n "$irq" ] && [ $((now - irq_at)) -lt "${KV_LIGHT_TIMEOUT:-60}" ] && [ $((now - last)) -lt 600 ]; then
            continue
        fi
        # Ask the page whether someone is typing (it reports that while an input is in use).
        age=$(typing_age)
        case "$age" in
            ''|-*|*[!0-9]*) light "${KV_LIGHT_OFF:-0}" off ;;
            *) if [ "$age" -lt 30 ]; then date +%s > "$TOUCHFILE"; else light "${KV_LIGHT_OFF:-0}" off; fi ;;
        esac
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
