#!/bin/sh
# Usage: bars.sh hide|show
. /mnt/us/extensions/kindlehome/bin/common.sh

case "$1" in
    hide) hide_bars ;;
    *) show_bars ;;
esac
