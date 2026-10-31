# KindleHome

Turn a jailbroken Kindle (built for the Paperwhite 1) into a wall-mounted,
touch-enabled household display:

- the todo list of one or more [Vikunja](https://vikunja.io) projects
- the shopping list of a [KitchenOwl](https://kitchenowl.org) household
- the next events from CalDAV calendars or `.ics` links
- a dashboard that shows all of it, with a tile per list, and opens them on a tap

Todo lists:

- Quick overview of the open tasks in a project, in the same order as in
  Vikunja's list view (drag and drop there to reorder)
- Tap the box to tick a task off (or tap again to undo)
- Subtasks are listed on the task's detail page, so the main list stays uncluttered.
  Both kinds work: real subtasks, and checklist items typed in the description
  (`- [ ] item`). Both can be ticked off, done ones included
- Tap a task's text to open its detail page, with the description, its subtasks (tickable),
  its parent task and a field to add a subtask
- On the detail page, tap the task's title, a subtask or a checklist item to rename it
- Add new tasks on the main page. On a detail page, new subtasks are added as `- [ ] item`
  checklist entries to the description, as in Vikunja's editor
- Due dates (overdue ones shown inverted), priority (`!`–`!!!`), assignees (in *bold italics*),
  labels, subtask progress
- Re-syncs automatically, but only redraws when something changed (saves e-ink flashes)

Dashboard:

- Fits on one screen, without scrolling. The *ToDo* and *Shopping List* tiles
  share the free space equally, keep a fixed size, and show the same number
  of entries. The rest is summed up as "+N more". The detail pages show the
  real project and list names, and they can be as long as needed

Shopping list:

- Several lists in the household: *Lists* in the header switches between them.
  The dashboard always shows KitchenOwl's default list (or `KITCHENOWL_LIST_ID`)

- Items are grouped by KitchenOwl category. Tap the box to tick an item off
- Tap the note next to an item (`+` when empty) to edit it, e.g. "2 l"
- "Recently bought" items: tick one to put it back on the list
- Add items with the field at the top. As you type, KitchenOwl's suggestions
  appear below the field (with icons). Tap one to add it. Amounts work as in
  the app: "milk 2l" adds *Milk* with the note "2l"
- Item icons as in the app (see below)

```
┌──────────┐  plain HTTP (LAN)  ┌────────────────┐  HTTPS + API tokens  ┌────────────┐
│  Kindle  │ ─────────────────▶ │   KindleHome   │ ───────────────────▶ │ Vikunja    │
│ browser  │  simple HTML/ES5   │  (this server) │ ───────────────────▶ │ KitchenOwl │
└──────────┘                    └────────────────┘                      └────────────┘
```

**Why a server in between?** The Kindle's experimental browser is a 2012 WebKit.
It can't handle modern TLS or the Vikunja web app. The server keeps your API
token off the Kindle and serves lightweight pages the old browser can render.
It is a single Python file with no dependencies.

## 1. Vikunja API token

Requires Vikunja **0.22 or newer** (API tokens). The current project-view API
(0.24+ / 1.x) is used, with a fallback for older versions.

In Vikunja go to *Settings → API Tokens → Create a token* and grant at least:

| Group            | Permissions                                 |
|------------------|---------------------------------------------|
| projects         | read_all, read_one                          |
| projects / views | read_all, (views tasks) read_all            |
| tasks            | read_all, read_one, create, update          |
| tasks_assignees  | create, delete (assignee chips)             |
| projects_users, projects_teams, teams | read_all (people chips; without them only the owner is offered) |

The permission names in the UI differ a bit between versions. If anything
fails with a 401/403, pick all permissions in these groups. Choose a long
expiry date, because the display stops working when the token expires.

The project ID is the number in the URL of the project in Vikunja
(`…/projects/12/…` → `12`).

## 1b. KitchenOwl token (optional)

In KitchenOwl, open *Settings → Profile → Sessions* and create a
**long-lived token**. The menu names can vary a little between versions. Put
it in `KITCHENOWL_TOKEN`, together with `KITCHENOWL_URL`. The household and
list are optional: by default the user's first household and its default
shopping list are used. Otherwise take the household number from the
KitchenOwl web URL and set `KITCHENOWL_HOUSEHOLD_ID`, and `KITCHENOWL_LIST_ID`
for a specific list.

**Icons:** KitchenOwl's item icons are by Icons8 and may not be
redistributed, so they are not part of this repository. The server instead
loads the icon font from *your* KitchenOwl at runtime
(`<KITCHENOWL_URL>/assets/fonts/Items.ttf`) and serves it to the Kindle. The
name → icon map comes from KitchenOwl's source (`item_icons.dart`, fetched
from GitHub). The server only turns icons on if that map matches your font
exactly, because a map from another KitchenOwl version would show wrong
icons. If no icons appear, the server log says why. Usually the fix is to
point `KITCHENOWL_ICON_MAP_URL` at the file for your KitchenOwl version. Set
`KITCHENOWL_ICONS=0` to turn them off.

**Emoji** (e.g. in list or category names) can't be shown by the Kindle's
fonts. The server replaces them with small pictures from
[Twemoji](https://github.com/jdecked/twemoji) (graphics © Twitter/X and
contributors, CC-BY 4.0). It fetches them from a CDN when first needed and
caches them in memory. `EMOJI=strip` removes emoji instead.

## 1c. Calendar (optional)

Set `CALDAV_URL` to one or more calendar URLs, separated by spaces, and the
dashboard shows the next `CALDAV_EVENTS` (3) events of all of them together,
in a row of rounded boxes at the bottom. All URLs go into this one line: a second
`CALDAV_URL=` line would replace the first.

`CALDAV_USER`/`CALDAV_PASSWORD` are only sent when a server asks for a login,
so public `.ics` links never receive them. For a calendar with a different
account, put the login into its URL: `https://user:password@host/…`.

- **CalDAV**, e.g. Nextcloud
  `https://cloud.example.com/remote.php/dav/calendars/<user>/<calendar>/`,
  with `CALDAV_USER` and `CALDAV_PASSWORD`. For Nextcloud, use an app
  password (*Settings → Security*). The server asks the calendar for the next
  `CALDAV_DAYS` (30) days and lets it expand repeating events.
- **Nextcloud public share links** (`https://…/apps/calendar/p/<token>`, a web
  page) are turned into the matching CalDAV address
  (`…/remote.php/dav/public-calendars/<token>/`) automatically. No login
  needed.
- **iCalendar links** (`.ics`, `webcal://`, e.g. Google's "secret address in
  iCal format"). The server reads the file itself. Repeating events are
  supported in their common forms: daily, weekly on given days, monthly by
  date or e.g. "last Friday", and yearly, with skipped and moved dates.

All-day events show the date only, and events in progress count as upcoming.
The result is cached for 5 minutes.

## 2. Run the server

```sh
cp .env.example .env      # set VIKUNJA_URL, VIKUNJA_TOKEN, VIKUNJA_PROJECT_ID
docker compose up -d --build
```

Without Docker: `set -a; . ./.env; set +a; python3 server/kindlehome.py`
(Python ≥ 3.9). A systemd unit is in [deploy/](deploy/kindlehome.service).

Open `http://<server>:8080/` in a desktop browser to check that it works.

| URL          | What                                          |
|--------------|-----------------------------------------------|
| `/`          | dashboard (or the default project / picker)   |
| `/s/<id>`    | KitchenOwl shopping list `<id>`               |
| `/shop`      | the configured/default shopping list          |
| `/shop/lists`| pick one of the household's shopping lists    |
| `/p/<id>`    | the todo page for project `<id>`              |
| `/t/<id>`    | detail page of task `<id>`                    |
| `/projects`  | project picker                                |
| `/healthz`   | health check                                  |

All settings (sort order, font size, language `en`/`de`, refresh interval,
access key, …) are documented in [.env.example](.env.example).

**Security:** anyone who can reach the server can view and edit the
project's tasks. Keep it on your LAN, or set `ACCESS_KEY`. Do not expose it to
the internet over plain HTTP.

## 3. Set up the Kindle

Prerequisites: jailbreak + [KUAL](https://www.mobileread.com/forums/showthread.php?t=203326)
installed (as for any KUAL extension).

1. Connect the Kindle via USB and copy `kindle/extensions/kindlehome/` to
   `extensions/kindlehome/` on the Kindle drive (i.e. `/mnt/us/extensions/kindlehome`).
2. Edit `extensions/kindlehome/config.sh` and set `KV_URL` to your server, e.g.
   `http://192.168.1.10:8080/p/12` (add `?key=…` if you set `ACCESS_KEY`).
   Use the IP address or a name the Kindle can resolve. Use `http://`, not https.
3. Eject the Kindle, open KUAL → **KindleHome → Start todo display**.

What *Start* does:

- disables the screensaver/sleep (`preventScreenSaver`)
- opens the page in the browser
- starts a small watchdog. It keeps the screensaver off, turns wifi back on
  if it dropped, and reopens the page every `KV_RELAUNCH_HOURS`. Reopening
  frees browser memory and also restarts the browser if it crashed.

*Stop* re-enables sleep and returns to the home screen. *Reload page* reopens
the URL right away. A log is written to `/tmp/kindlehome.log` on the Kindle.

After a reboot, run *Start todo display* again (it is not started automatically).

### Usage tips

- Tap a task's text (not the box) to open its details. `¶` in the list means the
  task has a description, and `1/3` means 1 of 3 subtasks or checklist items are done. Tap the
  project name at the top to go back. The detail page also goes back to the
  list on its own after 5 minutes without input (`DETAIL_RETURN_SECONDS`).
  To show subtasks nested in the main list as well, set `LIST_SUBTASKS=1`.
- When you tap into "New task…", chips appear under the field. *Today*,
  *Tomorrow* and *Weekend* (the coming Saturday) set a due date. The first
  names of everyone with access to the project (owner, shared users, team
  members) assign the task, and several can be picked. Tap a chip again to
  deselect it. Chip due dates use the time `QUICK_DUE_TIME` (23:59), so a
  task due "today" isn't overdue at noon.
- Tap a box to tick a task off. It is struck through right away and
  disappears at the next sync. Tap again before then to undo.
- Tap the text field on the main page to add a task with the on-screen
  keyboard. To add a subtask, open the task and use the field at the bottom.
  It adds a checklist item to the task's description.
- On a detail page, text with a dotted underline can be edited: tap the
  title, a subtask or a checklist item, change it, then tap *Save*. Open a
  subtask with the `›` arrow, and tick checklist items with their box.
- Tapping the task's title on its detail page also shows the chips: the
  current due date and assignees are selected, and you can change them before
  tapping *Save*. *No date* removes the due date. If the due date is not one of
  the chips, it stays as it is unless you pick one. To change a subtask's due
  date or assignees, open the subtask first.
- Auto-refresh pauses while you type or right after you tapped something.
- *Refresh* in the header reloads the whole page, which also clears e-ink
  ghosting. The page does a full reload on its own every 6 hours as well;
  with a dashboard, that reload goes back to the dashboard.
- **Fullscreen (experimental):** KUAL → *Start fullscreen (experimental)*
  shows the page in a small Kindle web app of its own (`kindle/extensions/kindlehome/app/`)
  instead of the browser. It is the same mechanism the Kindle store and apps
  like Komic use, and it has no toolbar. To do this, Start registers the app
  in the Kindle's app registry (`/var/local/appreg.db`). KUAL → *Uninstall
  fullscreen app* removes the entry again.
  - Use the page's *Exit* link (only shown in the app) to get back to the
    Kindle home screen, then KUAL → *Stop* to end the display.
  - If the app closes without *Exit* (a crash), the watchdog opens it again
    within a minute. It saves what the system logged at that moment to
    `kindlehome-crash.txt` on the Kindle drive (the one before is kept as
    `kindlehome-crash.prev.txt`), and *Collect diagnostics* includes it too.
    The app also closes when someone taps the status bar at the top and then
    the toolbar's search bar, which opens the Kindle home screen. The report
    lists the last taps and marks that case as "not a crash". Warnings, errors,
    popups and app exits from the system log are also copied every 5 minutes
    to `kindlehome-events.txt` (the system log itself is rotated often).
    Once an hour the log also records the battery level and
    memory use.
  - If the app stays blank, set `KV_APP_MODE=top` in `config.sh` and start again.
  - If it does not work at all, use the normal *Start todo display* (browser).
  - `KV_HIDE_BARS=1` hides the browser's top bar instead, but on firmware
    5.6 this breaks the browser.
- Long lists get page up/down buttons at the bottom of the screen. They are
  quicker than drag scrolling on e-ink.
- To change the font size, set `FONT_SIZE` on the server (default 24px).

### Front light

The front light follows touches by default. Touching the screen turns it on
(`KV_LIGHT_ON`, 18 of 24 = 75%), and it goes off after `KV_LIGHT_TIMEOUT`
seconds (60) without a touch. A background script on the Kindle does this. It
reads the touchscreen's input events alongside the Kindle's own software, so
the touch that wakes the light also counts as a normal tap on the page. Set
`KV_LIGHT_AUTO=0` in `config.sh` to turn this off, and `KV_FRONTLIGHT` for a
fixed level instead. While you type into a page, the page tells the server
every 20 seconds, and the light stays on even if keyboard taps don't reach
the script (it asks the server with `wget` before turning off). If the light doesn't react, *Collect diagnostics* lists
the input devices, and `KV_TOUCH_DEV` can name the right one.

### 24/7 notes

- Keep it on the charger. The wifi and the front light drain the battery
  within a day or two.
- A Li-ion battery that sits at 100% all the time ages faster. Check it now
  and then for swelling.
- `KV_FRONTLIGHT` in `config.sh` sets the front light level while the
  display runs. `0` means off, which is fine in a bright room.

## Upgrading from kindle-vikunja

The project used to be called *kindle-vikunja*. To switch an existing setup:

- **Server (Docker):** the service is now called `kindlehome`. Run
  `docker compose up -d --build --remove-orphans`, so the old
  `kindle-vikunja` container is removed and port 8080 is free.
- **Kindle:**
  1. In KUAL, run the old extension's *Stop* and *Uninstall fullscreen app*.
  2. Delete `extensions/vikunja/`.
  3. Copy `kindle/extensions/kindlehome/` to `extensions/kindlehome/`.
  4. Your old `config.sh` can be copied over unchanged, since the setting
     names stayed the same.

## Development

```sh
python3 -m unittest discover tests -v
```

The tests run the server against an in-memory fake of the Vikunja API. They
cover tree building, ticking off (the full task object is sent back, because
Vikunja's update overwrites every field), adding tasks and subtasks, and the
access key.

Main file: [server/kindlehome.py](server/kindlehome.py). The page's
JavaScript is plain ES5 on purpose, because the Kindle browser is old.
