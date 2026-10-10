#!/bin/sh
# Back to normal Kindle behaviour.
. /mnt/us/extensions/vikunja/bin/common.sh

stop_watchdog
stop_light
[ "${KV_LIGHT_AUTO:-1}" = "1" ] && lipc-set-prop com.lab126.powerd flIntensity "${KV_LIGHT_ON:-18}"
show_bars
lipc-set-prop com.lab126.powerd preventScreenSaver 0
log "stopped"
lipc-set-prop com.lab126.appmgrd start app://com.lab126.booklet.home
