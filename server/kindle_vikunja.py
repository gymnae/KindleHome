#!/usr/bin/env python3
"""
kindle-vikunja: a tiny, dependency-free web frontend that shows one Vikunja
project as an e-ink friendly todo list.

Built for the experimental browser of a jailbroken Kindle, but works in any
browser. The Kindle never talks to Vikunja directly: this server holds the API
token, talks (HTTPS) to Vikunja and serves plain, old-WebKit-compatible HTML.

Configuration is done with environment variables, see .env.example.
"""

import datetime as dt
import hmac
import html
import json
import os
import re
import base64
import ssl
import struct
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from http import cookies
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9
    ZoneInfo = None

NULL_DATE_PREFIX = "0001-01-01"  # Vikunja's "no date"
MAX_DEPTH = 4  # subtask nesting shown on screen
PAGE_LIMIT = 50  # safety net for pagination loops

STRINGS = {
    "en": {
        "today": "today", "tomorrow": "tomorrow", "yesterday": "yesterday",
        "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "new_task": "New task…", "add": "Add", "save": "Save", "cancel": "Cancel",
        "empty": "Nothing to do.", "projects": "Projects",
        "refresh": "Refresh", "error": "Error", "offline": "offline", "exit": "Exit",
        "forbidden": "Access key missing or wrong.",
        "pick": "Choose a project",
        "back": "Back", "subtasks": "Subtasks", "parent": "Part of",
        "new_subtask": "New subtask…",
        "weekend": "Weekend", "calendar": "Upcoming", "no_events": "No upcoming events.",
        "home": "Home", "recent": "Recently bought", "new_item": "Add item…", "other": "Other",
        "open_count": "{n} open", "item_count": "{n} items", "more": "+{n} more",
        "list_empty": "The shopping list is empty.", "not_configured": "{name} is not configured.",
    },
    "de": {
        "today": "heute", "tomorrow": "morgen", "yesterday": "gestern",
        "weekdays": ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"],
        "new_task": "Neue Aufgabe…", "add": "Neu", "save": "Speichern", "cancel": "Abbrechen",
        "empty": "Nichts zu tun.", "projects": "Projekte",
        "refresh": "Aktualisieren", "error": "Fehler", "offline": "offline", "exit": "Beenden",
        "forbidden": "Zugangsschlüssel fehlt oder ist falsch.",
        "pick": "Projekt wählen",
        "back": "Zurück", "subtasks": "Unteraufgaben", "parent": "Teil von",
        "new_subtask": "Neue Unteraufgabe…",
        "weekend": "Wochenende", "calendar": "Termine", "no_events": "Keine Termine.",
        "home": "Start", "recent": "Zuletzt gekauft", "new_item": "Artikel hinzufügen…", "other": "Sonstiges",
        "open_count": "{n} offen", "item_count": "{n} Artikel", "more": "+{n} weitere",
        "list_empty": "Die Einkaufsliste ist leer.", "not_configured": "{name} ist nicht eingerichtet.",
    },
}


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

class Config:
    def __init__(self, env=None):
        env = os.environ if env is None else env
        url = env.get("VIKUNJA_URL", "").strip().rstrip("/")
        if url and not url.endswith("/api/v1"):
            url += "/api/v1"
        self.api_url = url
        self.token = env.get("VIKUNJA_TOKEN", "").strip()
        # one or more project ids ("3" or "3,5"); the first one is the default
        self.projects = [p.strip() for p in env.get("VIKUNJA_PROJECT_ID", "").split(",") if p.strip()]
        self.default_project = self.projects[0] if self.projects else ""
        self.vikunja = bool(self.api_url and self.token)

        ko_url = env.get("KITCHENOWL_URL", "").strip().rstrip("/")
        if ko_url and not ko_url.endswith("/api"):
            ko_url += "/api"
        self.ko_url = ko_url
        self.ko_token = env.get("KITCHENOWL_TOKEN", "").strip()
        self.ko_household = env.get("KITCHENOWL_HOUSEHOLD_ID", "").strip()
        self.ko_list = env.get("KITCHENOWL_LIST_ID", "").strip()
        self.ko_recent = max(0, min(120, int(env.get("KITCHENOWL_RECENT", "12"))))
        self.kitchenowl = bool(self.ko_url and self.ko_token)

        # CalDAV calendar(s) or iCalendar (.ics) feeds: upcoming events on the dashboard
        self.caldav_urls = [u.replace("webcal://", "https://", 1)
                            for u in re.split(r"[\s,]+", env.get("CALDAV_URL", "").strip()) if u]
        self.caldav_user = env.get("CALDAV_USER", "")
        self.caldav_password = env.get("CALDAV_PASSWORD", "")
        self.caldav_title = env.get("CALDAV_TITLE", "").strip()
        self.caldav_events = max(1, int(env.get("CALDAV_EVENTS", "3")))
        self.caldav_days = max(1, int(env.get("CALDAV_DAYS", "30")))
        self.caldav_verify_tls = env.get("CALDAV_VERIFY_TLS", "1").lower() not in ("0", "false", "no")
        self.caldav = bool(self.caldav_urls)

        self.dashboard = (env.get("DASHBOARD", "auto").strip().lower() in ("1", "yes", "true")
                          or (env.get("DASHBOARD", "auto").strip().lower() == "auto"
                              and (self.kitchenowl or self.caldav or len(self.projects) > 1)))
        self.dashboard_after = int(env.get("DASHBOARD_RETURN_SECONDS", "0"))
        self.host = env.get("LISTEN_HOST", "0.0.0.0")
        self.port = int(env.get("LISTEN_PORT", "8080"))
        self.access_key = env.get("ACCESS_KEY", "").strip()
        self.refresh = max(10, int(env.get("REFRESH_SECONDS", "60")))
        # "position" = the manual (drag and drop) order of Vikunja's list view
        self.sort = env.get("SORT", "position").strip().lower()
        self.font_size = int(env.get("FONT_SIZE", "24"))
        # time of day for due dates set with the Today/Tomorrow/Weekend chips
        hh, _, mm = env.get("QUICK_DUE_TIME", "23:59").partition(":")
        self.quick_due_time = dt.time(int(hh), int(mm or 0))
        self.dash_font_size = int(env.get("DASHBOARD_FONT_SIZE", "28"))
        # The Kindle has no emoji font: "image" (Twemoji pictures via this server) | "strip" | "keep"
        self.emoji = env.get("EMOJI", "image").strip().lower()
        self.emoji_url = env.get("EMOJI_URL", "").strip() or (
            "https://cdn.jsdelivr.net/gh/jdecked/twemoji@15.1.0/assets/72x72/{code}.png")
        self.lang = env.get("UI_LANG", "en").strip().lower()
        self.list_subtasks = env.get("LIST_SUBTASKS", "0").lower() not in ("0", "false", "no")
        self.home_after = int(env.get("DETAIL_RETURN_SECONDS", "300"))
        self.timeout = float(env.get("VIKUNJA_TIMEOUT", "15"))
        self.verify_tls = env.get("VIKUNJA_VERIFY_TLS", "1").lower() not in ("0", "false", "no")
        self.ko_verify_tls = env.get("KITCHENOWL_VERIFY_TLS", "1").lower() not in ("0", "false", "no")
        # Item icons: KitchenOwl's icon font (Icons8, not redistributable) is fetched from your own
        # instance at runtime; the name -> character map from KitchenOwl's source.
        self.ko_icons = env.get("KITCHENOWL_ICONS", "1").lower() not in ("0", "false", "no")
        self.ko_icon_font_url = (env.get("KITCHENOWL_ICON_FONT_URL", "").strip()
                                 or (ko_url[:-len("/api")] + "/assets/fonts/Items.ttf" if ko_url else ""))
        self.ko_icon_map_url = env.get("KITCHENOWL_ICON_MAP_URL", "").strip() or (
            "https://raw.githubusercontent.com/TomBursch/kitchenowl/main/kitchenowl/lib/item_icons.dart")


# --------------------------------------------------------------------------
# API clients
# --------------------------------------------------------------------------

class ApiError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


VikunjaError = ApiError


class JsonApi:
    """Minimal JSON-over-HTTP client with bearer token auth."""

    name = "API"

    def __init__(self, base_url, token, timeout, verify_tls):
        self.base_url, self.token, self.timeout = base_url, token, timeout
        self._ctx = None if verify_tls else ssl._create_unverified_context()

    def request(self, method, path, params=None, body=None):
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as resp:
                raw = resp.read()
                return (json.loads(raw) if raw else None), resp.headers
        except urllib.error.HTTPError as e:
            msg = e.reason
            try:
                err = json.loads(e.read())
                msg = err.get("message") or err.get("msg") or msg
            except Exception:
                pass
            raise ApiError(f"{self.name} {method} {path}: {e.code} {msg}", e.code) from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise ApiError(f"Cannot reach {self.name}: {e}") from None


class Vikunja(JsonApi):
    name = "Vikunja"

    def __init__(self, cfg):
        super().__init__(cfg.api_url, cfg.token, cfg.timeout, cfg.verify_tls)
        self._titles = {}  # project id -> (title, fetched at)
        self._users = {}   # project id -> (users, fetched at)

    def project_title(self, project_id):
        """Project title, cached for 10 minutes (used by the dashboard on every refresh)."""
        cached = self._titles.get(str(project_id))
        if cached and time.time() - cached[1] < 600:
            return cached[0]
        title = self.project(project_id).get("title") or ""
        self._titles[str(project_id)] = (title, time.time())
        return title

    def get_all(self, path, params=None):
        """GET a paginated collection and return all items."""
        params = dict(params or {})
        params["per_page"] = 50
        items, page = [], 1
        while True:
            params["page"] = page
            data, headers = self.request("GET", path, params)
            data = data or []
            items.extend(data)
            total = int(headers.get("x-pagination-total-pages") or 1)
            if page >= total or not data or page >= PAGE_LIMIT:
                return items
            page += 1

    def projects(self):
        return self.get_all("/projects")

    def project(self, project_id):
        return self.request("GET", f"/projects/{project_id}")[0]

    def open_tasks(self, project_id):
        """All undone tasks of a project (with related_tasks populated)."""
        try:
            views = self.request("GET", f"/projects/{project_id}/views")[0] or []
        except VikunjaError as e:
            if e.status not in (400, 404, 405):
                raise
            views = None  # Vikunja < 0.24 has no project views

        if views is not None:
            # list and table views return plain tasks; kanban would return buckets
            for kind in ("list", "table"):
                view = next((v for v in views if v.get("view_kind") == kind), None)
                if view:
                    # sorting by position makes Vikunja return each task's position in this view
                    items = self.get_all(f"/projects/{project_id}/views/{view['id']}/tasks",
                                         {"filter": "done = false", "sort_by": ["position", "id"],
                                          "order_by": ["asc", "asc"]})
                    return _flatten_buckets(items)
            return self.get_all("/tasks/all", {"filter": f"done = false && project = {int(project_id)}"})

        return self.get_all(f"/projects/{project_id}/tasks", {
            "filter_by": "done", "filter_value": "false", "filter_comparator": "equals"})

    def task(self, task_id):
        return self.request("GET", f"/tasks/{task_id}")[0]

    def _update(self, task_id, change):
        # Vikunja's update overwrites every field, so always send back the full task.
        task = self.request("GET", f"/tasks/{task_id}")[0]
        if change(task) is not False:
            self.request("POST", f"/tasks/{task_id}", body=task)

    def _change_description(self, task_id, change):
        def apply(task):
            desc = task.get("description") or ""
            new = change(desc)
            if new is None:
                raise VikunjaError("Checklist item not found (description changed?), reload the page", 409)
            if new == desc:
                return False
            task["description"] = new
        self._update(task_id, apply)

    def set_check(self, task_id, index, done):
        """Tick/untick the index-th checklist item ("- [ ]") of a task's description."""
        self._change_description(task_id, lambda d: toggle_checklist(d, index, done))

    def set_check_text(self, task_id, index, text):
        self._change_description(task_id, lambda d: set_checklist_text(d, index, text))

    def add_check(self, task_id, text):
        """Add a "- [ ] text" checklist item (Vikunja-style subtask in the description)."""
        self._change_description(task_id, lambda d: append_checklist_item(d, text))

    def rename(self, task_id, title):
        self._update(task_id, lambda task: task.__setitem__("title", title))

    def set_done(self, task_id, done):
        self._update(task_id, lambda task: task.__setitem__("done", bool(done)))

    def project_users(self, project_id):
        """Everyone with access to a project: owner, shared users and team members.

        Each source is asked separately (tokens may lack the permission for some of them); a
        complete result is cached for 10 minutes, an incomplete one for 1 minute."""
        cached = self._users.get(str(project_id))
        if cached and time.time() - cached[1] < cached[2]:
            return cached[0]
        users, failed = {}, []

        def add(user):
            user = user.get("user") if isinstance(user.get("user"), dict) else user
            if user and user.get("id"):
                users.setdefault(user["id"], user)

        def team_members(team):
            members = team.get("members")
            if members is None:
                members = self.request("GET", f"/teams/{team['id']}")[0].get("members") or []
            return members

        add(self.project(project_id).get("owner") or {})
        sources = (
            ("projectusers", lambda: self.get_all(f"/projects/{project_id}/projectusers")),  # all with access
            ("users", lambda: self.get_all(f"/projects/{project_id}/users")),                # shared with users
            ("teams", lambda: [m for team in self.get_all(f"/projects/{project_id}/teams")
                               for m in team_members(team)]),                                 # via teams
        )
        for name, fetch in sources:
            try:
                for user in fetch():
                    add(user)
            except ApiError as e:
                failed.append(name)
                sys.stderr.write(f"project {project_id}: can't read {name} ({e})\n")
        result = sorted(users.values(), key=lambda u: (_first_name(u).lower(), u["id"]))
        self._users[str(project_id)] = (result, time.time(), 60 if failed else 600)
        return result

    def create_task(self, project_id, title, parent_id=None, due=None, assignees=()):
        body = {"title": title, "project_id": int(project_id)}
        if due:
            body["due_date"] = due.isoformat()
        task = self.request("PUT", f"/projects/{project_id}/tasks", body=body)[0]
        for user_id in assignees:
            self.request("PUT", f"/tasks/{task['id']}/assignees", body={"user_id": int(user_id)})
        if parent_id:
            self.request("PUT", f"/tasks/{parent_id}/relations", body={
                "task_id": int(parent_id), "other_task_id": task["id"], "relation_kind": "subtask"})
        return task


