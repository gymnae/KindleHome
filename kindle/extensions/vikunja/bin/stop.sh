#!/bin/sh
# Back to normal Kindle behaviour.
. /mnt/us/extensions/vikunja/bin/common.sh

stop_watchdog
lipc-set-prop com.lab126.powerd preventScreenSaver 0
log "stopped"
lipc-set-prop com.lab126.appmgrd start app://com.lab126.booklet.home
