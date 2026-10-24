#!/bin/sh
. /mnt/us/extensions/kindlehome/bin/common.sh

if [ "$1" != run ]; then
    # detach: open_page waits for KUAL to close, and KUAL actions must return quickly
    start_detached "$EXT/bin/reload.sh" run
    exit 0
fi
open_page
