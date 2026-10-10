# Settings for the Vikunja todo display. Edit, then run "Start todo display" from KUAL.

# Page to show: your kindle-vikunja server (plain http on your LAN).
# /p/<id> picks the project. If ACCESS_KEY is set on the server, append ?key=<ACCESS_KEY>
# (only needed once, a cookie remembers it).
KV_URL="http://192.168.1.10:8080/p/1"

# Re-open the page every N hours: frees browser memory and clears e-ink ghosting.
# 0 = never.
KV_RELAUNCH_HOURS=12

# Frontlight level (0-24) while the display runs. Empty = leave unchanged.
KV_FRONTLIGHT=""