class KitchenOwl(JsonApi):
    """KitchenOwl shopping lists (https://kitchenowl.org), via its REST API."""

    name = "KitchenOwl"

    def __init__(self, cfg):
        super().__init__(cfg.ko_url, cfg.ko_token, cfg.timeout, cfg.ko_verify_tls)
        self.cfg = cfg
        self._household = cfg.ko_household or None

    def household_id(self):
        if not self._household:
            households = self.request("GET", "/household")[0] or []
            if not households:
                raise ApiError("KitchenOwl: this user is not in any household")
            self._household = str(households[0]["id"])
        return self._household

    def shopping_list(self, list_id=None):
        """One list with its "items" and "recentItems" (default: configured or household default list)."""
        lists = self.request("GET", f"/household/{self.household_id()}/shoppinglist",
                             {"recent_limit": max(1, self.cfg.ko_recent)})[0] or []
        if not lists:
            raise ApiError("KitchenOwl: the household has no shopping list")
        wanted = str(list_id or self.cfg.ko_list or "")
        if wanted:
            found = [lst for lst in lists if str(lst.get("id")) == wanted]
            if not found:
                raise ApiError(f"KitchenOwl: shopping list {wanted} not found", 404)
            lst = found[0]
        else:
            lst = min(lists, key=lambda lst: lst.get("id", 0))  # KitchenOwl's default list
        # older KitchenOwl versions don't embed the items
        if "items" not in lst:
            lst["items"] = self.request("GET", f"/shoppinglist/{lst['id']}/items")[0] or []
        if "recentItems" not in lst:
            lst["recentItems"] = self.request("GET", f"/shoppinglist/{lst['id']}/recent-items",
                                              {"limit": max(1, self.cfg.ko_recent)})[0] or []
        if not self.cfg.ko_recent:
            lst["recentItems"] = []
        return lst

    def search(self, query):
        """Item suggestions for a typed text (KitchenOwl splits off amounts into "description")."""
        return self.request("GET", f"/household/{self.household_id()}/item/search", {"query": query})[0] or []

    def add(self, list_id, name):
        return self.request("POST", f"/shoppinglist/{list_id}/add-item-by-name", body={"name": name})[0]

    def remove(self, list_id, item_id):
        """Tick an item off (KitchenOwl moves it to the recent items)."""
        self.request("DELETE", f"/shoppinglist/{list_id}/item",
                     body={"item_id": int(item_id), "removed_at": int(time.time() * 1000)})

    def set_description(self, list_id, item_id, description):
        """Set an item's description; also (re-)adds the item to the list if it isn't on it."""
        self.request("POST", f"/shoppinglist/{list_id}/item/{item_id}", body={"description": description})


# --------------------------------------------------------------------------
# Calendar (CalDAV / iCalendar)
# --------------------------------------------------------------------------

_WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def _local_time(naive):
    """Attach the local time zone (TZ, with DST rules when available)."""
    tz = os.environ.get("TZ", "")
    if ZoneInfo and tz:
        try:
            return naive.replace(tzinfo=ZoneInfo(tz))
        except Exception:  # noqa: BLE001
            pass
    return naive.astimezone()


def _ical_lines(text):
    lines = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]  # unfold continuation lines
        elif line:
            lines.append(line)
    return lines


def _ical_prop(line):
    """("NAME", {params}, "value") of a content line (colons in quoted params allowed)."""
    quoted = False
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == ":" and not quoted:
            head, value = line[:i], line[i + 1:]
            break
    else:
        return None
    name, *params = head.split(";")
    return name.upper(), {k.upper(): v.strip('"') for k, _, v in (p.partition("=") for p in params)}, value


def _ical_text(value):
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), value)


def _ical_time(value, params):
    """(aware datetime, all_day) of a DATE or DATE-TIME value."""
    v = value.strip()
    if params.get("VALUE") == "DATE" or (len(v) == 8 and v.isdigit()):
        return _local_time(dt.datetime(int(v[:4]), int(v[4:6]), int(v[6:8]))), True
    t = dt.datetime.strptime(v[:15], "%Y%m%dT%H%M%S")
    if v.endswith("Z"):
        return t.replace(tzinfo=dt.timezone.utc), False
    if params.get("TZID") and ZoneInfo:
        try:
            return t.replace(tzinfo=ZoneInfo(params["TZID"])), False
        except Exception:  # noqa: BLE001 - unknown (e.g. Windows) zone names: use local time
            pass
    return _local_time(t), False


def _ical_duration(value):
    m = re.fullmatch(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value.strip())
    if not m:
        return dt.timedelta(0)
    sign, w, d, h, mi, sec = m.groups()
    delta = dt.timedelta(weeks=int(w or 0), days=int(d or 0), hours=int(h or 0), minutes=int(mi or 0),
                         seconds=int(sec or 0))
    return -delta if sign == "-" else delta


def parse_events(ics):
    """VEVENTs of an iCalendar text as dicts (start/end aware datetimes, all_day, rrule, ...)."""
    events, cur, nested = [], None, 0
    for line in _ical_lines(ics):
        prop = _ical_prop(line)
        if not prop:
            continue
        name, params, value = prop
        if name == "BEGIN":
            if value.upper() == "VEVENT" and cur is None:
                cur = {"exdates": set(), "summary": "", "uid": "", "rrule": None, "recurrence_id": None}
            elif cur is not None:
                nested += 1  # e.g. VALARM
            continue
        if name == "END" and cur is not None:
            if nested:
                nested -= 1
            elif value.upper() == "VEVENT":
                if "start" in cur and cur.get("status") != "CANCELLED":
                    if "end" not in cur:
                        cur["end"] = cur["start"] + cur.pop("duration", dt.timedelta(days=1 if cur["all_day"] else 0))
                    events.append(cur)
                cur = None
            continue
        if cur is None or nested:
            continue
        if name == "SUMMARY":
            cur["summary"] = _ical_text(value)
        elif name == "UID":
            cur["uid"] = value
        elif name == "STATUS":
            cur["status"] = value.upper()
        elif name == "DTSTART":
            cur["start"], cur["all_day"] = _ical_time(value, params)
        elif name == "DTEND":
            cur["end"] = _ical_time(value, params)[0]
        elif name == "DURATION":
            cur["duration"] = _ical_duration(value)
        elif name == "RRULE":
            cur["rrule"] = {k.upper(): v for k, _, v in (p.partition("=") for p in value.split(";"))}
        elif name == "EXDATE":
            cur["exdates"].update(_ical_time(v, params)[0] for v in value.split(",") if v)
        elif name == "RECURRENCE-ID":
            cur["recurrence_id"] = _ical_time(value, params)[0]
    return events


