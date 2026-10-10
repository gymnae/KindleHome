#!/bin/sh
# Usage: app.sh uninstall   - remove the fullscreen app from the app registry
. /mnt/us/extensions/vikunja/bin/common.sh

case "$1" in
    uninstall) uninstall_app ;;
esac
