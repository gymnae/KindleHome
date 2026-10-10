# Settings for the Vikunja todo display. Edit, then run "Start todo display" from KUAL.

# Page to show: your kindle-vikunja server (plain http on your LAN).
# "/" shows the dashboard (if enabled on the server), /p/<id> a Vikunja project,
# /shop the KitchenOwl shopping list. If ACCESS_KEY is set on the server, append ?key=<ACCESS_KEY>
# (only needed once, a cookie remembers it).
KV_URL="http://192.168.0.31:8080/p/3"

# Re-open the page every N hours: frees browser memory and clears e-ink ghosting.
# 0 = never.
KV_RELAUNCH_HOURS=12

# EXPERIMENTAL: hide the Kindle's top bar (status line + browser toolbar).
# Known to break the display on some firmware, hence off by default.
# 1 = hide, 0 = keep. KUAL "Show top bar" or "Stop" brings it back.
KV_HIDE_BARS=0

# Fullscreen app only: "frame" (default) shows the page in a frame inside the app;
# "top" navigates the app itself to the page (try this if "frame" stays blank).
KV_APP_MODE=frame

# Frontlight level (0-24) while the display runs. Empty = leave unchanged.
KV_FRONTLIGHT=""