def _nth_weekday(year, month, weekday, n):
    """Day of the n-th (negative: from the end) weekday of a month, or None."""
    first = dt.date(year, month, 1)
    days = (dt.date(year + month // 12, month % 12 + 1, 1) - first).days
    if n > 0:
        day = 1 + (weekday - first.weekday()) % 7 + (n - 1) * 7
    else:
        last_wd = (first.weekday() + days - 1) % 7
        day = days - (last_wd - weekday) % 7 + (n + 1) * 7
    return day if 1 <= day <= days else None


def _rrule_starts(start, rule, horizon, limit=3000):
    """Start times of a recurring event up to horizon (FREQ DAILY/WEEKLY/MONTHLY/YEARLY with
    INTERVAL, COUNT, UNTIL, BYDAY, BYMONTHDAY - the common cases)."""
    freq, interval = rule.get("FREQ", ""), max(1, int(rule.get("INTERVAL", "1") or 1))
    count = int(rule["COUNT"]) if rule.get("COUNT", "").isdigit() else None
    until = None
    if rule.get("UNTIL"):
        until, all_day = _ical_time(rule["UNTIL"], {})
        if all_day:
            until += dt.timedelta(days=1) - dt.timedelta(seconds=1)
    byday = [(int(m.group(1)) if m.group(1) else None, _WEEKDAYS[m.group(2)])
             for m in re.finditer(r"([+-]?\d+)?(MO|TU|WE|TH|FR|SA|SU)", rule.get("BYDAY", ""))]
    bymonthday = [int(d) for d in rule.get("BYMONTHDAY", "").split(",") if d.lstrip("-").isdigit()]
    found = 0
    for k in range(limit):
        if freq == "DAILY":
            cands = [start + dt.timedelta(days=k * interval)]
        elif freq == "WEEKLY":
            week = start - dt.timedelta(days=start.weekday()) + dt.timedelta(weeks=k * interval)
            cands = [week + dt.timedelta(days=wd) for wd in sorted({wd for _, wd in byday} or {start.weekday()})]
        elif freq == "MONTHLY":
            month0 = start.month - 1 + k * interval
            year, month = start.year + month0 // 12, month0 % 12 + 1
            if byday:
                days = [_nth_weekday(year, month, wd, n or 1) for n, wd in byday]
            else:
                days = bymonthday or [start.day]
            cands = []
            for day in sorted(d for d in days if d):
                try:
                    cands.append(start.replace(year=year, month=month, day=day))
                except ValueError:
                    pass  # e.g. the 31st in a short month
        elif freq == "YEARLY":
            try:
                cands = [start.replace(year=start.year + k * interval)]
            except ValueError:  # 29 Feb
                cands = []
        else:
            return
        for s in cands:
            if s < start:
                continue
            if (until and s > until) or (count is not None and found >= count) or s > horizon:
                return
            found += 1
            yield s


def event_occurrences(event, range_start, range_end):
    """(start, end) of an event's occurrences overlapping the range."""
    duration = event["end"] - event["start"]
    starts = _rrule_starts(event["start"], event["rrule"], range_end) if event["rrule"] else [event["start"]]
    for s in starts:
        if s in event["exdates"]:
            continue
        e = s + duration
        if s < range_end and (e > range_start if duration else s >= range_start):
            yield s, e


class Calendar:
    """Upcoming events from CalDAV calendars or .ics URLs (cached for 5 minutes)."""

    CALDAV = "urn:ietf:params:xml:ns:caldav"

    def __init__(self, cfg):
        self.cfg = cfg
        self._ctx = None if cfg.caldav_verify_tls else ssl._create_unverified_context()
        self._cache = (None, 0)

    def _request(self, method, url, body=None, headers=None):
        headers = dict(headers or {}, **{"User-Agent": "kindle-vikunja"})
        if self.cfg.caldav_user:
            auth = f"{self.cfg.caldav_user}:{self.cfg.caldav_password}".encode()
            headers["Authorization"] = "Basic " + base64.b64encode(auth).decode()
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=self.cfg.timeout, context=self._ctx) as resp:
            return resp.read().decode("utf-8", "replace")

    def _fetch(self, url, start, end):
        """iCalendar texts for a URL: a CalDAV calendar-query (with server-side expansion of
        recurring events), or else a plain GET of an .ics file."""
        fmt = "%Y%m%dT%H%M%SZ"
        span = f'start="{start.astimezone(dt.timezone.utc):{fmt}}" end="{end.astimezone(dt.timezone.utc):{fmt}}"'
        query = (
            '<?xml version="1.0" encoding="utf-8"?>'
            f'<C:calendar-query xmlns:D="DAV:" xmlns:C="{self.CALDAV}">'
            f'<D:prop><C:calendar-data><C:expand {span}/></C:calendar-data></D:prop>'
            '<C:filter><C:comp-filter name="VCALENDAR"><C:comp-filter name="VEVENT">'
            f'<C:time-range {span}/></C:comp-filter></C:comp-filter></C:filter></C:calendar-query>'
        )
        try:
            xml = self._request("REPORT", url, query.encode(),
                                {"Depth": "1", "Content-Type": "application/xml; charset=utf-8"})
            root = ET.fromstring(xml)
            return [el.text or "" for el in root.iter(f"{{{self.CALDAV}}}calendar-data")]
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise ApiError(f"Calendar {url}: {e.code} {e.reason} (check CALDAV_USER/CALDAV_PASSWORD)", e.code)
            if e.code not in (400, 404, 405, 406, 415, 501):
                raise ApiError(f"Calendar {url}: {e.code} {e.reason}", e.code)
        except ET.ParseError:
            pass
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise ApiError(f"Cannot reach calendar {url}: {e}") from None
        try:  # not a CalDAV collection: plain iCalendar file
            ics = self._request("GET", url)
        except urllib.error.HTTPError as e:
            raise ApiError(f"Calendar {url}: {e.code} {e.reason}", e.code)
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise ApiError(f"Cannot reach calendar {url}: {e}") from None
        if "BEGIN:VCALENDAR" not in ics:
            raise ApiError(f"Calendar {url}: neither CalDAV nor an iCalendar file")
        return [ics]

    def upcoming(self, now=None):
        """The next CALDAV_EVENTS events (ongoing ones included), sorted by start."""
        cached, at = self._cache
        if cached is not None and now is None and time.time() - at < 300:
            return cached
        now = now or dt.datetime.now().astimezone()
        end = now + dt.timedelta(days=self.cfg.caldav_days)
        events = []
        for url in self.cfg.caldav_urls:
            for ics in self._fetch(url, now, end):
                events.extend(parse_events(ics))
        moved = {(e["uid"], e["recurrence_id"]) for e in events if e["recurrence_id"]}
        found = []
        for event in events:
            for s, e in event_occurrences(event, now, end):
                if not event["recurrence_id"] and (event["uid"], s) in moved:
                    continue  # this occurrence was moved/changed: its own VEVENT is used instead
                found.append({"start": s, "end": e, "all_day": event["all_day"], "summary": event["summary"]})
        found.sort(key=lambda ev: (ev["start"], ev["summary"]))
        result = found[:self.cfg.caldav_events]
        self._cache = (result, time.time())
        return result


def _shop_item_key(item):
    return (item.get("ordering") or 0, (item.get("name") or "").lower())


def ttf_codepoints(data):
    """Set of code points mapped by a TrueType font's cmap table (formats 4 and 12)."""
    num_tables = struct.unpack(">H", data[4:6])[0]
    for i in range(num_tables):
        tag, _, offset, _ = struct.unpack(">4sIII", data[12 + 16 * i: 28 + 16 * i])
        if tag == b"cmap":
            break
    else:
        raise ValueError("no cmap table")
    points = set()
    count = struct.unpack(">H", data[offset + 2: offset + 4])[0]
    for i in range(count):
        _, _, sub_offset = struct.unpack(">HHI", data[offset + 4 + 8 * i: offset + 12 + 8 * i])
        at = offset + sub_offset
        fmt = struct.unpack(">H", data[at: at + 2])[0]
        if fmt == 4:
            segs = struct.unpack(">H", data[at + 6: at + 8])[0] // 2
            ends = struct.unpack(f">{segs}H", data[at + 14: at + 14 + 2 * segs])
            starts = struct.unpack(f">{segs}H", data[at + 16 + 2 * segs: at + 16 + 4 * segs])
            for start, end in zip(starts, ends):
                if start != 0xFFFF:
                    points.update(range(start, end + 1))
        elif fmt == 12:
            groups = struct.unpack(">I", data[at + 12: at + 16])[0]
            for g in range(groups):
                start, end, _ = struct.unpack(">III", data[at + 16 + 12 * g: at + 28 + 12 * g])
                points.update(range(start, end + 1))
    return points


def parse_icon_map(dart_source):
    """{icon name: code point} from KitchenOwl's generated item_icons.dart."""
    consts = {name: int(cp, 0) for name, cp in
              re.findall(r"static const IconData (\w+)\s*=\s*IconData\(\s*(0x[0-9a-fA-F]+|\d+)", dart_source)}
    return {key: consts[ident] for key, ident in re.findall(r'"([^"]+)"\s*:\s*(\w+)\s*,', dart_source)
            if ident in consts}


_EMOJI_BMP_DEFAULT = ("\u231A\u231B\u23E9-\u23EC\u23F0\u23F3\u25FD\u25FE\u2614\u2615\u2648-\u2653\u267F"
                      "\u2693\u26A1\u26AA\u26AB\u26BD\u26BE\u26C4\u26C5\u26CE\u26D4\u26EA\u26F2\u26F3"
                      "\u26F5\u26FA\u26FD\u2705\u270A\u270B\u2728\u274C\u274E\u2753-\u2755\u2757"
                      "\u2795-\u2797\u27B0\u27BF\u2B1B\u2B1C\u2B50\u2B55")
# symbols that are only emoji when followed by the emoji variation selector (U+FE0F)
_EMOJI_BMP_VS16 = ("\u00A9\u00AE\u203C\u2049\u2122\u2139\u2194-\u21AA\u2300-\u23FF\u24C2\u25AA-\u25FE"
                   "\u2600-\u27BF\u2934\u2935\u2B00-\u2BFF\u3030\u303D\u3297\u3299")
_EMOJI_BASE = (f"(?:[\U0001F000-\U0001FAFF]|[{_EMOJI_BMP_DEFAULT}]\uFE0F?|[{_EMOJI_BMP_VS16}]\uFE0F)"
               "\uFE0F?[\U0001F3FB-\U0001F3FF]?")
EMOJI_RE = re.compile(f"[\U0001F1E6-\U0001F1FF]{{2}}|{_EMOJI_BASE}(?:\u200D{_EMOJI_BASE})*")
_TAG_RE = re.compile(r"(<script\b.*?</script>|<style\b.*?</style>|<title>.*?</title>|<[^>]*>)", re.S | re.I)
# 1x1 transparent GIF: shown instead of a broken image when an emoji can't be fetched
BLANK_GIF = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\x00\x00\x00!\xf9\x04\x01\x00\x00\x00"
             b"\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;")


def twemoji_code(seq):
    """Twemoji file name for an emoji sequence (U+FE0F is dropped unless it is a ZWJ sequence)."""
    cps = [ord(c) for c in seq]
    if 0x200D not in cps:
        cps = [c for c in cps if c != 0xFE0F]
    return "-".join(f"{c:x}" for c in cps)


def emojify(page, mode):
    """Replace emoji in the text of an HTML page (not in tags, scripts or attributes)."""
    if mode not in ("image", "strip"):
        return page

    def text(segment):
        if mode == "strip":  # also drop the space after a leading emoji ("🛒 Daily" -> "Daily")
            return re.sub(f"(?:{EMOJI_RE.pattern}) ?", "", segment)
        return EMOJI_RE.sub(lambda m: f'<img class="emo" src="/emoji/{twemoji_code(m.group(0))}.png" '
                                      f'alt="{m.group(0)}">', segment)

    parts = _TAG_RE.split(page)
    return "".join(part if i % 2 else text(part) for i, part in enumerate(parts))


class EmojiCache:
    """Twemoji PNGs fetched on demand and kept in memory (failures retried after 30 min)."""

    def __init__(self, cfg, limit=1000):
        self.cfg, self.limit, self.images = cfg, limit, {}
        self._lock = threading.Lock()

    def get(self, code):
        hit = self.images.get(code)
        if hit is not None and (hit[0] is not None or time.time() - hit[1] < 1800):
            return hit[0]
        try:
            req = urllib.request.Request(self.cfg.emoji_url.format(code=code), headers={"User-Agent": "kindle-vikunja"})
            with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
                data = resp.read()
        except Exception as e:  # noqa: BLE001 - emoji are decoration only
            sys.stderr.write(f"emoji {code} unavailable: {e}\n")
            data = None
        with self._lock:
            if len(self.images) >= self.limit:
                self.images.clear()
            self.images[code] = (data, time.time())
        return data


class IconFont:
    """KitchenOwl item icons, loaded lazily (and retried every 30 min after a failure)."""

    RETRY = 1800

    def __init__(self, cfg):
        self.cfg = cfg
        self.font, self.icons, self._failed_at = None, None, 0
        self._lock = threading.Lock()
        self._ko_ctx = None if cfg.ko_verify_tls else ssl._create_unverified_context()

    def _get(self, url, ctx=None):
        req = urllib.request.Request(url, headers={"User-Agent": "kindle-vikunja"})
        with urllib.request.urlopen(req, timeout=self.cfg.timeout, context=ctx) as resp:
            return resp.read()

    def mapping(self):
        """{icon name: code point}, or None if icons are unavailable."""
        if self.icons is not None or not self.cfg.ko_icons:
            return self.icons
        with self._lock:
            if self.icons is None and time.time() - self._failed_at > self.RETRY:
                self._load()
        return self.icons

    def _load(self):
        try:
            font = self._get(self.cfg.ko_icon_font_url, self._ko_ctx)
            icons = parse_icon_map(self._get(self.cfg.ko_icon_map_url).decode("utf-8", "replace"))
            in_font = {cp for cp in ttf_codepoints(font) if cp >= 0x100}
            # code points are assigned alphabetically: a map from another KitchenOwl version would
            # show wrong icons, so only accept an exact match
            if not icons or set(icons.values()) != in_font:
                raise ValueError(f"icon map ({len(icons)} icons) does not match the font ({len(in_font)} glyphs); "
                                 "set KITCHENOWL_ICON_MAP_URL to item_icons.dart of your KitchenOwl version")
            self.font, self.icons = font, icons
            sys.stderr.write(f"KitchenOwl icons loaded: {len(icons)}\n")
        except Exception as e:  # noqa: BLE001 - icons are optional, never break the page
            self._failed_at = time.time()
            sys.stderr.write(f"KitchenOwl icons unavailable: {e}\n")


def _flatten_buckets(items):
    out = []
    for it in items:
        if isinstance(it, dict) and "tasks" in it and "done" not in it:
            out.extend(it.get("tasks") or [])
        else:
            out.append(it)
    return out


# --------------------------------------------------------------------------
# Task tree / sorting / dates
# --------------------------------------------------------------------------

def parse_date(value):
    if not value or value.startswith(NULL_DATE_PREFIX):
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def sort_key(mode):
    def due(t):
        d = parse_date(t.get("due_date"))
        return (d is None, d.timestamp() if d else 0.0)

    def prio(t):
        return -(t.get("priority") or 0)

    if mode == "priority":
        return lambda t: (prio(t), *due(t), t.get("id", 0))
    if mode == "position":
        return lambda t: (float(t.get("position") or 0), t.get("id", 0))
    if mode == "title":
        return lambda t: ((t.get("title") or "").lower(), t.get("id", 0))
    if mode == "created":
        return lambda t: (t.get("created") or "", t.get("id", 0))
    return lambda t: (*due(t), prio(t), t.get("id", 0))


def _first_name(user):
    name = (user.get("name") or "").strip() or (user.get("username") or "").strip()
    return name.split()[0] if name else "?"


def quick_due(kind, at, now=None):
    """Due date for a quick-add chip: today, tomorrow or weekend (coming Saturday, today on weekends)."""
    now = now or dt.datetime.now().astimezone()
    day = now.date()
    if kind == "tomorrow":
        day += dt.timedelta(days=1)
    elif kind == "weekend" and now.weekday() < 5:
        day += dt.timedelta(days=5 - now.weekday())
    elif kind not in ("today", "weekend"):
        return None
    return dt.datetime.combine(day, at).astimezone()


