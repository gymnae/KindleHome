#!/bin/sh
# Usage: app.sh uninstall   - remove the fullscreen app from the app registry
. /mnt/us/extensions/kindlehome/bin/common.sh

case "$1" in
    uninstall) uninstall_app ;;
esac
