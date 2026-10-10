#!/bin/sh
# Collects read-only system info into a file visible over USB:
#   <Kindle drive>/vikunja-diag.txt
. /mnt/us/extensions/vikunja/bin/common.sh

OUT=/mnt/us/vikunja-diag.txt
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
    echo "=== tools";           for t in setsid nohup ps sqlite3; do echo "$t: $(command -v $t)"; done
    echo "=== watchdog";        cat "$PIDFILE" 2>&1 && kill -0 "$(cat "$PIDFILE")" 2>&1 && echo running
    echo "=== processes";       ps 2>&1 | head -n 80
    echo "=== binaries";        ls -l /usr/bin/mesquite /usr/bin/browserd /usr/bin/pillow* 2>&1
    echo "=== komic (example of a fullscreen web app on this device)"
    ls -lR /mnt/us/extensions/komic 2>&1 | head -n 60
    for f in /mnt/us/extensions/komic/bin/config.xml /mnt/us/extensions/komic/*.json \
             /mnt/us/extensions/komic/*.xml /mnt/us/extensions/komic/bin/*.sh /mnt/us/extensions/komic/*.sh; do
        [ -f "$f" ] && { echo "--- $f"; head -c 6000 "$f"; echo; }
    done
    echo "--- komic: chrome / messaging calls in its scripts"
    grep -rhoE ".{0,80}(chrome|pillow|messaging|appmgr|titleBar|kindle\.[a-zA-Z]+).{0,120}" \
        /mnt/us/extensions/komic/bin --include=*.js --include=*.html 2>/dev/null | head -n 60
    echo "--- appreg entries for komic"
    sqlite3 /var/local/appreg.db "SELECT * FROM properties WHERE handlerId LIKE '%komic%';" 2>&1
    sqlite3 /var/local/appreg.db "SELECT * FROM handlerIds WHERE handlerId LIKE '%komic%';" 2>&1
    sqlite3 /var/local/appreg.db ".tables" 2>&1
    echo "=== kindle-vikunja log"; tail -n 200 "$LOG" 2>&1
} > "$OUT" 2>&1
log "diagnostics done"