def assignee_names(task):
    """Display names of a task's assignees (full name, else username)."""
    names = []
    for user in task.get("assignees") or []:
        name = (user.get("name") or "").strip() or (user.get("username") or "").strip()
        if name:
            names.append(name)
    return names


def _rel(task, kind):
    return (task.get("related_tasks") or {}).get(kind) or []


def build_tree(tasks, sort="due"):
    """Return (roots, children) where children maps task id -> sorted subtasks.

    Open subtasks are part of `tasks` themselves; done subtasks only show up in
    their parent's related_tasks, which is why both directions are merged.
    """
    by_id = {t["id"]: t for t in tasks if "id" in t}
    children, has_parent = {}, set()

    for t in by_id.values():
        for p in _rel(t, "parenttask"):
            if p.get("id") in by_id:
                children.setdefault(p["id"], {}).setdefault(t["id"], t)
                has_parent.add(t["id"])
    for t in by_id.values():
        for s in _rel(t, "subtask"):
            if "id" in s:
                children.setdefault(t["id"], {}).setdefault(s["id"], by_id.get(s["id"], s))
                if s["id"] in by_id:
                    has_parent.add(s["id"])

    key = sort_key(sort)
    roots = sorted((t for t in by_id.values() if t["id"] not in has_parent), key=key)
    kids = {pid: sorted(d.values(), key=lambda t: (bool(t.get("done")), key(t)))
            for pid, d in children.items()}
    return roots, kids


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------

def esc(value):
    return html.escape("" if value is None else str(value), quote=True)


class _Sanitizer(HTMLParser):
    """Reduce Vikunja's (tiptap) description HTML to a safe, e-ink friendly subset."""

    TAGS = {
        "p": "p", "div": "div", "br": "br", "hr": "hr",
        "ul": "ul", "ol": "ol", "li": "li", "blockquote": "blockquote", "pre": "pre",
        "code": "code", "b": "strong", "strong": "strong", "i": "em", "em": "em",
        "u": "u", "s": "s", "del": "s", "strike": "s", "mark": "strong",
        "h1": "h3", "h2": "h3", "h3": "h3", "h4": "h4", "h5": "h4", "h6": "h4",
        "table": "table", "thead": "thead", "tbody": "tbody", "tr": "tr", "td": "td", "th": "th",
        "a": "u",  # links would navigate the kiosk away: keep the text only
    }
    VOID = {"br", "hr"}
    SKIP = {"script", "style", "head", "title", "iframe", "object", "embed", "svg", "template"}

    def __init__(self, check_form=None):
        super().__init__(convert_charrefs=True)
        self.out, self.stack, self.skip = [], [], 0
        self.check_form, self.check_index = check_form, 0
        self.edit_pending = None  # index of the checklist item whose first <p> is its text

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        if tag == "img":
            alt = dict(attrs).get("alt")
            if alt:
                self.out.append(f"[{esc(alt)}]")
            return
        out = self.TAGS.get(tag)
        if not out:
            return
        if out in self.VOID:
            self.out.append(f"<{out}>")
            return
        attrs = dict(attrs)
        if out == "li" and attrs.get("data-checked") in ("true", "false"):  # tiptap checklist item
            on = attrs["data-checked"] == "true"
            self.out.append(f'<li class="ck{" on" if on else ""}">' + _check_box(self.check_form, self.check_index, on))
            self.edit_pending = self.check_index if self.check_form else None
            self.check_index += 1
        elif out == "p" and self.edit_pending is not None:
            url = f"/t/{self.check_form[0]}/item-text"
            self.out.append(f'<p class="edit"{_edit_attrs(url, self.edit_pending)}>')
            self.edit_pending = None
        else:
            if out == "li":
                self.edit_pending = None
            self.out.append(f"<{out}>")
        self.stack.append((tag, out))

    def handle_startendtag(self, tag, attrs):
        if tag == "img" or self.TAGS.get(tag) in self.VOID:
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or not any(t == tag for t, _ in self.stack):
            return
        while self.stack:
            t, out = self.stack.pop()
            self.out.append(f"</{out}>")
            if t == tag:
                break

    def handle_data(self, data):
        if not self.skip:
            self.out.append(esc(data))

    def result(self):
        self.close()
        return "".join(self.out) + "".join(f"</{out}>" for _, out in reversed(self.stack))


# A checklist item in a plain-text (markdown) description: "- [ ] text" / "* [x] text" / "1. [ ] text"
CHECK_LINE = re.compile(r"^(\s*(?:[-*+]|\d+\.)\s+\[)([ xX])(\].*)$")
# ... and in Vikunja's rich-text (tiptap) descriptions
CHECK_HTML = re.compile(r'(<li\b[^>]*?\bdata-checked=")(true|false)(")', re.I)


def _is_html(value):
    return bool(re.search(r"<[a-zA-Z/!]", value))


def checklist_progress(value):
    """(done, total) of the checklist items in a description."""
    value = value or ""
    if _is_html(value):
        states = [m.group(2).lower() for m in CHECK_HTML.finditer(value)]
    else:
        states = ["false" if m.group(2) == " " else "true"
                  for m in map(CHECK_LINE.match, value.split("\n")) if m]
    return states.count("true"), len(states)


def toggle_checklist(value, index, done):
    """Description with the index-th checklist item set to done (None if there is no such item)."""
    if not _is_html(value):
        lines, n = value.split("\n"), 0
        for i, line in enumerate(lines):
            m = CHECK_LINE.match(line)
            if m:
                if n == index:
                    lines[i] = m.group(1) + ("x" if done else " ") + m.group(3)
                    return "\n".join(lines)
                n += 1
        return None
    matches = list(CHECK_HTML.finditer(value))
    if index >= len(matches):
        return None
    m = matches[index]
    head = value[:m.start(2)] + ("true" if done else "false") + value[m.end(2):m.end()]
    rest = value[m.end():]
    # keep tiptap's own <input type="checkbox" checked> in sync (up to the next item)
    nxt = re.search(r"<li\b|</li>", rest, re.I)
    seg, tail = (rest[:nxt.start()], rest[nxt.start():]) if nxt else (rest, "")

    def fix_input(mi):
        tag = re.sub(r'\s+checked(\s*=\s*("[^"]*"|\'[^\']*\'|[^\s>]+))?', "", mi.group(0))
        return tag[:-1].rstrip("/ ") + (' checked="checked"' if done else "") + ">"

    seg = re.sub(r"<input\b[^>]*>", fix_input, seg, count=1, flags=re.I)
    return head + seg + tail


def _check_item_segment(value, index):
    """For the index-th tiptap checklist item: (offset after its <li ...>, its own markup up to
    the next item). None if there is no such item."""
    matches = list(CHECK_HTML.finditer(value))
    if index >= len(matches):
        return None
    start = value.index(">", matches[index].end()) + 1
    nxt = re.search(r"<li\b|</li>", value[start:], re.I)
    return start, value[start:start + nxt.start()] if nxt else value[start:]


def set_checklist_text(value, index, text):
    """Description with the index-th checklist item's text replaced (None if not found)."""
    if not _is_html(value):
        lines, n = value.split("\n"), 0
        for i, line in enumerate(lines):
            m = CHECK_LINE.match(line)
            if m:
                if n == index:
                    cr = "\r" if line.endswith("\r") else ""
                    lines[i] = f"{m.group(1)}{m.group(2)}] {text}{cr}"
                    return "\n".join(lines)
                n += 1
        return None
    found = _check_item_segment(value, index)
    if not found:
        return None
    start, seg = found
    p = re.search(r"(<p\b[^>]*>)(.*?)(</p>)", seg, re.S | re.I)
    if not p:
        return None
    seg_new = seg[:p.start(2)] + html.escape(text, quote=False) + seg[p.end(2):]
    return value[:start] + seg_new + value[start + len(seg):]


def append_checklist_item(value, text):
    """Description with a new unticked checklist item at the end of its (last) checklist."""
    value = value or ""
    if value.strip() and not _is_html(value):  # plain markdown description
        return value.rstrip("\r\n") + "\n- [ ] " + text
    item = ('<li data-checked="false" data-type="taskItem"><label><input type="checkbox"><span></span>'
            f'</label><div><p>{html.escape(text, quote=False)}</p></div></li>')
    # find the end of the last top-level checklist (not a nested one)
    depth, top_is_checklist, last_close = 0, False, None
    for m in re.finditer(r"<(/?)ul\b([^>]*)>", value, re.I):
        if m.group(1):
            depth = max(0, depth - 1)
            if depth == 0 and top_is_checklist:
                last_close = m.start()
        else:
            if depth == 0:
                top_is_checklist = 'data-type="tasklist"' in m.group(2).lower()
            depth += 1
    if last_close is not None:
        return value[:last_close] + item + value[last_close:]
    return value + f'<ul data-type="taskList">{item}</ul>'


def _edit_attrs(url, index=None):
    """Marks text as editable in place (see startEdit in the page JS)."""
    return f' data-url="{esc(url)}"' + (f' data-i="{index}"' if index is not None else "")


def _check_box(form, index, on):
    """Tick box for a checklist item: a form when tickable (detail page), else just a box."""
    if not form:
        return '<span class="box"><span class="mark"></span></span>'
    task_id, back = form
    return (
        '<form class="ckf" method="post" action="/check">'
        f'<input type="hidden" name="id" value="{task_id}">'
        f'<input type="hidden" name="i" value="{index}">'
        f'<input type="hidden" name="done" value="{"0" if on else "1"}">'
        f'<input type="hidden" name="back" value="{esc(back)}">'
        '<button type="submit" class="box"><span class="mark"></span></button></form>'
    )


def has_description(value):
    return bool(re.sub(r"<[^>]*>|&nbsp;|\s", "", value or ""))


def description_html(value, check_form=None):
    """Safe HTML for a task description ('' if it is empty).

    check_form=(task_id, back_url) makes checklist items tickable.
    """
    value = value or ""
    if not has_description(value):
        return ""
    if not _is_html(value):  # plain text / markdown
        out, n = [], 0
        for line in value.split("\n"):
            m = CHECK_LINE.match(line)
            if m:
                on = m.group(2) != " "
                edit = (' edit"' + _edit_attrs(f"/t/{check_form[0]}/item-text", n)) if check_form else '"'
                out.append(f'<div class="ck{" on" if on else ""}">{_check_box(check_form, n, on)}'
                           f'<span class="ckt{edit}>{esc(m.group(3)[1:].strip())}</span></div>')
                n += 1
            else:
                out.append(esc(line) + "<br>")
        html_ = "".join(out)
        return html_[:-4] if html_.endswith("<br>") else html_
    sanitizer = _Sanitizer(check_form)
    sanitizer.feed(value)
    return sanitizer.result()


