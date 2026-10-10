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
    echo "=== processes";       ps 2>&1 | grep -i -e browser -e pillow -e mesquite -e webkit -e cvm | grep -v grep
    echo "=== binaries";        ls -l /usr/bin/mesquite /usr/bin/browserd /usr/bin/pillow* 2>&1
    echo "=== kindle-vikunja log"; tail -n 200 "$LOG" 2>&1
} > "$OUT" 2>&1
log "diagnostics done"
