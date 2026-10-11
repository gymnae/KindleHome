#!/bin/sh
# Collects read-only system info into a file visible over USB:
#   <Kindle drive>/kindlehome-diag.txt
. /mnt/us/extensions/kindlehome/bin/common.sh

OUT=/mnt/us/kindlehome-diag.txt
log "collecting diagnostics into $OUT"
{
    echo "=== date";            date
    echo "=== firmware";        cat /etc/prettyversion.txt 2>&1; cat /etc/version.txt 2>&1
    echo "=== uname";           uname -a
    echo "=== lipc pillow";     lipc-probe -v com.lab126.pillow 2>&1
    echo "=== lipc chromebar";  lipc-probe -v com.lab126.chromebar 2>&1
    echo "=== lipc browser";    lipc-probe -v com.lab126.browser 2>&1
    echo "=== lipc appmgrd";    lipc-probe -v com.lab126.appmgrd 2>&1
    echo "=== lipc services";   lipc-probe -l 2>&1 | grep -i -e pillow -e chrome -e browser -e mesquite -e waf
    echo "=== appreg (browser / web apps)"
    sqlite3 /var/local/appreg.db \
        "SELECT handlerId, name, value FROM properties WHERE handlerId LIKE '%browser%' OR value LIKE '%mesquite%' OR value LIKE '%waf%';" 2>&1
    echo "=== tools";           ls -l /usr/bin/setsid /bin/setsid /sbin/setsid /usr/bin/nohup /bin/nohup 2>&1
    echo "=== watchdog";        cat "$PIDFILE" 2>&1 && kill -0 "$(cat "$PIDFILE")" 2>&1 && echo running
    echo "=== input devices";   cat /proc/bus/input/devices 2>&1
    echo "=== touch device";    find_touch_dev
    echo "=== front light";     lipc-get-prop com.lab126.powerd flIntensity 2>&1
    lipc-probe -v com.lab126.powerd 2>&1 | grep -i -e fl -e light
    ls /sys/devices/system/ 2>&1 | grep -i fl
    echo "light control: $(cat "$LIGHTSTATE" 2>/dev/null) pid $(cat "$LIGHTPID" 2>/dev/null)"
    echo "=== fullscreen app";  cat "$MODEFILE" 2>&1; cat "$EXT/app/config.js" 2>&1
    sqlite3 "$APPREG" "SELECT * FROM properties WHERE handlerId = '$APP_ID';" 2>&1
    echo "=== mesquite apps";   ls /var/local/mesquite/ 2>&1
    for app in browser store; do
        dir=/var/local/mesquite/$app
        echo "=== $app: files";    ls -l "$dir" 2>&1 | head -n 40
        echo "=== $app: config.xml"; cat "$dir/config.xml" 2>&1 | head -n 150
        echo "=== $app: chrome / pillow / messaging usage"
        grep -r -i -e configureChrome -e dismissChrome -e displayChrome -e titleBar -e "kindle\.chrome" \
            -e "kindle\.messaging" -e "kindle\.appmgr" -e "\.pillow" "$dir" 2>/dev/null \
            | cut -c1-300 | head -n 60
    done
    echo "=== komic: config files"
    for f in /mnt/us/extensions/komic/config.xml /mnt/us/extensions/komic/menu.json \
             /mnt/us/extensions/komic/bin/config.xml; do
        [ -f "$f" ] && { echo "--- $f"; cat "$f" | head -n 120; }
    done
    echo "--- komic: head of index.html"; head -n 60 /mnt/us/extensions/komic/bin/index.html 2>&1
    echo "--- komic: chrome / pillow / messaging usage"
    grep -r -i -e chrome -e pillow -e "kindle\.messaging" -e "kindle\.appmgr" -e titleBar \
        /mnt/us/extensions/komic/bin 2>/dev/null | grep -v "/locales/" | cut -c1-300 | head -n 60
    echo "--- komic: scripts"; ls -l /mnt/us/extensions/komic/bin/resource/Scripts 2>&1
    for f in /mnt/us/extensions/komic/bin/resource/Scripts/*.sh; do
        [ -f "$f" ] && { echo "--- $f"; cat "$f" | head -n 80; }
    done
    echo "=== KindleHome log"; tail -n 200 "$LOG" 2>&1
} > "$OUT" 2>&1
log "diagnostics done"