CSS = """
*{-webkit-box-sizing:border-box;box-sizing:border-box}
html,body{background:#fff;color:#000}
body{margin:0;padding:10px 16px 90px;font:%%FS%%px/1.3 sans-serif;-webkit-text-size-adjust:none}
a{color:#000}
#hd{display:table;width:100%;border-bottom:4px solid #000;padding-bottom:4px;margin-bottom:8px}
#hd h1{display:table-cell;font-size:1.35em;margin:0;vertical-align:bottom}
#hd .nav{display:table-cell;text-align:right;vertical-align:bottom;font-size:.7em;white-space:nowrap}
#hd .nav a{display:inline-block;padding:10px 0 4px 16px}
#status{font-size:.7em;min-height:1.2em}
#pager{position:fixed;bottom:10px;left:50%;width:150px;margin-left:-75px;text-align:center}
#pager a{position:relative;display:inline-block;width:62px;height:50px;margin:0 4px;border:3px solid #000;background:#fff;vertical-align:top}
.tri{position:absolute;left:50%;top:50%;width:0;height:0;margin-left:-13px;border-left:13px solid transparent;border-right:13px solid transparent}
.tri.up{margin-top:-9px;border-bottom:15px solid #000}
.tri.dn{margin-top:-6px;border-top:15px solid #000}
#status.err{font-weight:bold}
.banner{border:3px solid #000;padding:8px;margin:8px 0;font-weight:bold}
#add{margin:0 0 10px}
#add .line{display:table;width:100%}
#add .in{display:table-cell;width:100%;vertical-align:middle}
#add .go{display:table-cell;padding-left:8px;vertical-align:middle}
#add input[type=text]{width:100%;font:inherit;padding:10px;border:3px solid #000;border-radius:0;-webkit-appearance:none;background:#fff;color:#000}
#add button{font:inherit;font-weight:bold;padding:10px 18px;border:3px solid #000;background:#000;color:#fff;border-radius:0;-webkit-appearance:none}
ul{list-style:none;margin:0;padding:0}
.row{display:table;width:100%;border-bottom:1px solid #000}
.row form{display:table-cell;width:1%;vertical-align:middle;padding:8px 10px 8px 0;margin:0}
.row .body{display:table-cell;vertical-align:middle;padding:8px 0;word-wrap:break-word}
.cb{position:relative;display:block;width:2.1em;height:2.1em;margin:0;padding:0;border:3px solid #000;border-radius:0;background:#fff;-webkit-appearance:none}
.mark{display:none;position:absolute;left:32%;top:6%;width:32%;height:62%;border:solid #000;border-width:0 .25em .25em 0;-webkit-transform:rotate(45deg);transform:rotate(45deg)}
.done>.row .mark{display:block}
.done>.row .title{text-decoration:line-through}
.sub{margin-left:1.5em}
.sub .row{border-bottom:1px dotted #000}
.sub .cb{width:1.6em;height:1.6em}
.sub .body{font-size:.9em}
.meta{font-size:.7em;white-space:nowrap}
.meta span{margin-left:6px}
.due.over{background:#000;color:#fff;padding:0 4px;font-weight:bold}
.prio{font-weight:bold}
.lbl{border:1px solid #000;padding:0 4px}
.who{font-weight:bold;font-style:italic}
.empty{padding:40px 0;text-align:center;font-size:1.1em}
.tile{display:block;margin:0 0 16px;padding:10px 14px 12px;border:3px solid #000;color:#000;text-decoration:none;font-size:%%DFS%%px}
.tile .th{display:table;width:100%}
.tile .tt{display:table-cell;font-size:1.3em;font-weight:bold}
.tile .tc{display:table-cell;text-align:right;font-size:1.2em;font-weight:bold;white-space:nowrap}
.tile ul{margin:6px 0 0}
.tile li{padding:5px 0;border-top:1px dotted #000;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.tile li .due{font-size:.85em;margin-left:8px}
.tile .tmore{font-size:.8em;margin-top:4px}
.tile li b{margin-right:6px}
.tile .terr{font-weight:bold;font-size:.9em;margin-top:6px}
@font-face{font-family:KOItems;src:url(/ko/items.ttf) format("truetype")}
.koi{display:inline-block;width:1.4em;margin-right:.3em;font-family:KOItems;font-size:1.3em;line-height:1;text-align:center;vertical-align:middle;font-weight:normal;font-style:normal}
.tile .koi{font-size:1.1em;width:1.3em}
#sugg button{display:block;width:100%;margin:0;padding:8px 4px;border:0;border-bottom:1px dotted #000;background:#fff;color:#000;font:inherit;text-align:left;-webkit-appearance:none;border-radius:0}
#sugg{display:none;margin:0 0 8px;border-top:2px solid #000}
#chips{display:none;margin:6px 0 0}
.chiprow{margin:0 0 2px}
.chip{display:inline-block;margin:0 6px 6px 0;padding:6px 12px;border:2px solid #000;border-radius:1em;background:#fff;color:#000;font-size:.8em;-webkit-appearance:none}
.chip.on{background:#000;color:#fff}
.note{display:inline-block;min-width:1.6em;padding:2px 4px;text-align:center}
.plist a{display:block;padding:14px 0;border-bottom:1px solid #000;text-decoration:none}
.open{display:block;color:#000;text-decoration:none}
.row .body.link{padding:0}
.body.link .open{padding:10px 0;min-height:2.4em}
.arrow{display:table-cell;width:1%;vertical-align:middle;padding:0 6px 0 16px;text-decoration:none}
.chev{display:block;width:.55em;height:.55em;margin-right:4px;border:solid #000;border-width:.2em .2em 0 0;-webkit-transform:rotate(45deg);transform:rotate(45deg)}
.head>.row{border-bottom:none}
.head .title{font-size:1.25em;font-weight:bold}
.parent{font-size:.75em;margin:0 0 6px}
.parent a{display:inline-block;padding:6px 0}
h2.sec{font-size:.75em;margin:18px 0 4px;padding-bottom:2px;border-bottom:3px solid #000}
h2.cat{font-size:1.05em;margin:22px 0 4px}
.emo{width:1.1em;height:1.1em;margin:0 .1em;vertical-align:-.15em;border:0}
.desc{word-wrap:break-word}
.desc p,.desc div{margin:0 0 .5em}
.desc ul,.desc ol{margin:0 0 .5em;padding-left:1.4em}
.desc ul{list-style:disc}
.desc ol{list-style:decimal}
.desc li.ck{list-style:none;margin-left:-1.2em}
.desc li.ck div,.desc li.ck p{display:inline;margin:0}
.ck{padding:5px 0}
.ck.on>div,.ck.on>p,.ck.on>.ckt{text-decoration:line-through}
.box{position:relative;display:inline-block;width:.9em;height:.9em;margin-right:.4em;border:2px solid #000;vertical-align:-.1em}
.ck.on>.box .mark,.ck.on>.ckf .mark{display:block}
.ckf{display:inline;margin:0}
.edit{border-bottom:2px dotted #000}
.ren{display:block;margin:4px 0}
.ren input{width:100%;font:inherit;padding:6px;border:3px solid #000;border-radius:0;-webkit-appearance:none;background:#fff;color:#000}
.ren button{font-size:.8em;margin:6px 8px 0 0;padding:8px 14px;border:3px solid #000;border-radius:0;background:#000;color:#fff;-webkit-appearance:none}
.ren button.cancel{background:#fff;color:#000}
.ckf .box{width:1.5em;height:1.5em;margin:0 .5em 0 0;padding:0;border:3px solid #000;border-radius:0;background:#fff;-webkit-appearance:none;vertical-align:middle}
.desc pre{white-space:pre-wrap;border:1px solid #000;padding:4px}
.desc blockquote{margin:0 0 .5em;padding-left:8px;border-left:4px solid #000}
.desc table{border-collapse:collapse;margin-bottom:.5em}
.desc td,.desc th{border:1px solid #000;padding:2px 4px}
.desc h3,.desc h4{margin:.6em 0 .3em}
"""

# Plain ES5 only: the Kindle's WebKit is from ~2012.
JS = r"""
(function () {
  var CFG = %%CFG%%;
  // CFG.list: fragment URL to re-sync from (list page).
  // CFG.home: page to go back to after CFG.homeAfter idle seconds (detail page).
  var IDLE = 15000, loadedAt = now(), lastAct = CFG.home ? now() : 0, lastRefresh = now(), busy = false;
  var current = null;

  function now() { return new Date().getTime(); }
  function $(id) { return document.getElementById(id); }
  function hasCls(el, c) { return el && el.className && (" " + el.className + " ").indexOf(" " + c + " ") >= 0; }
  function closest(el, c) { while (el && el !== document) { if (hasCls(el, c)) return el; el = el.parentNode; } return null; }
  function status(msg, isErr) { var s = $("status"); s.textContent = msg || ""; s.className = isErr ? "err" : ""; }
  function touch() { lastAct = now(); }
  function setCls(el, c, on) {
    var rest = (" " + el.className + " ").replace(" " + c + " ", " ").replace(/^\s+|\s+$/g, "");
    el.className = on ? (rest ? rest + " " : "") + c : rest;
  }

  function xhr(method, url, body, cb) {
    var r = new XMLHttpRequest();
    r.open(method, url, true);
    r.setRequestHeader("X-Requested-With", "XMLHttpRequest");
    if (body !== null) r.setRequestHeader("Content-Type", "application/x-www-form-urlencoded");
    r.onreadystatechange = function () {
      if (r.readyState === 4) cb(r.status >= 200 && r.status < 300, r);
    };
    r.send(body);
  }

  function serialize(f) {
    var p = [], els = f.elements;
    for (var i = 0; i < els.length; i++) {
      var e = els[i];
      if (e.name) p.push(encodeURIComponent(e.name) + "=" + encodeURIComponent(e.value));
    }
    return p.join("&");
  }

  function errText(r) {
    if (!r.status) return CFG.offline;
    return CFG.error + " " + r.status + (r.responseText ? ": " + r.responseText.substring(0, 200) : "");
  }

  function refresh() {
    if (busy || !CFG.list) return;
    busy = true;
    xhr("GET", CFG.list, null, function (ok, r) {
      busy = false;
      lastRefresh = now();
      if (!ok) { status(errText(r), true); return; }
      // Only touch the DOM when something changed: every redraw flashes e-ink.
      if (r.responseText !== current) {
        $("list").innerHTML = r.responseText;
        current = r.responseText;
        updatePager();
      }
      $("upd").textContent = r.getResponseHeader("X-Updated") || "";
      if ($("today") && r.getResponseHeader("X-Today")) $("today").textContent = r.getResponseHeader("X-Today");
      status("");
    });
  }

  // Tick/untick right away, revert if the server says no.
  function toggleVia(f, holder, cls) {
    var field = f.elements["done"], target = field.value === "1", body = serialize(f);
    setCls(holder, cls, target);
    field.value = target ? "0" : "1";
    xhr("POST", f.action, body, function (ok, r) {
      if (ok) { status(""); lastRefresh = 0; return; }   // re-sync once idle
      setCls(holder, cls, !target);
      field.value = target ? "1" : "0";
      status(errText(r), true);
    });
  }

  // Edit in place: tapping text marked "edit" (detail page) swaps in a small form.
  var editing = null;
  function startEdit(el) {
    if (editing) return;
    var f = document.createElement("form"), inp = document.createElement("input");
    var save = document.createElement("button"), cancel = document.createElement("button");
    f.className = "ren";
    inp.type = "text"; inp.name = "title"; inp.setAttribute("autocomplete", "off");
    // data-empty: placeholder shown for empty text (which may then be saved empty)
    var ph = el.getAttribute("data-empty"), cur = textOf(el).replace(/^\s+|\s+$/g, "");
    inp.value = cur === ph ? "" : cur;
    save.type = "submit"; save.textContent = CFG.save;
    cancel.type = "button"; cancel.className = "cancel"; cancel.textContent = CFG.cancel;
    f.appendChild(inp); f.appendChild(save); f.appendChild(cancel);
    editing = { el: el, html: el.innerHTML, text: inp.value, ph: ph };
    el.innerHTML = "";
    el.appendChild(f);
    inp.focus();
  }
  // Text of an element with emoji pictures turned back into the emoji (their alt text).
  function textOf(el) {
    var out = "", n;
    for (n = el.firstChild; n; n = n.nextSibling) {
      if (n.nodeType === 3) out += n.nodeValue;
      else if (n.tagName === "IMG") out += n.getAttribute("alt") || "";
      else if (n.nodeType === 1) out += textOf(n);
    }
    return out;
  }
  function stopEdit(text) {
    if (!editing) return;
    if (text === null) editing.el.innerHTML = editing.html;
    else editing.el.textContent = text;
    editing = null;
  }
  function saveEdit(f) {
    var value = f.elements["title"].value.replace(/\s+/g, " ").replace(/^ | $/g, "");
    if ((!value && editing.ph === null) || value === editing.text) { stopEdit(null); return; }
    var el = editing.el, i = el.getAttribute("data-i"), ph = editing.ph;
    var body = "title=" + encodeURIComponent(value) + (i !== null ? "&i=" + encodeURIComponent(i) : "");
    status("\u2026");
    xhr("POST", el.getAttribute("data-url"), body, function (ok, r) {
      if (!ok) { status(errText(r), true); return; }
      status("");
      stopEdit(value || ph);
      if (hasCls(el, "title") && closest(el, "head")) document.title = value;
    });
  }

  // Quick-add chips (due date, assignees) under "New task": shown while adding.
  function resetChips() {
    var box = $("chips");
    if (!box) return;
    var all = box.getElementsByTagName("button");
    for (var i = 0; i < all.length; i++) setCls(all[i], "on", false);
    $("due").value = ""; $("who").value = "";
    box.style.display = "none";
  }
  function pickChip(chip) {
    var row = chip.parentNode, kind = chip.getAttribute("data-kind"), on = !hasCls(chip, "on");
    var all = row.getElementsByTagName("button"), picked = [];
    if (kind === "due") for (var i = 0; i < all.length; i++) setCls(all[i], "on", false);
    setCls(chip, "on", on);
    for (var j = 0; j < all.length; j++) if (hasCls(all[j], "on")) picked.push(all[j].getAttribute("data-v"));
    $(kind).value = picked.join(",");
  }
  if ($("chips")) {
    $("title").addEventListener("focus", function () { $("chips").style.display = "block"; }, false);
    setInterval(function () { if ($("title").value) $("chips").style.display = "block"; }, 700);
  }

  document.addEventListener("click", function (ev) {
    var el = ev.target;
    var chip = closest(el, "chip");
    if (chip) { ev.preventDefault(); touch(); pickChip(chip); return; }
    if (closest(el, "ren")) {
      if (hasCls(el, "cancel")) { ev.preventDefault(); stopEdit(null); }
      return;
    }
    if (closest(el, "ckf")) return;                      // tick box: handled as submit
    var ed = closest(el, "edit");
    if (ed) { ev.preventDefault(); touch(); startEdit(ed); }
  }, false);

  document.addEventListener("submit", function (ev) {
    var f = ev.target;
    touch();
    if (hasCls(f, "t")) {
      ev.preventDefault();
      toggleVia(f, closest(f, "task"), "done");
    } else if (hasCls(f, "ckf")) {
      ev.preventDefault();
      toggleVia(f, closest(f, "ck"), "on");
    } else if (hasCls(f, "ren")) {
      ev.preventDefault();
      saveEdit(f);
    } else if (hasCls(f, "pick")) {   // autocomplete suggestion
      ev.preventDefault();
      status("\u2026");
      xhr("POST", f.action, serialize(f), function (ok, r) {
        if (!ok) { status(errText(r), true); return; }
        $("title").value = "";
        $("title").blur();
        showSugg("");
        lastAct = 0;
        refresh();
      });
    } else if (f.id === "add") {
      ev.preventDefault();
      var title = f.elements["title"];
      if (!title.value.replace(/\s+/g, "")) return;
      status("…");
      xhr("POST", f.action, serialize(f), function (ok, r) {
        if (!ok) { status(errText(r), true); return; }
        title.value = "";
        title.blur();
        showSugg("");
        resetChips();
        if (!CFG.list) { location.reload(); return; }
        lastAct = 0;
        refresh();
      });
    }
  }, false);

  document.addEventListener("touchstart", touch, false);
  document.addEventListener("mousedown", touch, false);
  document.addEventListener("keydown", touch, false);

  setInterval(function () {
    var t = now(), a = document.activeElement, title = $("title");
    var typing = (a && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName)) || (title && title.value !== "");
    if (typing && t - lastAct > 600000) {   // abandoned input: clear it after 10 min
      title.value = ""; if (a && a.blur) a.blur(); typing = false;
    }
    if (typing || t - lastAct < IDLE) return;
    if (CFG.home && t - lastAct > CFG.homeAfter * 1000) { location.href = CFG.home; return; }
    // Full reload now and then: frees browser memory and clears e-ink ghosting.
    if (t - loadedAt > CFG.reloadHours * 3600000) { location.reload(); return; }
    if (t - lastRefresh >= CFG.refresh * 1000) refresh();
  }, 5000);

  // Shopping list autocomplete. Shown/hidden from script: old WebKit doesn't re-evaluate
  // CSS :empty after innerHTML changes. Key events from the Kindle keyboard may not arrive,
  // so the input is also polled.
  function showSugg(html) {
    var box = $("sugg");
    if (!box) return;
    box.innerHTML = html;
    box.style.display = html ? "block" : "none";
  }
  if (CFG.suggest && $("title")) {
    var lastQ = "", sugTimer = null;
    var checkSugg = function () {
      var q = $("title").value.replace(/^\s+|\s+$/g, "");
      if (q === lastQ) return;
      lastQ = q;
      clearTimeout(sugTimer);
      if (q.length < 2) { showSugg(""); return; }
      sugTimer = setTimeout(function () {
        xhr("GET", CFG.suggest + "?q=" + encodeURIComponent(q), null, function (ok, r) {
          if (ok && $("title").value.replace(/^\s+|\s+$/g, "") === q) showSugg(r.responseText);
        });
      }, 300);
    };
    $("title").addEventListener("keyup", checkSugg, false);
    $("title").addEventListener("input", checkSugg, false);
    setInterval(checkSugg, 500);
  }

  // Kiosk app (kindle/extensions/vikunja/app): the page runs in a frame there,
  // and only the app itself may talk to the Kindle, so ask it to exit.
  var exitLink = $("exit"), framed = false;
  try { framed = window.parent !== window; } catch (e) { framed = true; }
  if (exitLink && (framed || window.kindle)) {
    exitLink.style.display = "inline-block";
    exitLink.addEventListener("click", function (ev) {
      ev.preventDefault();
      if (window.kindle && kindle.appmgr) kindle.appmgr.start("com.lab126.booklet.home");
      else window.parent.postMessage("kv-exit", "*");
    }, false);
  }

  // Page up/down buttons: drag scrolling is slow on e-ink (and may be absent in the app).
  function viewHeight() { return window.innerHeight || document.documentElement.clientHeight; }
  function updatePager() {
    var p = $("pager");
    if (p) p.style.display = document.body.scrollHeight > viewHeight() + 4 ? "block" : "none";
  }
  function pageBy(dir) {
    return function (ev) { ev.preventDefault(); touch(); window.scrollBy(0, dir * (viewHeight() - 90)); };
  }
  if ($("pgup")) {
    $("pgup").addEventListener("click", pageBy(-1), false);
    $("pgdn").addEventListener("click", pageBy(1), false);
  }
  updatePager();

  current = $("list") ? $("list").innerHTML : null;
})();
"""


