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
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NULL_DATE_PREFIX = "0001-01-01"  # Vikunja's "no date"
MAX_DEPTH = 4  # subtask nesting shown on screen
PAGE_LIMIT = 50  # safety net for pagination loops

STRINGS = {
    "en": {
        "today": "today", "tomorrow": "tomorrow", "yesterday": "yesterday",
        "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "new_task": "New task…", "add": "Add", "subtask_of": "Subtask of",
        "none": "— none —", "empty": "Nothing to do.", "projects": "Projects",
        "refresh": "Refresh", "error": "Error", "offline": "offline",
        "forbidden": "Access key missing or wrong.",
        "pick": "Choose a project",
    },
    "de": {
        "today": "heute", "tomorrow": "morgen", "yesterday": "gestern",
        "weekdays": ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"],
        "new_task": "Neue Aufgabe…", "add": "Neu", "subtask_of": "Unteraufgabe von",
        "none": "— keine —", "empty": "Nichts zu tun.", "projects": "Projekte",
        "refresh": "Aktualisieren", "error": "Fehler", "offline": "offline",
        "forbidden": "Zugangsschlüssel fehlt oder ist falsch.",
        "pick": "Projekt wählen",
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
        self.default_project = env.get("VIKUNJA_PROJECT_ID", "").strip()
        self.host = env.get("LISTEN_HOST", "0.0.0.0")
        self.port = int(env.get("LISTEN_PORT", "8080"))
        self.access_key = env.get("ACCESS_KEY", "").strip()
        self.refresh = max(10, int(env.get("REFRESH_SECONDS", "60")))
        self.sort = env.get("SORT", "due").strip().lower()
        self.font_size = int(env.get("FONT_SIZE", "24"))
        self.lang = env.get("UI_LANG", "en").strip().lower()
        self.timeout = float(env.get("VIKUNJA_TIMEOUT", "15"))
        self.verify_tls = env.get("VIKUNJA_VERIFY_TLS", "1").lower() not in ("0", "false", "no")


# --------------------------------------------------------------------------
# Vikunja API client
# --------------------------------------------------------------------------

class VikunjaError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class Vikunja:
    def __init__(self, cfg):
        self.cfg = cfg
        self._ctx = None if cfg.verify_tls else ssl._create_unverified_context()

    def request(self, method, path, params=None, body=None):
        url = self.cfg.api_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        headers = {"Authorization": "Bearer " + self.cfg.token, "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout, context=self._ctx) as resp:
                raw = resp.read()
                return (json.loads(raw) if raw else None), resp.headers
        except urllib.error.HTTPError as e:
            msg = e.reason
            try:
                msg = json.loads(e.read()).get("message", msg)
            except Exception:
                pass
            raise VikunjaError(f"Vikunja {method} {path}: {e.code} {msg}", e.code) from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise VikunjaError(f"Cannot reach Vikunja: {e}") from None

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
                    items = self.get_all(f"/projects/{project_id}/views/{view['id']}/tasks",
                                         {"filter": "done = false"})
                    return _flatten_buckets(items)
            return self.get_all("/tasks/all", {"filter": f"done = false && project = {int(project_id)}"})

        return self.get_all(f"/projects/{project_id}/tasks", {
            "filter_by": "done", "filter_value": "false", "filter_comparator": "equals"})

    def set_done(self, task_id, done):
        # Vikunja's update overwrites every field, so send back the full task.
        task = self.request("GET", f"/tasks/{task_id}")[0]
        task["done"] = bool(done)
        return self.request("POST", f"/tasks/{task_id}", body=task)[0]

    def create_task(self, project_id, title, parent_id=None):
        task = self.request("PUT", f"/projects/{project_id}/tasks",
                            body={"title": title, "project_id": int(project_id)})[0]
        if parent_id:
            self.request("PUT", f"/tasks/{parent_id}/relations", body={
                "task_id": int(parent_id), "other_task_id": task["id"], "relation_kind": "subtask"})
        return task


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


CSS = """
*{-webkit-box-sizing:border-box;box-sizing:border-box}
html,body{background:#fff;color:#000}
body{margin:0;padding:10px 16px 60px;font:%%FS%%px/1.3 sans-serif;-webkit-text-size-adjust:none}
a{color:#000}
#hd{display:table;width:100%;border-bottom:4px solid #000;padding-bottom:4px;margin-bottom:8px}
#hd h1{display:table-cell;font-size:1.35em;margin:0;vertical-align:bottom}
#hd .nav{display:table-cell;text-align:right;vertical-align:bottom;font-size:.7em;white-space:nowrap}
#hd .nav a{display:inline-block;padding:10px 0 4px 16px}
#status{font-size:.7em;min-height:1.2em}
#status.err{font-weight:bold}
.banner{border:3px solid #000;padding:8px;margin:8px 0;font-weight:bold}
#add{margin:0 0 10px}
#add .line{display:table;width:100%}
#add .in{display:table-cell;width:100%;vertical-align:middle}
#add .go{display:table-cell;padding-left:8px;vertical-align:middle}
#add input[type=text]{width:100%;font:inherit;padding:10px;border:3px solid #000;border-radius:0;-webkit-appearance:none;background:#fff;color:#000}
#add button{font:inherit;font-weight:bold;padding:10px 18px;border:3px solid #000;background:#000;color:#fff;border-radius:0;-webkit-appearance:none}
#add .par{font-size:.7em;margin-top:6px}
#add select{font-size:1em;max-width:75%}
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
.empty{padding:40px 0;text-align:center;font-size:1.1em}
.plist a{display:block;padding:14px 0;border-bottom:1px solid #000;text-decoration:none}
"""

# Plain ES5 only: the Kindle's WebKit is from ~2012.
JS = r"""
(function () {
  var CFG = %%CFG%%;
  var IDLE = 15000, loadedAt = now(), lastAct = 0, lastRefresh = now(), busy = false;
  var current = null;

  function now() { return new Date().getTime(); }
  function $(id) { return document.getElementById(id); }
  function hasCls(el, c) { return el && el.className && (" " + el.className + " ").indexOf(" " + c + " ") >= 0; }
  function closest(el, c) { while (el && el !== document) { if (hasCls(el, c)) return el; el = el.parentNode; } return null; }
  function status(msg, isErr) { var s = $("status"); s.textContent = msg || ""; s.className = isErr ? "err" : ""; }
  function touch() { lastAct = now(); }

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

  function rebuildParents() {
    var sel = $("parent");
    if (!sel) return;
    var keep = sel.value, lis = $("list").getElementsByTagName("li");
    while (sel.options.length > 1) sel.remove(1);
    for (var i = 0; i < lis.length; i++) {
      var li = lis[i];
      if (!li.getAttribute("data-id") || hasCls(li, "done")) continue;
      var o = document.createElement("option"), pre = "";
      for (var d = 0; d < +li.getAttribute("data-depth"); d++) pre += "– ";
      o.value = li.getAttribute("data-id");
      o.text = pre + li.getAttribute("data-title");
      sel.appendChild(o);
    }
    sel.value = keep;
    if (sel.value !== keep) sel.value = "";
  }

  function refresh() {
    if (busy) return;
    busy = true;
    xhr("GET", "/p/" + CFG.pid + "/list", null, function (ok, r) {
      busy = false;
      lastRefresh = now();
      if (!ok) { status(errText(r), true); return; }
      // Only touch the DOM when something changed: every redraw flashes e-ink.
      if (r.responseText !== current) {
        $("list").innerHTML = r.responseText;
        current = r.responseText;
        rebuildParents();
      }
      $("upd").textContent = r.getResponseHeader("X-Updated") || "";
      status("");
    });
  }

  document.addEventListener("submit", function (ev) {
    var f = ev.target;
    touch();
    if (hasCls(f, "t")) {
      ev.preventDefault();
      var li = closest(f, "task"), field = f.elements["done"], target = field.value === "1";
      var body = serialize(f);
      li.className = target ? "task done" : "task";
      field.value = target ? "0" : "1";
      xhr("POST", "/toggle", body, function (ok, r) {
        if (ok) { status(""); lastRefresh = 0; return; }   // re-sync once idle
        li.className = target ? "task" : "task done";
        field.value = target ? "1" : "0";
        status(errText(r), true);
      });
    } else if (f.id === "add") {
      ev.preventDefault();
      var title = f.elements["title"];
      if (!title.value.replace(/\s+/g, "")) return;
      status("…");
      xhr("POST", f.action, serialize(f), function (ok, r) {
        if (!ok) { status(errText(r), true); return; }
        title.value = "";
        f.elements["parent"].value = "";
        title.blur();
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
    // Full reload now and then: frees browser memory and clears e-ink ghosting.
    if (t - loadedAt > CFG.reloadHours * 3600000) { location.reload(); return; }
    if (t - lastRefresh >= CFG.refresh * 1000) refresh();
  }, 5000);

  current = $("list").innerHTML;
  rebuildParents();
})();
"""


class Renderer:
    def __init__(self, cfg):
        self.cfg = cfg
        self.t = STRINGS.get(cfg.lang, STRINGS["en"])

    # -- building blocks ---------------------------------------------------

    def layout(self, title, body, script=""):
        css = CSS.replace("%%FS%%", str(self.cfg.font_size))
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
        if (d.hour, d.minute) != (0, 0):
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
        sub = kids.get(t["id"])
        if sub:
            parts.append(f'<span class="cnt">{sum(1 for s in sub if s.get("done"))}/{len(sub)}</span>')
        for label in t.get("labels") or []:
            parts.append(f'<span class="lbl">{esc(label.get("title"))}</span>')
        return f' <span class="meta">{"".join(parts)}</span>' if parts else ""

    def _node(self, t, kids, back, depth, seen, parents, now):
        tid, done = t["id"], bool(t.get("done"))
        seen.add(tid)
        if not done:
            parents.append((tid, depth, t.get("title") or ""))
        out = [
            f'<li class="task{" done" if done else ""}" data-id="{tid}" data-depth="{depth}"'
            f' data-title="{esc(t.get("title"))}"><div class="row">'
            '<form class="t" method="post" action="/toggle">'
            f'<input type="hidden" name="id" value="{tid}">'
            f'<input type="hidden" name="done" value="{"0" if done else "1"}">'
            f'<input type="hidden" name="back" value="{esc(back)}">'
            '<button type="submit" class="cb"><span class="mark"></span></button></form>'
            f'<div class="body"><span class="title">{esc(t.get("title"))}</span>'
            f"{self._meta(t, kids, now)}</div></div>"
        ]
        sub = [c for c in kids.get(tid, []) if c.get("id") not in seen] if depth < MAX_DEPTH else []
        if sub:
            out.append('<ul class="sub">')
            for c in sub:
                if c["id"] not in seen:
                    out.append(self._node(c, kids, back, depth + 1, seen, parents, now))
            out.append("</ul>")
        out.append("</li>")
        return "".join(out)

    # -- pages -------------------------------------------------------------

    def task_list(self, project_id, tasks, now=None):
        """The refreshable part of the page. Returns (html, parent_choices)."""
        now = now or dt.datetime.now().astimezone()
        roots, kids = build_tree(tasks, self.cfg.sort)
        back = f"/p/{project_id}"
        seen, parents, items = set(), [], []
        for t in roots:
            if t["id"] not in seen:
                items.append(self._node(t, kids, back, 0, seen, parents, now))
        for t in tasks:  # anything unreachable (e.g. relation cycles)
            if t.get("id") is not None and t["id"] not in seen and not t.get("done"):
                items.append(self._node(t, kids, back, 0, seen, parents, now))
        if not items:
            return f'<p class="empty">{esc(self.t["empty"])}</p>', parents
        return '<ul id="tasks">' + "".join(items) + "</ul>", parents

    def task_page(self, project_id, title, list_html, parents, error=None):
        t = self.t
        options = "".join(
            f'<option value="{pid}">{"– " * depth}{esc(ptitle)}</option>'
            for pid, depth, ptitle in parents
        )
        banner = f'<div class="banner">{esc(error)}</div>' if error else ""
        body = (
            f'<div id="hd"><h1>{esc(title)}</h1><div class="nav">'
            f'<span id="upd">{dt.datetime.now().strftime("%H:%M")}</span>'
            f'<a href="/p/{project_id}">{esc(t["refresh"])}</a>'
            f'<a href="/projects">{esc(t["projects"])}</a></div></div>'
            f'{banner}<form id="add" method="post" action="/p/{project_id}/add">'
            '<div class="line"><div class="in">'
            f'<input type="text" id="title" name="title" autocomplete="off" placeholder="{esc(t["new_task"])}">'
            f'</div><div class="go"><button type="submit">{esc(t["add"])}</button></div></div>'
            f'<div class="par">{esc(t["subtask_of"])}: <select id="parent" name="parent">'
            f'<option value="">{esc(t["none"])}</option>{options}</select></div></form>'
            f'<div id="status"></div><div id="list">{list_html}</div>'
        )
        cfg = json.dumps({
            "pid": str(project_id), "refresh": self.cfg.refresh, "reloadHours": 6,
            "error": t["error"], "offline": t["offline"],
        })
        return self.layout(title, body, "<script>" + JS.replace("%%CFG%%", cfg) + "</script>")

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
    api = None
    render = None

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    # -- plumbing ----------------------------------------------------------

    def _send(self, status, body, ctype="text/html; charset=utf-8", headers=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
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
        if not self._authorized(urllib.parse.parse_qs(url.query)):
            return self._send(403, self.render.message_page("403", self.render.t["forbidden"]))
        try:
            self._route(method, path)
        except VikunjaError as e:
            self.log_message("%s", e)
            if self._ajax() or path.endswith("/list"):
                self._send(502, str(e), "text/plain; charset=utf-8")
            else:
                self._send(502, self.render.message_page(self.render.t["error"], str(e)))

    def _route(self, method, path):
        m = re.fullmatch(r"/p/(-?\d+)(/list|/add)?", path)
        if method == "GET" and path == "/":
            dp = self.cfg.default_project
            return self._redirect(f"/p/{dp}" if dp else "/projects")
        if method == "GET" and path == "/projects":
            return self._send(200, self.render.projects_page(self.api.projects()))
        if method == "GET" and m and m.group(2) is None:
            return self._task_page(m.group(1))
        if method == "GET" and m and m.group(2) == "/list":
            html_, _ = self.render.task_list(m.group(1), self.api.open_tasks(m.group(1)))
            return self._send(200, html_, headers={"X-Updated": dt.datetime.now().strftime("%H:%M")})
        if method == "POST" and m and m.group(2) == "/add":
            return self._add(m.group(1))
        if method == "POST" and path == "/toggle":
            return self._toggle()
        self._send(404, self.render.message_page("404", path))

    # -- actions -----------------------------------------------------------

    def _task_page(self, project_id):
        project = self.api.project(project_id)
        list_html, parents = self.render.task_list(project_id, self.api.open_tasks(project_id))
        self._send(200, self.render.task_page(project_id, project.get("title") or "", list_html, parents))

    def _toggle(self):
        form = self._form()
        try:
            task_id = int(form.get("id", ""))
        except ValueError:
            return self._send(400, "bad task id", "text/plain")
        self.api.set_done(task_id, form.get("done") == "1")
        if self._ajax():
            return self._send(204, "")
        back = form.get("back", "/")
        self._redirect(back if re.fullmatch(r"/p/-?\d+", back) else "/")

    def _add(self, project_id):
        form = self._form()
        title = " ".join(form.get("title", "").split())
        parent = form.get("parent", "").strip()
        if not title:
            return self._send(400, "empty title", "text/plain") if self._ajax() else self._redirect(f"/p/{project_id}")
        if parent and not parent.isdigit():
            return self._send(400, "bad parent id", "text/plain")
        self.api.create_task(project_id, title[:250], int(parent) if parent else None)
        if self._ajax():
            return self._send(204, "")
        self._redirect(f"/p/{project_id}")


def make_server(cfg):
    handler = type("BoundHandler", (Handler,), {
        "cfg": cfg, "api": Vikunja(cfg), "render": Renderer(cfg)})
    return ThreadingHTTPServer((cfg.host, cfg.port), handler)


def main():
    cfg = Config()
    if not cfg.api_url or not cfg.token:
        sys.exit("VIKUNJA_URL and VIKUNJA_TOKEN must be set (see .env.example)")
    server = make_server(cfg)
    print(f"kindle-vikunja listening on http://{cfg.host}:{cfg.port} -> {cfg.api_url}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
