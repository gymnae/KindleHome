# kindle-vikunja

Turn a jailbroken Kindle (built for the Paperwhite 1) into a wall-mounted,
touch-enabled todo list for one project of a self-hosted
[Vikunja](https://vikunja.io).

- Quick overview of the open tasks in a project, sorted by due date (or priority, position…)
- Tap the box to tick a task off (or tap again to undo)
- Subtasks are shown nested under their parent and can be ticked off, including done ones
- Tap a task's text to open its detail page, with the description, its subtasks (tickable,
  tappable to go deeper), its parent task and a field to add a subtask
- Add new tasks, optionally as a subtask of an existing task
- Due dates (overdue ones shown inverted), priority (`!`–`!!!`), assignees (in *bold italics*),
  labels, subtask progress
- Re-syncs automatically, but only redraws when something changed (saves e-ink flashes)

```
┌──────────┐  plain HTTP (LAN)  ┌────────────────┐  HTTPS + API token  ┌─────────┐
│  Kindle  │ ─────────────────▶ │ kindle-vikunja │ ──────────────────▶ │ Vikunja │
│ browser  │  simple HTML/ES5   │  (this server) │     /api/v1         │         │
└──────────┘                    └────────────────┘                     └─────────┘
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
| tasks_relations  | create (needed only for adding subtasks)    |

The permission names in the UI differ a bit between versions. If anything
fails with a 401/403, pick all permissions in these groups. Choose a long
expiry date, because the display stops working when the token expires.

The project ID is the number in the URL of the project in Vikunja
(`…/projects/12/…` → `12`).

## 2. Run the server

```sh
cp .env.example .env      # set VIKUNJA_URL, VIKUNJA_TOKEN, VIKUNJA_PROJECT_ID
docker compose up -d --build
```

Without Docker: `set -a; . ./.env; set +a; python3 server/kindle_vikunja.py`
(Python ≥ 3.9). A systemd unit is in [deploy/](deploy/kindle-vikunja.service).

Open `http://<server>:8080/` in a desktop browser to check that it works.

| URL          | What                                          |
|--------------|-----------------------------------------------|
| `/`          | redirects to the default project (or picker)  |
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

1. Connect the Kindle via USB and copy `kindle/extensions/vikunja/` to
   `extensions/vikunja/` on the Kindle drive (i.e. `/mnt/us/extensions/vikunja`).
2. Edit `extensions/vikunja/config.sh` and set `KV_URL` to your server, e.g.
   `http://192.168.1.10:8080/p/12` (add `?key=…` if you set `ACCESS_KEY`).
   Use the IP address or a name the Kindle can resolve. Use `http://`, not https.
3. Eject the Kindle, open KUAL → **Vikunja Todo → Start todo display**.

What *Start* does:

- disables the screensaver/sleep (`preventScreenSaver`)
- opens the page in the browser
- starts a small watchdog. It keeps the screensaver off, turns wifi back on
  if it dropped, and reopens the page every `KV_RELAUNCH_HOURS`. Reopening
  frees browser memory and also restarts the browser if it crashed.

*Stop* re-enables sleep and returns to the home screen. *Reload page* reopens
the URL right away. A log is written to `/tmp/kindle-vikunja.log` on the Kindle.

After a reboot, run *Start todo display* again (it is not started automatically).

### Usage tips

- Tap a task's text (not the box) to open its details. `¶` in the list means the
  task has a description, and `1/3` means 1 of 3 subtasks are done. Tap the
  project name at the top to go back. The detail page also goes back to the
  list on its own after 5 minutes without input (`DETAIL_RETURN_SECONDS`).
  If the list gets too long, set `LIST_SUBTASKS=0` to show subtasks only on
  the detail page.
- Tap a box to tick a task off. It is struck through right away and
  disappears at the next sync. Tap again before then to undo.
- Tap the text field to add a task with the on-screen keyboard. Choose a
  parent under *Subtask of* to add a subtask.
- Auto-refresh pauses while you type or right after you tapped something.
- *Refresh* in the header reloads the whole page, which also clears e-ink
  ghosting. The page does a full reload on its own every 6 hours as well.
- **Fullscreen (experimental):** KUAL → *Start fullscreen (experimental)*
  shows the page in a small Kindle web app of its own (`kindle/extensions/vikunja/app/`)
  instead of the browser. It is the same mechanism the Kindle store and apps
  like Komic use, and it has no toolbar. To do this, Start registers the app
  in the Kindle's app registry (`/var/local/appreg.db`). KUAL → *Uninstall
  fullscreen app* removes the entry again.
  - Use the page's *Exit* link (only shown in the app) to get back to the
    Kindle home screen, then KUAL → *Stop* to end the display.
  - If the app stays blank, set `KV_APP_MODE=top` in `config.sh` and start again.
  - If it does not work at all, use the normal *Start todo display* (browser).
  - `KV_HIDE_BARS=1` hides the browser's top bar instead, but on firmware
    5.6 this breaks the browser.
- Long lists get page up/down buttons at the bottom of the screen. They are
  quicker than drag scrolling on e-ink.
- To change the font size, set `FONT_SIZE` on the server (default 24px).

### 24/7 notes

- Keep it on the charger. The wifi and the front light drain the battery
  within a day or two.
- A Li-ion battery that sits at 100% all the time ages faster. Check it now
  and then for swelling.
- `KV_FRONTLIGHT` in `config.sh` sets the front light level while the
  display runs. `0` means off, which is fine in a bright room.

## Development

```sh
python3 -m unittest discover tests -v
```

The tests run the server against an in-memory fake of the Vikunja API. They
cover tree building, ticking off (the full task object is sent back, because
Vikunja's update overwrites every field), adding tasks and subtasks, and the
access key.

Main file: [server/kindle_vikunja.py](server/kindle_vikunja.py). The page's
JavaScript is plain ES5 on purpose, because the Kindle browser is old.