class Renderer:
    def __init__(self, cfg, icons=None):
        self.cfg = cfg
        self.t = STRINGS.get(cfg.lang, STRINGS["en"])
        self.icons = icons  # IconFont (KitchenOwl item icons) or None

    # -- building blocks ---------------------------------------------------

    def layout(self, title, body, script=""):
        css = CSS.replace("%%FS%%", str(self.cfg.font_size)).replace("%%DFS%%", str(self.cfg.dash_font_size))
        return (
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{esc(title)}</title><style>{css}</style></head>"
            f"<body>{body}{script}</body></html>"
        )

    def fmt_date(self, d, now):
        days = (d.date() - now.date()).days
        if days == 0:
            label = self.t["today"]
        elif days == 1:
            label = self.t["tomorrow"]
        elif days == -1:
            label = self.t["yesterday"]
        elif 1 < days < 7:
            label = self.t["weekdays"][d.weekday()]
        elif d.year == now.year:
            label = d.strftime("%d.%m.")
        else:
            label = d.strftime("%d.%m.%Y")
        if (d.hour, d.minute) not in ((0, 0), (self.cfg.quick_due_time.hour, self.cfg.quick_due_time.minute)):
            label += d.strftime(" %H:%M")
        return label

    def _meta(self, t, kids, now):
        parts = []
        prio = t.get("priority") or 0
        if prio >= 3:  # high / urgent / do now
            parts.append(f'<span class="prio">{"!" * (prio - 2)}</span>')
        d = parse_date(t.get("due_date"))
        if d:
            over = " over" if d < now and not t.get("done") else ""
            parts.append(f'<span class="due{over}">{esc(self.fmt_date(d, now))}</span>')
        names = assignee_names(t)
        if names:
            parts.append(f'<span class="who">{esc(", ".join(names))}</span>')
        sub = kids.get(t["id"]) or []
        ck_done, ck_total = checklist_progress(t.get("description"))
        total = len(sub) + ck_total
        if total:
            done_count = sum(1 for s in sub if s.get("done")) + ck_done
            parts.append(f'<span class="cnt">{done_count}/{total}</span>')
        for label in t.get("labels") or []:
            parts.append(f'<span class="lbl">{esc(label.get("title"))}</span>')
        if has_description(t.get("description")):
            parts.append('<span class="more">\u00b6</span>')
        return f' <span class="meta">{"".join(parts)}</span>' if parts else ""

    def _toggle_form(self, t, back, action="/toggle", extra=None):
        hidden = "".join(f'<input type="hidden" name="{k}" value="{esc(v)}">' for k, v in (extra or {}).items())
        return (
            f'<form class="t" method="post" action="{action}">'
            f'<input type="hidden" name="id" value="{t["id"]}">'
            f'<input type="hidden" name="done" value="{"0" if t.get("done") else "1"}">'
            f'<input type="hidden" name="back" value="{esc(back)}">{hidden}'
            '<button type="submit" class="cb"><span class="mark"></span></button></form>'
        )

    def _home_link(self):
        return f'<a href="/">{esc(self.t["home"])}</a>' if self.cfg.dashboard else ""

    def _home_return(self):
        """Script options: go back to the dashboard after DASHBOARD_RETURN_SECONDS idle."""
        if self.cfg.dashboard and self.cfg.dashboard_after > 0:
            return {"home": "/", "homeAfter": self.cfg.dashboard_after}
        return {"home": None}

    def _add_form(self, action, placeholder, hidden=""):
        return (
            f'<form id="add" method="post" action="{action}">{hidden}'
            '<div class="line"><div class="in">'
            f'<input type="text" id="title" name="title" autocomplete="off" placeholder="{esc(placeholder)}">'
            f'</div><div class="go"><button type="submit">{esc(self.t["add"])}</button></div></div></form>'
        )

    def _node(self, t, kids, ctx, depth=0):
        """One task row (plus nested subtasks when ctx["nest"]). Tapping the text opens details."""
        tid, done = t["id"], bool(t.get("done"))
        ctx["seen"].add(tid)
        href = f"/t/{tid}?p={ctx['pid']}"
        if ctx.get("edit"):  # detail page: tap the text to rename, the arrow opens the subtask
            body = (f'<div class="body"><span class="title edit"{_edit_attrs(f"/t/{tid}/rename")}>'
                    f'{esc(t.get("title"))}</span>{self._meta(t, kids, ctx["now"])}</div>')
        else:
            body = (f'<div class="body link"><a class="open" href="{href}">'
                    f'<span class="title">{esc(t.get("title"))}</span>'
                    f'{self._meta(t, kids, ctx["now"])}</a></div>')
        out = [
            f'<li class="task{" done" if done else ""}" data-id="{tid}"><div class="row">'
            f'{self._toggle_form(t, ctx["back"])}{body}'
            f'<a class="arrow" href="{href}"><span class="chev"></span></a></div>'
        ]
        sub = []
        if ctx["nest"] and depth < MAX_DEPTH:
            sub = [c for c in kids.get(tid, []) if c.get("id") not in ctx["seen"]]
        if sub:
            out.append('<ul class="sub">')
            for c in sub:
                if c["id"] not in ctx["seen"]:
                    out.append(self._node(c, kids, ctx, depth + 1))
            out.append("</ul>")
        out.append("</li>")
        return "".join(out)

    def _exit_link(self):
        return f'<a id="exit" href="#" style="display:none">{esc(self.t["exit"])}</a>'

    def _script(self, **cfg):
        pager = ('<div id="pager" style="display:none"><a id="pgup" href="#"><span class="tri up"></span></a>'
                 '<a id="pgdn" href="#"><span class="tri dn"></span></a></div>')
        cfg.update(refresh=self.cfg.refresh, reloadHours=6, error=self.t["error"], offline=self.t["offline"],
                   save=self.t["save"], cancel=self.t["cancel"])
        return pager + "<script>" + JS.replace("%%CFG%%", json.dumps(cfg)) + "</script>"

    # -- pages -------------------------------------------------------------

    def task_list(self, project_id, tasks, now=None):
        """The refreshable part of the page."""
        roots, kids = build_tree(tasks, self.cfg.sort)
        ctx = {"back": f"/p/{project_id}", "pid": project_id, "now": now or dt.datetime.now().astimezone(),
               "seen": set(), "nest": self.cfg.list_subtasks}
        items = []
        for t in roots:
            if t["id"] not in ctx["seen"]:
                items.append(self._node(t, kids, ctx))
        if ctx["nest"]:
            for t in tasks:  # anything unreachable (e.g. relation cycles)
                if t.get("id") is not None and t["id"] not in ctx["seen"] and not t.get("done"):
                    items.append(self._node(t, kids, ctx))
        if not items:
            return f'<p class="empty">{esc(self.t["empty"])}</p>'
        return '<ul id="tasks">' + "".join(items) + "</ul>"

    def _chips(self, users):
        t = self.t
        due = "".join(f'<button type="button" class="chip" data-kind="due" data-v="{k}">{esc(label)}</button>'
                      for k, label in (("today", t["today"].capitalize()), ("tomorrow", t["tomorrow"].capitalize()),
                                       ("weekend", t["weekend"])))
        who = "".join(f'<button type="button" class="chip" data-kind="who" data-v="{u["id"]}">'
                      f'{esc(_first_name(u))}</button>' for u in users)
        return ('<input type="hidden" id="due" name="due" value="">'
                '<input type="hidden" id="who" name="who" value="">'
                f'<div id="chips"><div class="chiprow">{due}</div>'
                + (f'<div class="chiprow">{who}</div>' if who else "") + "</div>")

    def task_page(self, project_id, title, list_html, error=None, users=()):
        t = self.t
        banner = f'<div class="banner">{esc(error)}</div>' if error else ""
        body = (
            f'<div id="hd"><h1>{esc(title)}</h1><div class="nav">'
            f'<span id="upd">{dt.datetime.now().strftime("%H:%M")}</span>{self._home_link()}'
            f'<a href="/p/{project_id}">{esc(t["refresh"])}</a>'
            f'<a href="/projects">{esc(t["projects"])}</a>{self._exit_link()}</div></div>'
            f'{banner}<form id="add" method="post" action="/p/{project_id}/add">'
            '<div class="line"><div class="in">'
            f'<input type="text" id="title" name="title" autocomplete="off" placeholder="{esc(t["new_task"])}">'
            f'</div><div class="go"><button type="submit">{esc(t["add"])}</button></div></div>'
            f'{self._chips(users)}</form>'
            f'<div id="status"></div><div id="list">{list_html}</div>'
        )
        return self.layout(title, body, self._script(list=f"/p/{project_id}/list", **self._home_return()))

    def detail_page(self, task, project_id, project_title, now=None):
        """One task: its description, parent and (tickable) subtasks."""
        t, now = self.t, now or dt.datetime.now().astimezone()
        tid = task["id"]
        home, here = f"/p/{project_id}", f"/t/{tid}?p={project_id}"
        key = sort_key(self.cfg.sort)
        subs = sorted((s for s in _rel(task, "subtask") if "id" in s),
                      key=lambda s: (bool(s.get("done")), key(s)))
        ctx = {"back": here, "pid": project_id, "now": now, "seen": {tid}, "nest": False, "edit": True}
        rows = "".join(self._node(s, {}, ctx) for s in subs)
        parents = " ".join(f'<a href="/t/{p["id"]}?p={project_id}">{esc(p.get("title"))}</a>'
                           for p in _rel(task, "parenttask") if "id" in p)
        desc = description_html(task.get("description"), check_form=(tid, here))
        body = (
            f'<div id="hd"><h1><a class="open" href="{home}">\u2039 {esc(project_title or t["back"])}</a></h1>'
            f'<div class="nav"><a href="{esc(here)}">{esc(t["refresh"])}</a>{self._exit_link()}</div></div>'
            f'<div id="status"></div>'
            + (f'<div class="parent">{esc(t["parent"])}: {parents}</div>' if parents else "")
            + f'<div class="task head{" done" if task.get("done") else ""}"><div class="row">'
            f'{self._toggle_form(task, here)}<div class="body">'
            f'<span class="title edit"{_edit_attrs(f"/t/{tid}/rename")}>{esc(task.get("title"))}</span>'
            f'{self._meta(task, {tid: subs}, now)}</div></div></div>'
            + (f'<div class="desc">{desc}</div>' if desc else "")
            + (f'<h2 class="sec">{esc(t["subtasks"])}</h2><ul>{rows}</ul>' if rows else "")
            # new subtasks become "- [ ]" checklist items in the description, like in Vikunja's editor
            + f'<form id="add" method="post" action="/t/{tid}/item" style="margin-top:14px">'
            f'<input type="hidden" name="back" value="{esc(here)}">'
            '<div class="line"><div class="in">'
            f'<input type="text" id="title" name="title" autocomplete="off" placeholder="{esc(t["new_subtask"])}">'
            f'</div><div class="go"><button type="submit">{esc(t["add"])}</button></div></div></form>'
        )
        home_after = self.cfg.home_after
        return self.layout(task.get("title") or "", body,
                           self._script(list=None, home=home if home_after > 0 else None, homeAfter=home_after))

    # -- dashboard -----------------------------------------------------------

    def today(self, now=None):
        now = now or dt.datetime.now()
        return f'{self.t["weekdays"][now.weekday()]} {now.strftime("%d.%m.")}'

    def _tile(self, href, title, count="", lines=(), more=0, error=None, note=""):
        items = "".join(f"<li>{line}</li>" for line in lines)
        tag = "a" if href else "div"  # tiles without a page (calendar) aren't links
        return (
            f'<{tag} class="tile"' + (f' href="{esc(href)}"' if href else "") + '>'
            f'<div class="th"><span class="tt">{esc(title)}</span><span class="tc">{esc(count)}</span></div>'
            + (f"<ul>{items}</ul>" if items else "")
            + (f'<div class="tmore">{esc(self.t["more"].format(n=more))}</div>' if more > 0 else "")
            + (f'<div class="tmore">{esc(note)}</div>' if note else "")
            + (f'<div class="terr">{esc(error)}</div>' if error else "")
            + f"</{tag}>"
        )

    def calendar_tile(self, events, now=None):
        now = now or dt.datetime.now().astimezone()
        lines = []
        for ev in events:
            start = ev["start"].astimezone() if not ev["all_day"] else ev["start"]
            day = max(start, now) if ev["all_day"] else start   # ongoing all-day event: "today"
            when = self.fmt_date(dt.datetime.combine(day.date(), dt.time()).astimezone(), now)
            if not ev["all_day"]:
                when += start.strftime(" %H:%M")
            lines.append(f"<b>{esc(when)}</b> {esc(ev['summary'])}")
        return self._tile(None, self.cfg.caldav_title or self.t["calendar"], lines=lines,
                          note="" if lines else self.t["no_events"])

    def vikunja_tile(self, project_id, title, tasks, now=None, max_lines=6):
        now = now or dt.datetime.now().astimezone()
        roots, _ = build_tree(tasks, self.cfg.sort)
        lines = []
        for t in roots[:max_lines]:
            d = parse_date(t.get("due_date"))
            due = (f'<span class="due{" over" if d < now else ""}">{esc(self.fmt_date(d, now))}</span>'
                   if d else "")
            lines.append(esc(t.get("title")) + due)
        return self._tile(f"/p/{project_id}", title, self.t["open_count"].format(n=len(roots)),
                          lines, len(roots) - len(lines))

    def shop_tile(self, lst, max_lines=6):
        items = sorted(lst.get("items") or [], key=_shop_item_key)
        lines = [self._koi(i) + esc(i.get("name")) + (f' <span class="due">{esc(i["description"])}</span>'
                                       if i.get("description") else "") for i in items[:max_lines]]
        return self._tile(f"/s/{lst['id']}", lst.get("name") or "KitchenOwl",
                          self.t["item_count"].format(n=len(items)), lines, len(items) - len(lines))

    def error_tile(self, href, title, error):
        return self._tile(href, title, error=str(error))

    def dashboard_page(self, tiles_html):
        body = (
            f'<div id="hd"><h1 id="today">{esc(self.today())}</h1><div class="nav">'
            f'<span id="upd">{dt.datetime.now().strftime("%H:%M")}</span>'
            f'<a href="/">{esc(self.t["refresh"])}</a>{self._exit_link()}</div></div>'
            f'<div id="status"></div><div id="list">{tiles_html}</div>'
        )
        return self.layout(self.t["home"], body, self._script(list="/dash/list", home=None))

    # -- KitchenOwl shopping list --------------------------------------------

    def _koi(self, item):
        """KitchenOwl item icon (empty if unavailable)."""
        icons = self.icons.mapping() if self.icons else None
        cp = icons.get(item.get("icon") or "") if icons else None
        return f'<span class="koi">&#{cp};</span>' if cp else ""

    def suggestions(self, lid, items, limit=6):
        rows = []
        for item in items[:limit]:
            desc = item.get("description") or ""
            rows.append(
                f'<form class="pick" method="post" action="/s/{lid}/pick">'
                f'<input type="hidden" name="id" value="{item["id"]}">'
                f'<input type="hidden" name="desc" value="{esc(desc)}">'
                f'<input type="hidden" name="back" value="/s/{lid}">'
                f'<button type="submit">{self._koi(item)}{esc(item.get("name"))}'
                + (f' <span class="meta">{esc(desc)}</span>' if desc else "") + "</button></form>")
        return "".join(rows)

    def _shop_row(self, lid, item, done):
        iid, desc = item["id"], item.get("description") or ""
        form = self._toggle_form({"id": iid, "done": done}, f"/s/{lid}", f"/s/{lid}/toggle",
                                 {"desc": desc} if done else None)
        if done:
            note = f' <span class="meta">{esc(desc)}</span>' if desc else ""
        else:  # tap the note to edit it ("+" when empty)
            note = (f' <span class="meta"><span class="note edit" data-empty="+"'
                    f'{_edit_attrs(f"/s/{lid}/item/{iid}/desc")}>{esc(desc) or "+"}</span></span>')
        return (f'<li class="task{" done" if done else ""}" data-id="{iid}"><div class="row">{form}'
                f'<div class="body">{self._koi(item)}<span class="title">{esc(item.get("name"))}</span>{note}'
                '</div></div></li>')

    def shop_list(self, lst):
        lid, t = lst["id"], self.t
        items = lst.get("items") or []
        groups = {}
        for item in items:
            cat = item.get("category") or {}
            key = (cat.get("id") is None, cat.get("ordering") or 0, (cat.get("name") or "").lower(), cat.get("id"))
            groups.setdefault(key, (cat.get("name") or t["other"], []))[1].append(item)
        with_headers = any(item.get("category") for item in items)
        out = []
        for key in sorted(groups, key=lambda k: k[:3]):
            name, group = groups[key]
            if with_headers:
                out.append(f'<h2 class="sec cat">{esc(name)}</h2>')
            out.append("<ul>" + "".join(self._shop_row(lid, i, False)
                                        for i in sorted(group, key=_shop_item_key)) + "</ul>")
        if not items:
            out.append(f'<p class="empty">{esc(t["list_empty"])}</p>')
        on_list = {i.get("id") for i in items}
        recent = [i for i in lst.get("recentItems") or [] if i.get("id") not in on_list]
        if recent:
            out.append(f'<h2 class="sec cat">{esc(t["recent"])}</h2><ul>'
                       + "".join(self._shop_row(lid, i, True) for i in recent) + "</ul>")
        return "".join(out)

    def shop_page(self, lst, list_html):
        lid, t = lst["id"], self.t
        title = lst.get("name") or "KitchenOwl"
        body = (
            f'<div id="hd"><h1>{esc(title)}</h1><div class="nav">'
            f'<span id="upd">{dt.datetime.now().strftime("%H:%M")}</span>{self._home_link()}'
            f'<a href="/s/{lid}">{esc(t["refresh"])}</a>{self._exit_link()}</div></div>'
            + self._add_form(f"/s/{lid}/add", t["new_item"])
            + f'<div id="sugg"></div><div id="status"></div><div id="list">{list_html}</div>'
        )
        return self.layout(title, body, self._script(list=f"/s/{lid}/list", suggest=f"/s/{lid}/suggest",
                                                     **self._home_return()))

    def projects_page(self, projects):
        ids = {p["id"] for p in projects}
        by_parent = {}
        for p in projects:
            parent = p.get("parent_project_id") or 0
            by_parent.setdefault(parent if parent in ids else 0, []).append(p)

        def order(p):
            return (float(p.get("position") or 0), (p.get("title") or "").lower())

        rows = []

        def walk(parent, depth):
            for p in sorted(by_parent.get(parent, []), key=order):
                rows.append(f'<a href="/p/{p["id"]}" style="padding-left:{depth * 1.2}em">'
                            f'{esc(p.get("title"))}</a>')
                if depth < 8:
                    walk(p["id"], depth + 1)

        walk(0, 0)
        body = (f'<div id="hd"><h1>{esc(self.t["pick"])}</h1></div>'
                f'<div class="plist">{"".join(rows)}</div>')
        return self.layout(self.t["projects"], body)

    def message_page(self, title, message):
        return self.layout(title, f'<div id="hd"><h1>{esc(title)}</h1></div>'
                                  f'<div class="banner">{esc(message)}</div>')


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "kindle-vikunja"
    cfg = None
    api = None  # Vikunja (None if not configured)
    ko = None   # KitchenOwl (None if not configured)
    emoji = None  # EmojiCache
    calendar = None  # Calendar (None if not configured)
    render = None

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    # -- plumbing ----------------------------------------------------------

    def _send(self, status, body, ctype="text/html; charset=utf-8", headers=None, cache="no-store"):
        if isinstance(body, str) and ctype.startswith("text/html"):
            body = emojify(body, self.cfg.emoji)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        if self._cookie:
            self.send_header("Set-Cookie", self._cookie)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location):
        self._send(303, "", headers={"Location": location})

    def _form(self):
        length = min(int(self.headers.get("Content-Length") or 0), 65536)
        raw = self.rfile.read(length).decode("utf-8", "replace")
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}

    def _ajax(self):
        return self.headers.get("X-Requested-With") == "XMLHttpRequest"

    def _authorized(self, query):
        key = self.cfg.access_key
        if not key:
            return True
        given = query.get("key", [""])[0]
        if given and hmac.compare_digest(given.encode(), key.encode()):
            self._cookie = f"kv_key={urllib.parse.quote(key)}; Max-Age=31536000; Path=/; HttpOnly"
            return True
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except cookies.CookieError:
            return False
        if "kv_key" not in jar:
            return False
        return hmac.compare_digest(urllib.parse.unquote(jar["kv_key"].value).encode(), key.encode())

    def _dispatch(self, method):
        self._cookie = None
        url = urllib.parse.urlsplit(self.path)
        path = url.path.rstrip("/") or "/"
        if path == "/healthz":
            return self._send(200, "ok", "text/plain")
        self._query = urllib.parse.parse_qs(url.query)
        if not self._authorized(self._query):
            return self._send(403, self.render.message_page("403", self.render.t["forbidden"]))
        try:
            self._route(method, path)
        except ApiError as e:
            self.log_message("%s", e)
            if self._ajax() or path.endswith("/list"):
                self._send(502, str(e), "text/plain; charset=utf-8")
            else:
                self._send(502, self.render.message_page(self.render.t["error"], str(e)))

    def _route(self, method, path):
        if method == "GET" and path == "/":
            if self.cfg.dashboard:
                return self._send(200, self.render.dashboard_page(self._tiles()))
            if not self.api:
                return self._redirect("/shop")
            dp = self.cfg.default_project
            return self._redirect(f"/p/{dp}" if dp else "/projects")
        if method == "GET" and path == "/dash/list":
            return self._send(200, self._tiles(), headers={
                "X-Updated": dt.datetime.now().strftime("%H:%M"), "X-Today": self.render.today()})
        m_emoji = re.fullmatch(r"/emoji/([0-9a-f]{2,8}(?:-[0-9a-f]{2,8}){0,9})\.png", path)
        if m_emoji:
            img = self.emoji.get(m_emoji.group(1))
            if img is None:  # invisible instead of a broken-image symbol; retried later
                return self._send(200, BLANK_GIF, "image/gif", cache="max-age=600")
            return self._send(200, img, "image/png", cache="max-age=2592000")
        if path == "/ko/items.ttf":
            icons = self.render.icons
            if not icons or not icons.mapping():
                return self._send(404, "no icon font", "text/plain")
            return self._send(200, icons.font, "font/ttf", cache="max-age=604800")
        if path == "/shop" or path.startswith("/s/"):
            if not self.ko:
                return self._send(404, self.render.message_page(
                    "404", self.render.t["not_configured"].format(name="KitchenOwl")))
            return self._shop_route(method, path)
        if not self.api and path != "/healthz":
            return self._send(404, self.render.message_page(
                "404", self.render.t["not_configured"].format(name="Vikunja")))
        m = re.fullmatch(r"/p/(-?\d+)(/list|/add)?", path)
        if method == "GET" and path == "/projects":
            return self._send(200, self.render.projects_page(self.api.projects()))
        if method == "GET" and m and m.group(2) is None:
            return self._task_page(m.group(1))
        if method == "GET" and m and m.group(2) == "/list":
            html_ = self.render.task_list(m.group(1), self.api.open_tasks(m.group(1)))
            return self._send(200, html_, headers={"X-Updated": dt.datetime.now().strftime("%H:%M")})
        m_task = re.fullmatch(r"/t/(\d+)", path)
        if method == "GET" and m_task:
            return self._detail_page(int(m_task.group(1)))
        m_edit = re.fullmatch(r"/t/(\d+)/(rename|item|item-text)", path)
        if method == "POST" and m_edit:
            return self._edit(int(m_edit.group(1)), m_edit.group(2))
        if method == "POST" and m and m.group(2) == "/add":
            return self._add(m.group(1))
        if method == "POST" and path == "/check":
            return self._check()
        if method == "POST" and path == "/toggle":
            return self._toggle()
        self._send(404, self.render.message_page("404", path))

    # -- dashboard / KitchenOwl ---------------------------------------------

    def _tiles(self):
        """Dashboard tiles; a failing service only breaks its own tile."""
        render, tiles = self.render, []
        if self.calendar:
            try:
                tiles.append(render.calendar_tile(self.calendar.upcoming()))
            except ApiError as e:
                self.log_message("%s", e)
                tiles.append(render.error_tile(None, self.cfg.caldav_title or render.t["calendar"], e))
        if self.api:
            if not self.cfg.projects:
                tiles.append(render._tile("/projects", "Vikunja"))
            for pid in self.cfg.projects:
                try:
                    tiles.append(render.vikunja_tile(pid, self.api.project_title(pid), self.api.open_tasks(pid)))
                except ApiError as e:
                    self.log_message("%s", e)
                    tiles.append(render.error_tile(f"/p/{pid}", "Vikunja", e))
        if self.ko:
            try:
                tiles.append(render.shop_tile(self.ko.shopping_list()))
            except ApiError as e:
                self.log_message("%s", e)
                tiles.append(render.error_tile("/shop", "KitchenOwl", e))
        return "".join(tiles)

    def _shop_route(self, method, path):
        if method == "GET" and path == "/shop":
            return self._redirect(f"/s/{self.ko.shopping_list()['id']}")
        m = re.fullmatch(r"/s/(\d+)(/list|/add|/toggle|/pick|/suggest|/item/(\d+)/desc)?", path)
        if not m:
            return self._send(404, self.render.message_page("404", path))
        lid, action = m.group(1), m.group(2)
        if method == "GET" and action is None:
            lst = self.ko.shopping_list(lid)
            return self._send(200, self.render.shop_page(lst, self.render.shop_list(lst)))
        if method == "GET" and action == "/list":
            return self._send(200, self.render.shop_list(self.ko.shopping_list(lid)),
                              headers={"X-Updated": dt.datetime.now().strftime("%H:%M")})
        if method == "GET" and action == "/suggest":
            q = " ".join(self._query.get("q", [""])[0].split())[:128]
            return self._send(200, self.render.suggestions(lid, self.ko.search(q) if len(q) >= 2 else []))
        if method != "POST" or action is None:
            return self._send(404, self.render.message_page("404", path))
        form = self._form()
        form.setdefault("back", f"/s/{lid}")
        if action == "/add":
            name = " ".join(form.get("title", "").split())[:128]
            if not name:
                return self._send(400, "empty name", "text/plain") if self._ajax() else self._redirect(f"/s/{lid}")
            # "milk 2l": an existing item plus an amount -> add that item with the amount as its note
            for hit in self.ko.search(name):
                hit_name, desc = (hit.get("name") or "").lower(), (hit.get("description") or "").strip()
                if desc and hit_name and " ".join(name.lower().replace(hit_name, " ", 1).split()) == desc.lower():
                    self.ko.set_description(lid, hit["id"], desc)
                    break
            else:
                self.ko.add(lid, name)
        elif action == "/pick":
            if not form.get("id", "").isdigit():
                return self._send(400, "bad item id", "text/plain")
            self.ko.set_description(lid, form["id"], form.get("desc", "")[:250])
        elif action == "/toggle":
            if not form.get("id", "").isdigit():
                return self._send(400, "bad item id", "text/plain")
            if form.get("done") == "1":
                self.ko.remove(lid, form["id"])
            else:  # back onto the list (from the recent items), keeping its description
                self.ko.set_description(lid, form["id"], form.get("desc", "")[:250])
        else:
            self.ko.set_description(lid, m.group(3), " ".join(form.get("title", "").split())[:250])
        self._done_redirect(form)

    # -- actions -----------------------------------------------------------

    def _task_page(self, project_id):
        project = self.api.project(project_id)
        list_html = self.render.task_list(project_id, self.api.open_tasks(project_id))
        try:
            users = self.api.project_users(project_id)
        except ApiError as e:
            self.log_message("%s", e)
            users = []
        self._send(200, self.render.task_page(project_id, project.get("title") or "", list_html, users=users))

    def _detail_page(self, task_id):
        task = self.api.task(task_id)
        project_id = self._query.get("p", [""])[0]
        if not re.fullmatch(r"-?\d+", project_id):
            project_id = str(task.get("project_id"))
        try:
            project_title = self.api.project(project_id).get("title")
        except VikunjaError:
            project_title = None
        self._send(200, self.render.detail_page(task, project_id, project_title))

    def _check(self):
        form = self._form()
        try:
            task_id, index = int(form.get("id", "")), int(form.get("i", ""))
        except ValueError:
            return self._send(400, "bad task id or item", "text/plain")
        self.api.set_check(task_id, index, form.get("done") == "1")
        self._done_redirect(form)

    def _edit(self, task_id, what):
        form = self._form()
        text = " ".join(form.get("title", "").split())[:250]
        if not text:
            return self._send(400, "empty text", "text/plain")
        if what == "rename":
            self.api.rename(task_id, text)
        elif what == "item":
            self.api.add_check(task_id, text)
        else:
            try:
                index = int(form.get("i", ""))
            except ValueError:
                return self._send(400, "bad item", "text/plain")
            self.api.set_check_text(task_id, index, text)
        if not form.get("back"):
            form["back"] = f"/t/{task_id}"
        self._done_redirect(form)

    def _done_redirect(self, form):
        if self._ajax():
            return self._send(204, "")
        back = form.get("back", "/")
        self._redirect(back if re.fullmatch(r"/|/p/-?\d+|/t/\d+(\?p=-?\d+)?|/s/\d+", back) else "/")

    def _toggle(self):
        form = self._form()
        try:
            task_id = int(form.get("id", ""))
        except ValueError:
            return self._send(400, "bad task id", "text/plain")
        self.api.set_done(task_id, form.get("done") == "1")
        self._done_redirect(form)

    def _add(self, project_id):
        form = self._form()
        title = " ".join(form.get("title", "").split())
        parent = form.get("parent", "").strip()
        if not title:
            return self._send(400, "empty title", "text/plain") if self._ajax() else self._redirect(f"/p/{project_id}")
        if parent and not parent.isdigit():
            return self._send(400, "bad parent id", "text/plain")
        who = [w for w in form.get("who", "").split(",") if w]
        if not all(w.isdigit() for w in who):
            return self._send(400, "bad assignee", "text/plain")
        due = quick_due(form.get("due", ""), self.cfg.quick_due_time) if form.get("due") else None
        self.api.create_task(project_id, title[:250], int(parent) if parent else None, due=due, assignees=who)
        if self._ajax():
            return self._send(204, "")
        self._redirect(f"/p/{project_id}")


def make_server(cfg):
    handler = type("BoundHandler", (Handler,), {
        "cfg": cfg, "render": Renderer(cfg, IconFont(cfg) if cfg.kitchenowl and cfg.ko_icons else None),
        "api": Vikunja(cfg) if cfg.vikunja else None,
        "ko": KitchenOwl(cfg) if cfg.kitchenowl else None,
        "emoji": EmojiCache(cfg),
        "calendar": Calendar(cfg) if cfg.caldav else None})
    return ThreadingHTTPServer((cfg.host, cfg.port), handler)


def main():
    cfg = Config()
    if not cfg.vikunja and not cfg.kitchenowl and not cfg.caldav:
        sys.exit("Configure VIKUNJA_URL + VIKUNJA_TOKEN, KITCHENOWL_URL + KITCHENOWL_TOKEN "
                 "and/or CALDAV_URL (see .env.example)")
    server = make_server(cfg)
    services = ", ".join(f"{n} {u}" for n, u, on in (("Vikunja", cfg.api_url, cfg.vikunja),
                                                    ("KitchenOwl", cfg.ko_url, cfg.kitchenowl)) if on)
    print(f"kindle-vikunja listening on http://{cfg.host}:{cfg.port} -> {services}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
