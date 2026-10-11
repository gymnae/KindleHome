"""End-to-end tests against a fake Vikunja API.  Run: python3 -m unittest discover tests"""

import html
import json
import os
import re
import struct
import sys
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import kindlehome as kv  # noqa: E402


class FakeVikunja:
    """Minimal in-memory imitation of the Vikunja >= 0.24 API."""

    def __init__(self):
        self.tasks = {
            1: {"id": 1, "title": "Buy milk", "done": False, "project_id": 1, "description": "keep me",
                "priority": 4, "due_date": "2020-01-01T10:00:00Z", "labels": [{"title": "shop"}],
                "assignees": [{"id": 1, "username": "anna", "name": "Anna <A>"},
                              {"id": 2, "username": "ben", "name": ""}]},
            2: {"id": 2, "title": "Clean <kitchen>", "done": False, "project_id": 1,
                "due_date": "0001-01-01T00:00:00Z",
                "description": '<p>Use <b>soap</b><script>alert(1)</script><img src="x" onerror="y"></p>'
                               '<ul data-type="taskList"><li data-checked="true" data-type="taskItem">'
                               '<label><input type="checkbox" checked></label><div><p>sink</p></div></li></ul>'
                               '<a href="javascript:evil()">link</a>'},
            3: {"id": 3, "title": "Wipe counters", "done": False, "project_id": 1},
            4: {"id": 4, "title": "Dishes", "done": True, "project_id": 1},
            5: {"id": 5, "title": "Other project", "done": False, "project_id": 2},
        }
        self.subtasks = {2: [3, 4]}  # parent -> children
        self.next_id = 6
        self.assigned = []           # (task id, user id)

    def with_relations(self, task):
        t = dict(task)
        rel = {}
        kids = self.subtasks.get(t["id"], [])
        if kids:
            rel["subtask"] = [dict(self.tasks[k]) for k in kids]
        parents = [p for p, ks in self.subtasks.items() if t["id"] in ks]
        if parents:
            rel["parenttask"] = [dict(self.tasks[p]) for p in parents]
        t["related_tasks"] = rel or None
        return t

    def handler(fake):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, code, data, headers=None):
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def body(self):
                return json.loads(self.rfile.read(int(self.headers["Content-Length"])))

            def check_auth(self):
                if self.headers.get("Authorization") != "Bearer secret":
                    self.reply(401, {"message": "bad token"})
                    return False
                return True

            def do_GET(self):
                if not self.check_auth():
                    return
                u = urllib.parse.urlsplit(self.path)
                q = urllib.parse.parse_qs(u.query)
                p = u.path
                if p == "/api/v1/projects":
                    return self.reply(200, [{"id": 1, "title": "Home"},
                                            {"id": 7, "title": "Sub", "parent_project_id": 1}])
                if p == "/api/v1/projects/1":
                    return self.reply(200, {"id": 1, "title": "Home",
                                            "owner": {"id": 1, "username": "anna", "name": "Anna Example"}})
                if p == "/api/v1/projects/1/projectusers":   # like an API token without access to it
                    return self.reply(401, {"message": "missing permission"})
                if p == "/api/v1/projects/1/users":
                    return self.reply(200, [{"id": 2, "username": "ben", "name": "Ben Smith", "permission": 1},
                                            {"id": 1, "username": "anna", "name": "Anna Example"}])
                if p == "/api/v1/projects/1/teams":
                    return self.reply(200, [{"id": 5, "name": "Family"}])
                if p == "/api/v1/teams/5":
                    return self.reply(200, {"id": 5, "members": [{"id": 3, "username": "cara", "name": ""}]})
                if p == "/api/v1/projects/1/views":
                    return self.reply(200, [{"id": 9, "view_kind": "kanban"}, {"id": 10, "view_kind": "list"}])
                if p == "/api/v1/projects/1/views/10/tasks":
                    assert q["filter"] == ["done = false"], q
                    fake.last_query = q
                    items = [fake.with_relations(t) for t in fake.tasks.values()
                             if t["project_id"] == 1 and not t["done"]]
                    page, per = int(q["page"][0]), 2  # force pagination
                    pages = max(1, -(-len(items) // per))
                    return self.reply(200, items[(page - 1) * per: page * per],
                                      {"x-pagination-total-pages": str(pages)})
                m = re.fullmatch(r"/api/v1/tasks/(\d+)", p)
                if m and int(m.group(1)) in fake.tasks:
                    return self.reply(200, fake.with_relations(fake.tasks[int(m.group(1))]))
                self.reply(404, {"message": "not found"})

            def do_POST(self):
                if not self.check_auth():
                    return
                m = re.fullmatch(r"/api/v1/tasks/(\d+)", self.path)
                if m:
                    data = self.body()
                    data.pop("related_tasks", None)
                    fake.tasks[int(m.group(1))] = data  # full replace, like Vikunja
                    return self.reply(200, data)
                self.reply(404, {"message": "not found"})

            def do_PUT(self):
                if not self.check_auth():
                    return
                m = re.fullmatch(r"/api/v1/tasks/(\d+)/assignees", self.path)
                if m:
                    fake.assigned.append((int(m.group(1)), self.body()["user_id"]))
                    return self.reply(201, {})
                if self.path == "/api/v1/projects/1/tasks":
                    data = self.body()
                    t = dict(data, id=fake.next_id, done=False, project_id=1)
                    fake.tasks[t["id"]] = t
                    fake.next_id += 1
                    return self.reply(201, t)
                m = re.fullmatch(r"/api/v1/tasks/(\d+)/relations", self.path)
                if m:
                    data = self.body()
                    assert data["relation_kind"] == "subtask"
                    fake.subtasks.setdefault(int(m.group(1)), []).append(data["other_task_id"])
                    return self.reply(201, data)
                self.reply(404, {"message": "not found"})

        return H


def make_ttf(codepoints):
    """A minimal TrueType file: just a table directory and a format 4 cmap."""
    cps = sorted(codepoints) + [0xFFFF]
    n = len(cps)
    zeros = struct.pack(f">{n}H", *[0] * n)
    sub = (struct.pack(">7H", 4, 16 + 8 * n, 0, 2 * n, 0, 0, 0) + struct.pack(f">{n}H", *cps)
           + struct.pack(">H", 0) + struct.pack(f">{n}H", *cps) + zeros + zeros)
    cmap = struct.pack(">HH", 0, 1) + struct.pack(">HHI", 3, 1, 12) + sub
    return struct.pack(">IHHHH", 0x00010000, 1, 0, 0, 0) + struct.pack(">4sIII", b"cmap", 0, 28, len(cmap)) + cmap


ICON_DART = """
class ItemIcons {
  static const Map<String, IconData> map = {
    "apple": apple,
    "milk": milk,
    "soap": soap,
  };
  static const IconData apple = IconData(256, fontFamily: _kFontFam, fontPackage: _kFontPkg);
  static const IconData milk = IconData(257, fontFamily: _kFontFam, fontPackage: _kFontPkg);
  static const IconData soap = IconData(0x102, fontFamily: _kFontFam, fontPackage: _kFontPkg);
}
"""


class FakeKitchenOwl:
    """Minimal in-memory imitation of the KitchenOwl API."""

    def __init__(self):
        self.catalog = {
            1: {"id": 1, "name": "Milk", "icon": "milk", "category": {"id": 2, "name": "Dairy", "ordering": 1}},
            2: {"id": 2, "name": "Apples", "icon": "apple", "category": {"id": 1, "name": "Fruit", "ordering": 0}},
            3: {"id": 3, "name": "Soap", "category": None},
            4: {"id": 4, "name": "Bread <fresh>"},
        }
        self.on_list = {1: "2 l", 2: "", 3: ""}   # item id -> description
        self.recent = [4, 1]                        # recently removed items
        self.requests = []
        self.down = False
        self.font = make_ttf([256, 257, 258])
        self.list_name = "Groceries"

    def item(self, iid, description=""):
        return dict(self.catalog[iid], description=description)

    def handler(fake):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, code, data, ctype="application/json"):
                body = data if isinstance(data, bytes) else json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.end_headers()
                self.wfile.write(body)

            def body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n)) if n else {}

            def handle_any(self, method):
                if fake.down:
                    return self.reply(500, {"msg": "boom"})
                u = urllib.parse.urlsplit(self.path)
                if u.path == "/assets/fonts/Items.ttf":   # static web app assets: no auth
                    return self.reply(200, fake.font, "font/ttf")
                if u.path == "/src/item_icons.dart":
                    return self.reply(200, ICON_DART.encode(), "text/plain")
                if u.path == "/emoji/1f6cd.png":
                    return self.reply(200, b"\x89PNG fake", "image/png")
                if self.headers.get("Authorization") != "Bearer ko-secret":
                    return self.reply(401, {"msg": "Unauthorized"})
                body = self.body()
                fake.requests.append((method, u.path, body))
                if method == "GET" and u.path == "/api/household":
                    return self.reply(200, [{"id": 4, "name": "Home"}])
                if method == "GET" and u.path == "/api/household/4/shoppinglist":
                    items = [fake.item(i, d) for i, d in fake.on_list.items()]
                    recent = [fake.item(i) for i in fake.recent]
                    return self.reply(200, [{"id": 9, "name": "Hardware", "items": [], "recentItems": []},
                                            {"id": 7, "name": fake.list_name, "items": items, "recentItems": recent}])
                if method == "GET" and u.path == "/api/household/4/item/search":
                    q = urllib.parse.parse_qs(u.query)["query"][0]
                    name, _, desc = q.partition(" ")
                    return self.reply(200, [fake.item(i, desc) for i, it in fake.catalog.items()
                                            if name.lower() in it["name"].lower()])
                if method == "POST" and u.path == "/api/shoppinglist/7/add-item-by-name":
                    match = [i for i, it in fake.catalog.items() if it["name"].lower() == body["name"].lower()]
                    iid = match[0] if match else max(fake.catalog) + 1
                    fake.catalog.setdefault(iid, {"id": iid, "name": body["name"]})
                    fake.on_list[iid] = body.get("description", "")
                    return self.reply(200, fake.item(iid))
                if method == "DELETE" and u.path == "/api/shoppinglist/7/item":
                    iid = body["item_id"]
                    fake.on_list.pop(iid, None)
                    fake.recent.insert(0, iid)
                    return self.reply(200, {"msg": "DONE"})
                m = re.fullmatch(r"/api/shoppinglist/7/item/(\d+)", u.path)
                if method == "POST" and m:
                    fake.on_list[int(m.group(1))] = body["description"]
                    return self.reply(200, fake.item(int(m.group(1)), body["description"]))
                self.reply(404, {"msg": "not found"})

            def do_GET(self):
                self.handle_any("GET")

            def do_POST(self):
                self.handle_any("POST")

            def do_DELETE(self):
                self.handle_any("DELETE")

        return H


def serve(server):
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class ServerTest(unittest.TestCase):
    access_key = ""
    extra_env = {}
    vikunja = True
    kitchenowl = False

    def setUp(self):
        env = {"LISTEN_HOST": "127.0.0.1", "LISTEN_PORT": "0", "ACCESS_KEY": self.access_key}
        self.servers = []
        self.fake = FakeVikunja()
        if self.vikunja:
            self.vik = serve(ThreadingHTTPServer(("127.0.0.1", 0), self.fake.handler()))
            self.servers.append(self.vik)
            env.update({"VIKUNJA_URL": f"http://127.0.0.1:{self.vik.server_port}",
                        "VIKUNJA_TOKEN": "secret", "VIKUNJA_PROJECT_ID": "1"})
        self.ko = FakeKitchenOwl()
        if self.kitchenowl:
            ko = serve(ThreadingHTTPServer(("127.0.0.1", 0), self.ko.handler()))
            self.servers.append(ko)
            env.update({"KITCHENOWL_URL": f"http://127.0.0.1:{ko.server_port}", "KITCHENOWL_TOKEN": "ko-secret",
                        "KITCHENOWL_LIST_ID": "7",
                        "KITCHENOWL_ICON_MAP_URL": f"http://127.0.0.1:{ko.server_port}/src/item_icons.dart",
                        "EMOJI_URL": f"http://127.0.0.1:{ko.server_port}/emoji/{{code}}.png"})
        env.update(self.extra_env)
        self.app = serve(kv.make_server(kv.Config(env)))
        self.servers.append(self.app)
        self.base = f"http://127.0.0.1:{self.app.server_port}"
        self.opener = urllib.request.build_opener(NoRedirect)

    def tearDown(self):
        for s in reversed(self.servers):
            s.shutdown()
            s.server_close()

    def req(self, path, data=None, headers=None):
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        r = urllib.request.Request(self.base + path, data=body, headers=headers or {})
        try:
            resp = self.opener.open(r)
            return resp.status, resp.read().decode("utf-8", "replace"), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace"), e.headers


class TaskTests(ServerTest):
    extra_env = {"LIST_SUBTASKS": "1"}  # nested subtasks in the list (default: detail page only)

    def test_root_redirects_to_default_project(self):
        status, _, headers = self.req("/")
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/p/1")

    def test_page_renders_tree(self):
        status, body, _ = self.req("/p/1")
        self.assertEqual(status, 200)
        self.assertIn("<h1>Home</h1>", body)
        self.assertIn("Clean &lt;kitchen&gt;", body)       # escaped
        self.assertNotIn("Other project", body)
        # subtasks nested inside their parent, done subtask included
        parent = body.index('data-id="2"')
        self.assertLess(parent, body.index('data-id="3"'))
        self.assertIn('class="task done" data-id="4"', body)
        self.assertIn('<span class="cnt">2/3</span>', body)  # 1/2 subtasks + 1/1 checklist
        self.assertEqual(body.count('data-id="3"'), 1)     # not duplicated as a root
        self.assertIn('<span class="prio">!!</span>', body)
        self.assertIn('class="due over"', body)
        self.assertIn('<span class="lbl">shop</span>', body)
        self.assertIn('<span class="who">Anna &lt;A&gt;, ben</span>', body)   # name, else username
        self.assertEqual(body.count('class="who"'), 1)
        self.assertNotIn('<select', body)   # no "subtask of" picker on the main page

    def test_fragment(self):
        status, body, headers = self.req("/p/1/list")
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith('<ul id="tasks">'))
        self.assertRegex(headers["X-Updated"], r"^\d\d:\d\d$")

    def test_toggle_keeps_other_fields(self):
        status, _, _ = self.req("/toggle", {"id": "1", "done": "1", "back": "/p/1"},
                                {"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(status, 204)
        self.assertTrue(self.fake.tasks[1]["done"])
        self.assertEqual(self.fake.tasks[1]["description"], "keep me")
        _, body, _ = self.req("/p/1/list")
        self.assertNotIn("Buy milk", body)

    def test_toggle_subtask_back_to_open_without_js(self):
        status, _, headers = self.req("/toggle", {"id": "4", "done": "0", "back": "/p/1"})
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/p/1")
        self.assertFalse(self.fake.tasks[4]["done"])

    def test_toggle_rejects_open_redirect(self):
        _, _, headers = self.req("/toggle", {"id": "4", "done": "0", "back": "http://evil"})
        self.assertEqual(headers["Location"], "/")

    def test_add_task_and_subtask(self):
        ajax = {"X-Requested-With": "XMLHttpRequest"}
        self.assertEqual(self.req("/p/1/add", {"title": "  New   thing ", "parent": ""}, ajax)[0], 204)
        self.assertEqual(self.req("/p/1/add", {"title": "Mop floor", "parent": "2"}, ajax)[0], 204)
        self.assertEqual(self.fake.tasks[6]["title"], "New thing")
        self.assertIn(7, self.fake.subtasks[2])
        _, body, _ = self.req("/p/1/list")
        self.assertLess(body.index('data-id="2"'), body.index('data-id="7"'))

    def test_add_empty_title_is_rejected(self):
        status, _, _ = self.req("/p/1/add", {"title": "   "}, {"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(status, 400)
        self.assertEqual(len(self.fake.tasks), 5)

    def test_projects_page(self):
        status, body, _ = self.req("/projects")
        self.assertEqual(status, 200)
        self.assertIn('href="/p/7" style="padding-left:1.2em">Sub', body)

    def test_vikunja_error_is_reported(self):
        status, body, _ = self.req("/p/1/list".replace("1", "99"))
        self.assertEqual(status, 502)
        self.assertIn("404", body)


class DetailTests(ServerTest):
    def test_list_rows_link_to_details(self):
        _, body, _ = self.req("/p/1/list")
        self.assertIn('<a class="open" href="/t/2?p=1">', body)
        self.assertNotIn('data-id="3"', body)   # subtasks only on the detail page by default
        self.assertIn('<span class="more">\u00b6</span>', body)    # has description

    def test_detail_page(self):
        status, body, _ = self.req("/t/2?p=1")
        self.assertEqual(status, 200)
        self.assertIn("\u2039 Home</a>", body)
        self.assertIn('<span class="title edit" data-url="/t/2/rename">Clean &lt;kitchen&gt;</span>', body)
        # description is sanitized
        self.assertIn("<p>Use <strong>soap</strong></p>", body)
        self.assertNotIn("alert(1)", body)
        self.assertNotIn("onerror", body)
        self.assertNotIn("javascript:", body)
        self.assertIn('<li class="ck on"><form class="ckf" method="post" action="/check">'
                      '<input type="hidden" name="id" value="2"><input type="hidden" name="i" value="0">'
                      '<input type="hidden" name="done" value="0">', body)
        self.assertIn('<p class="edit" data-url="/t/2/item-text" data-i="0">sink</p>', body)
        # subtasks are tickable rows; text renames, the arrow opens them
        self.assertIn('name="id" value="3"', body)
        self.assertIn('class="task done" data-id="4"', body)
        self.assertIn('<span class="title edit" data-url="/t/4/rename">Dishes</span>', body)
        self.assertIn('<a class="arrow" href="/t/4?p=1">', body)
        self.assertNotIn('<a class="open" href="/t/4', body)
        # new subtasks go into the description
        self.assertIn('<form id="add" method="post" action="/t/2/item"', body)
        self.assertNotIn('name="parent"', body)
        self.assertIn('"home": "/p/1"', body)

    def test_detail_of_subtask_links_parent(self):
        _, body, _ = self.req("/t/3")
        self.assertIn('href="/t/2?p=1">Clean &lt;kitchen&gt;</a>', body)
        self.assertNotIn('class="desc"', body)

    def test_toggle_from_detail_returns_there(self):
        _, _, headers = self.req("/toggle", {"id": "3", "done": "1", "back": "/t/2?p=1"})
        self.assertEqual(headers["Location"], "/t/2?p=1")
        self.assertTrue(self.fake.tasks[3]["done"])

    def test_missing_task(self):
        self.assertEqual(self.req("/t/999")[0], 502)


class FlatListTests(ServerTest):
    def test_subtasks_only_in_details_by_default(self):
        _, body, _ = self.req("/p/1/list")
        self.assertIn('data-id="2"', body)
        self.assertNotIn('data-id="3"', body)
        self.assertIn('<span class="cnt">2/3</span>', body)


class ChecklistTests(ServerTest):
    ajax = {"X-Requested-With": "XMLHttpRequest"}

    def test_untick_html_checklist_item(self):
        status, _, _ = self.req("/check", {"id": "2", "i": "0", "done": "0", "back": "/t/2?p=1"}, self.ajax)
        self.assertEqual(status, 204)
        desc = self.fake.tasks[2]["description"]
        self.assertIn('data-checked="false"', desc)
        self.assertIn('<input type="checkbox">', desc)
        self.assertIn("<script>alert(1)</script>", desc)   # rest of the description untouched
        self.assertEqual(self.fake.tasks[2]["title"], "Clean <kitchen>")

    def test_tick_plain_markdown_item_without_js(self):
        self.fake.tasks[1]["description"] = "Shopping:\n- [ ] eggs\n- [x] milk\n* [ ] bread"
        _, body, _ = self.req("/t/1?p=1")
        self.assertIn('<div class="ck"><form class="ckf"', body)
        self.assertIn('<span class="ckt edit" data-url="/t/1/item-text" data-i="2">bread</span>', body)
        status, _, headers = self.req("/check", {"id": "1", "i": "2", "done": "1", "back": "/t/1?p=1"})
        self.assertEqual((status, headers["Location"]), (303, "/t/1?p=1"))
        self.assertEqual(self.fake.tasks[1]["description"], "Shopping:\n- [ ] eggs\n- [x] milk\n* [x] bread")
        _, body, _ = self.req("/p/1/list")
        self.assertIn('<span class="cnt">2/3</span>', body)

    def test_rename_task(self):
        status, _, _ = self.req("/t/1/rename", {"title": "  Buy  oat milk "}, self.ajax)
        self.assertEqual(status, 204)
        self.assertEqual(self.fake.tasks[1]["title"], "Buy oat milk")
        self.assertEqual(self.fake.tasks[1]["description"], "keep me")
        self.assertEqual(self.req("/t/1/rename", {"title": "  "}, self.ajax)[0], 400)

    def test_rename_without_js_redirects_back(self):
        status, _, headers = self.req("/t/3/rename", {"title": "Wipe all", "back": "/t/2?p=1"})
        self.assertEqual((status, headers["Location"]), (303, "/t/2?p=1"))
        self.assertEqual(self.fake.tasks[3]["title"], "Wipe all")

    def test_edit_html_checklist_text(self):
        status, _, _ = self.req("/t/2/item-text", {"i": "0", "title": "sink & tap"}, self.ajax)
        self.assertEqual(status, 204)
        desc = self.fake.tasks[2]["description"]
        self.assertIn("<div><p>sink &amp; tap</p></div>", desc)
        self.assertIn('data-checked="true"', desc)

    def test_add_checklist_item(self):
        self.req("/t/2/item", {"title": "fridge"}, self.ajax)
        desc = self.fake.tasks[2]["description"]
        self.assertEqual(kv.checklist_progress(desc), (1, 2))
        self.assertLess(desc.index(">sink<"), desc.index(">fridge<"))
        self.assertLess(desc.index(">fridge<"), desc.index("</ul>"))   # inside the existing list
        self.req("/t/3/item", {"title": "first"}, self.ajax)               # empty description
        self.assertEqual(kv.checklist_progress(self.fake.tasks[3]["description"]), (0, 1))
        self.assertEqual(len(self.fake.tasks), 5)   # no separate subtask tasks were created

    def test_missing_item_is_an_error(self):
        status, body, _ = self.req("/check", {"id": "2", "i": "5", "done": "1"}, self.ajax)
        self.assertEqual(status, 502)
        self.assertIn("reload", body)


class OrderTests(ServerTest):
    def test_drag_and_drop_order_by_default(self):
        self.fake.tasks[1]["position"] = 300.0
        self.fake.tasks[2]["position"] = 100.5
        _, body, _ = self.req("/p/1/list")
        self.assertLess(body.index('data-id="2"'), body.index('data-id="1"'))
        self.assertEqual(self.fake.last_query["sort_by"], ["position", "id"])


class QuickAddTests(ServerTest):
    ajax = {"X-Requested-With": "XMLHttpRequest"}

    def test_chips_on_list_page(self):
        _, body, _ = self.req("/p/1")
        self.assertIn('<button type="button" class="chip" data-kind="due" data-v="today">Today</button>', body)
        self.assertIn('data-v="weekend">Weekend</button>', body)
        # owner + shared user + team member, first names, no duplicates, sorted
        people = re.findall(r'data-kind="who" data-v="(\d+)">([^<]*)<', body)
        self.assertEqual(people, [("1", "Anna"), ("2", "Ben"), ("3", "cara")])
        self.assertIn('<input type="hidden" id="due" name="due" value="">', body)

    def test_add_with_due_and_assignees(self):
        status, _, _ = self.req("/p/1/add", {"title": "Call plumber", "due": "tomorrow", "who": "1,3"}, self.ajax)
        self.assertEqual(status, 204)
        task = self.fake.tasks[6]
        due = kv.parse_date(task["due_date"])
        tomorrow = kv.dt.datetime.now().astimezone().date() + kv.dt.timedelta(days=1)
        self.assertEqual((due.date(), due.hour, due.minute), (tomorrow, 23, 59))
        self.assertEqual(self.fake.assigned, [(6, 1), (6, 3)])
        _, body, _ = self.req("/p/1/list")
        self.assertIn(">tomorrow</span>", body)          # quick-add time not shown
        self.assertEqual(self.req("/p/1/add", {"title": "x", "who": "1;drop"}, self.ajax)[0], 400)

    def test_quick_due(self):
        at = kv.dt.time(23, 59)
        tz = kv.dt.timezone.utc
        wed = kv.dt.datetime(2026, 10, 7, 9, 0, tzinfo=tz)
        sat = kv.dt.datetime(2026, 10, 10, 9, 0, tzinfo=tz)
        sun = kv.dt.datetime(2026, 10, 11, 9, 0, tzinfo=tz)
        self.assertEqual(kv.quick_due("weekend", at, wed).date(), kv.dt.date(2026, 10, 10))
        self.assertEqual(kv.quick_due("weekend", at, sat).date(), kv.dt.date(2026, 10, 10))
        self.assertEqual(kv.quick_due("weekend", at, sun).date(), kv.dt.date(2026, 10, 11))
        self.assertEqual(kv.quick_due("tomorrow", at, sun).date(), kv.dt.date(2026, 10, 12))
        self.assertIsNone(kv.quick_due("someday", at, wed))


class DashboardTests(ServerTest):
    kitchenowl = True
    ajax = {"X-Requested-With": "XMLHttpRequest"}

    def test_dashboard_tiles(self):
        status, body, _ = self.req("/")
        self.assertEqual(status, 200)
        self.assertIn('<a class="tile" href="/p/1"><div class="th"><span class="tt">Home</span>'
                      '<span class="tc">2 open</span>', body)
        self.assertIn('<a class="tile" href="/s/7"><div class="th"><span class="tt">Groceries</span>'
                      '<span class="tc">3 items</span>', body)
        self.assertIn('Milk <span class="due">2 l</span>', body)
        self.assertIn('"list": "/dash/list"', body)
        status, frag, headers = self.req("/dash/list")
        self.assertTrue(frag.startswith('<a class="tile"'))
        self.assertTrue(headers["X-Today"])

    def test_pages_link_home(self):
        self.assertIn('<a href="/">Home</a>', self.req("/p/1")[1])
        self.assertIn('<a href="/">Home</a>', self.req("/s/7")[1])

    def test_one_service_down_keeps_the_other(self):
        self.ko.down = True
        status, body, _ = self.req("/")
        self.assertEqual(status, 200)
        self.assertIn('href="/p/1"', body)
        self.assertIn('<a class="tile" href="/shop"><div class="th"><span class="tt">KitchenOwl</span>', body)
        self.assertRegex(body, r'<div class="terr">KitchenOwl GET /household[^<]*: 500 boom</div>')

    def test_shopping_list_page(self):
        status, body, _ = self.req("/s/7")
        self.assertEqual(status, 200)
        self.assertIn("<h1>Groceries</h1>", body)
        # grouped by category order, uncategorized last
        fruit, dairy, other = (body.index(f'<h2 class="sec cat">{n}</h2>') for n in ("Fruit", "Dairy", "Other"))
        self.assertLess(fruit, dairy)
        self.assertLess(dairy, other)
        self.assertIn('<span class="note edit" data-empty="+" data-url="/s/7/item/1/desc">2 l</span>', body)
        self.assertIn('data-url="/s/7/item/2/desc">+</span>', body)
        # recently bought: only items not on the list, ticked, can be put back
        recent = body.index("Recently bought")
        self.assertIn('Bread &lt;fresh&gt;', body[recent:])
        self.assertNotIn('data-id="1"', body[recent:])
        self.assertIn('action="/s/7/toggle"', body)

    def test_shop_default_list_and_tick_off(self):
        self.assertEqual(self.req("/shop")[2]["Location"], "/s/7")
        self.assertEqual(self.req("/s/7/toggle", {"id": "2", "done": "1"}, self.ajax)[0], 204)
        self.assertNotIn(2, self.ko.on_list)
        method, path, body = self.ko.requests[-1]
        self.assertEqual((method, path, body["item_id"]), ("DELETE", "/api/shoppinglist/7/item", 2))
        self.assertIn("removed_at", body)

    def test_put_back_keeps_description(self):
        status, _, headers = self.req("/s/7/toggle", {"id": "4", "done": "0", "desc": "1 loaf", "back": "/s/7"})
        self.assertEqual((status, headers["Location"]), (303, "/s/7"))
        self.assertEqual(self.ko.on_list[4], "1 loaf")

    def test_add_and_describe(self):
        self.assertEqual(self.req("/s/7/add", {"title": " oat  milk "}, self.ajax)[0], 204)
        self.assertIn("oat milk", [it["name"] for it in self.ko.catalog.values()])
        self.req("/s/7/item/1/desc", {"title": "3 l"}, self.ajax)
        self.assertEqual(self.ko.on_list[1], "3 l")
        self.req("/s/7/item/1/desc", {"title": ""}, self.ajax)       # clearing is allowed
        self.assertEqual(self.ko.on_list[1], "")
        self.assertEqual(self.req("/s/7/add", {"title": " "}, self.ajax)[0], 400)

    def test_unknown_list(self):
        status, body, _ = self.req("/s/99")
        self.assertEqual(status, 502)
        self.assertIn("not found", body)


class KitchenOwlExtrasTests(ServerTest):
    kitchenowl = True
    ajax = {"X-Requested-With": "XMLHttpRequest"}

    def test_icons_from_own_instance(self):
        _, body, _ = self.req("/s/7")
        self.assertIn('<span class="koi">&#257;</span><span class="title">Milk</span>', body)
        self.assertIn('<span class="koi">&#256;</span><span class="title">Apples</span>', body)
        self.assertIn('<span class="title">Bread &lt;fresh&gt;</span>', body)    # no icon set: none shown
        status, font, headers = self.req("/ko/items.ttf")
        self.assertEqual((status, headers["Content-Type"]), (200, "font/ttf"))
        self.assertIn("max-age", headers["Cache-Control"])
        self.assertIn('<span class="koi">&#257;</span>Milk', self.req("/")[1])  # dashboard tile

    def test_mismatched_icon_map_disables_icons(self):
        self.ko.font = make_ttf([256, 257, 258, 259])   # font from another KitchenOwl version
        _, body, _ = self.req("/s/7")
        self.assertNotIn('class="koi"', body)
        self.assertEqual(self.req("/ko/items.ttf")[0], 404)

    def test_autocomplete_and_pick(self):
        _, body, _ = self.req("/s/7")
        self.assertIn('"suggest": "/s/7/suggest"', body)
        self.assertIn('<div id="sugg"></div>', body)
        status, frag, _ = self.req("/s/7/suggest?q=" + urllib.parse.quote("mil 2l"))
        self.assertEqual(status, 200)
        self.assertIn('<form class="pick" method="post" action="/s/7/pick">', frag)
        self.assertIn('name="desc" value="2l"', frag)
        self.assertIn('<span class="koi">&#257;</span>Milk <span class="meta">2l</span>', frag)
        self.assertEqual(self.req("/s/7/suggest?q=m")[1], "")      # too short: no request
        self.ko.on_list.pop(1)
        self.assertEqual(self.req("/s/7/pick", {"id": "1", "desc": "2l"}, self.ajax)[0], 204)
        self.assertEqual(self.ko.on_list[1], "2l")

    def test_add_with_amount_reuses_item(self):
        self.ko.on_list.pop(1)
        self.req("/s/7/add", {"title": "milk 3l"}, self.ajax)
        self.assertEqual(self.ko.on_list[1], "3l")
        self.assertNotIn("milk 3l", [it["name"] for it in self.ko.catalog.values()])
        self.req("/s/7/add", {"title": "Rice"}, self.ajax)                 # unknown: new item
        self.assertIn("Rice", [it["name"] for it in self.ko.catalog.values()])


class EmojiTests(ServerTest):
    kitchenowl = True

    def test_emoji_become_pictures(self):
        self.ko.list_name = "\U0001F6CD\ufe0f Daily"
        _, body, _ = self.req("/s/7")
        img = '<img class="emo" src="/emoji/1f6cd.png" alt="\U0001F6CD\ufe0f">'
        self.assertIn(f"<h1>{img} Daily</h1>", body)
        self.assertIn("<title>\U0001F6CD\ufe0f Daily</title>", body)   # not inside <title>
        self.assertIn(img, self.req("/")[1])                          # dashboard tile
        status, png, headers = self.req("/emoji/1f6cd.png")
        self.assertEqual((status, headers["Content-Type"]), (200, "image/png"))
        status, _, headers = self.req("/emoji/1f34e.png")              # not available: invisible gif
        self.assertEqual((status, headers["Content-Type"]), (200, "image/gif"))
        self.assertEqual(self.req("/emoji/..%2fetc.png")[0], 404)


class IconUnitTests(unittest.TestCase):
    def test_emojify(self):
        self.assertEqual(kv.emojify("\U0001F966 Obst \u2192 \u2713 \u00a9", "image"),
                         '<img class="emo" src="/emoji/1f966.png" alt="\U0001F966"> Obst \u2192 \u2713 \u00a9')
        self.assertEqual(kv.emojify("<p title='\U0001F966'>\U0001F966 Obst</p>", "strip"), "<p title='\U0001F966'>Obst</p>")
        self.assertEqual(kv.emojify("\U0001F966", "keep"), "\U0001F966")
        self.assertEqual(kv.twemoji_code("\u2764\ufe0f"), "2764")
        self.assertEqual(kv.twemoji_code("\U0001F469\u200d\U0001F467"), "1f469-200d-1f467")

    def test_ttf_codepoints_and_map(self):
        self.assertEqual(kv.ttf_codepoints(make_ttf([65, 256, 300])), {65, 256, 300})
        self.assertEqual(kv.parse_icon_map(ICON_DART), {"apple": 256, "milk": 257, "soap": 258})


ICS_RULES = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:gym
SUMMARY:Gym\, upper body
DTSTART;TZID=Europe/Berlin:20260907T100000
DTEND;TZID=Europe/Berlin:20260907T110000
RRULE:FREQ=WEEKLY;BYDAY=MO,WE
EXDATE;TZID=Europe/Berlin:20261014T100000
BEGIN:VALARM
TRIGGER:-PT15M
SUMMARY:alarm text must be ignored
END:VALARM
END:VEVENT
BEGIN:VEVENT
UID:gym
RECURRENCE-ID;TZID=Europe/Berlin:20261019T100000
SUMMARY:Gym (moved)
DTSTART;TZID=Europe/Berlin:20261020T180000
DTEND;TZID=Europe/Berlin:20261020T190000
END:VEVENT
BEGIN:VEVENT
UID:club
SUMMARY:Book club
DTSTART:20260130T170000Z
DURATION:PT2H
RRULE:FREQ=MONTHLY;BYDAY=-1FR
END:VEVENT
BEGIN:VEVENT
UID:pills
SUMMARY:Pills
DTSTART:20261010T070000Z
RRULE:FREQ=DAILY;COUNT=3
END:VEVENT
BEGIN:VEVENT
UID:fest
SUMMARY:Harvest festival
DTSTART;VALUE=DATE:20261010
DTEND;VALUE=DATE:20261013
END:VEVENT
BEGIN:VEVENT
UID:off
SUMMARY:Cancelled thing
STATUS:CANCELLED
DTSTART:20261012T080000Z
END:VEVENT
END:VCALENDAR
"""


class CalendarUnitTests(unittest.TestCase):
    def occurrences(self, days=30):
        now = kv.dt.datetime(2026, 10, 11, 12, 0, tzinfo=kv.dt.timezone.utc)
        end = now + kv.dt.timedelta(days=days)
        events = kv.parse_events(ICS_RULES)
        moved = {(e["uid"], e["recurrence_id"]) for e in events if e["recurrence_id"]}
        out = []
        for e in events:
            for start, _ in kv.event_occurrences(e, now, end):
                if not e["recurrence_id"] and (e["uid"], start) in moved:
                    continue
                out.append((start, e["summary"]))
        return sorted(out)

    def test_parsing(self):
        events = kv.parse_events(ICS_RULES)
        self.assertEqual(len(events), 5)                      # cancelled one dropped
        self.assertEqual(events[0]["summary"], "Gym, upper body")
        self.assertTrue(events[4]["all_day"])

    def test_recurrence(self):
        berlin = kv.ZoneInfo("Europe/Berlin")
        got = [(s.astimezone(berlin).strftime("%m-%d %H:%M"), name) for s, name in self.occurrences()]
        self.assertIn("Harvest festival", [name for _, name in got])   # all-day since the 10th, still ongoing
        self.assertIn(("10-12 09:00", "Pills"), got)              # COUNT=3: 10th, 11th (past), 12th
        self.assertNotIn(("10-13 09:00", "Pills"), got)
        self.assertIn(("10-12 10:00", "Gym, upper body"), got)
        self.assertNotIn(("10-14 10:00", "Gym, upper body"), got)  # EXDATE
        self.assertNotIn(("10-19 10:00", "Gym, upper body"), got)  # moved ...
        self.assertIn(("10-20 18:00", "Gym (moved)"), got)         # ... to here
        self.assertIn(("10-26 10:00", "Gym, upper body"), got)     # after the DST change: still 10:00
        self.assertIn(("10-30 18:00", "Book club"), got)           # last Friday of October (17:00Z = 18:00 CET)

    def test_calendar_url(self):
        dav = "https://cloud.example.com/remote.php/dav/public-calendars/Aay9rgqp/"
        self.assertEqual(kv.calendar_url("https://cloud.example.com/apps/calendar/p/Aay9rgqp"), dav)
        self.assertEqual(kv.calendar_url("https://cloud.example.com/index.php/apps/calendar/p/Aay9rgqp/dayGridMonth/now"), dav)
        self.assertEqual(kv.calendar_url("https://example.com/nc/apps/calendar/p/Tok3n"),
                         "https://example.com/nc/remote.php/dav/public-calendars/Tok3n/")
        self.assertEqual(kv.calendar_url("webcal://example.com/cal.ics"), "https://example.com/cal.ics")
        self.assertEqual(kv.calendar_url(dav + "?export"), dav + "?export")
        self.assertEqual(kv.Config({"CALDAV_URL": "https://c.example/apps/calendar/p/X1 https://a.example/b.ics"}).caldav_urls,
                         ["https://c.example/remote.php/dav/public-calendars/X1/", "https://a.example/b.ics"])

    def test_nth_weekday(self):
        self.assertEqual(kv._nth_weekday(2026, 10, 4, -1), 30)
        self.assertEqual(kv._nth_weekday(2026, 10, 0, 1), 5)
        self.assertIsNone(kv._nth_weekday(2026, 2, 0, 5))


class FakeCalDav:
    """REPORT -> multistatus with already expanded events; /feed.ics only answers GET."""

    def __init__(self):
        now = kv.dt.datetime.now(kv.dt.timezone.utc).replace(microsecond=0)
        z = lambda d: d.strftime("%Y%m%dT%H%M%SZ")
        self.expanded = "".join(
            f"BEGIN:VEVENT\r\nUID:e{i}\r\nSUMMARY:{name}\r\nDTSTART:{z(now + delta)}\r\n"
            f"DTEND:{z(now + delta + kv.dt.timedelta(hours=1))}\r\nEND:VEVENT\r\n"
            for i, (name, delta) in enumerate([("Dentist", kv.dt.timedelta(days=2)),
                                                ("Parents' evening", kv.dt.timedelta(hours=3)),
                                                ("Too far away", kv.dt.timedelta(days=90))]))
        tomorrow = (kv.dt.datetime.now() + kv.dt.timedelta(days=1)).strftime("%Y%m%d")
        self.feed = (f"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:b\r\nSUMMARY:Birthday Cara\r\n"
                     f"DTSTART;VALUE=DATE:{tomorrow}\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nUID:x\r\n"
                     f"SUMMARY:Later\r\nDTSTART:{z(now + kv.dt.timedelta(days=5))}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
        self.password = "app-pass"
        self.auth_seen = []   # (path, Authorization header or None)

    def handler(fake):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def send(self, code, body, ctype):
                data = body.encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def authorized(self):
                fake.auth_seen.append((self.path, self.headers.get("Authorization")))
                if self.path == "/public.ics":
                    return True
                expected = "Basic " + kv.base64.b64encode(f"me:{fake.password}".encode()).decode()
                if self.headers.get("Authorization") != expected:
                    self.send(401, "no", "text/plain")
                    return False
                return True

            def do_REPORT(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if not self.authorized():
                    return
                if self.path != "/cal/":
                    return self.send(405, "not a calendar", "text/plain")
                ics = "BEGIN:VCALENDAR\r\n" + fake.expanded + "END:VCALENDAR\r\n"
                xml = ('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">'
                       f'<d:response><d:href>/cal/1.ics</d:href><d:propstat><d:prop><cal:calendar-data>{html.escape(ics)}'
                       '</cal:calendar-data></d:prop></d:propstat></d:response></d:multistatus>')
                self.send(207, xml, "application/xml")

            def do_GET(self):
                if self.authorized():
                    self.send(200, fake.feed, "text/calendar")

        return H


class CalendarTests(ServerTest):
    vikunja = False
    kitchenowl = True

    def setUp(self):
        self.cal = FakeCalDav()
        server = serve(ThreadingHTTPServer(("127.0.0.1", 0), self.cal.handler()))
        base = f"http://127.0.0.1:{server.server_port}"
        self.extra_env = {"CALDAV_URL": f"{base}/cal/ {base}/feed.ics", "CALDAV_USER": "me",
                          "CALDAV_PASSWORD": "app-pass"}
        super().setUp()
        self.servers.insert(0, server)

    def test_upcoming_events_tile(self):
        status, body, _ = self.req("/")
        self.assertEqual(status, 200)
        self.assertLess(body.index("Groceries"), body.index('<div class="tile cal">'))   # below the lists
        tile = body[body.index('<div class="tile cal">'):]
        names = re.findall(r'<div class="what">([^<]+)</div>', tile)
        self.assertEqual(names, ["Parents&#x27; evening", "Birthday Cara", "Dentist"])   # CALDAV_EVENTS=3
        self.assertIn('<div class="ev"><div class="when">tomorrow</div><div class="what">Birthday Cara</div></div>',
                      tile)                                                  # all-day: no time

    def test_credentials_only_when_asked(self):
        self.req("/")
        sent = {path: auth for path, auth in self.cal.auth_seen if auth}
        self.assertIn("/cal/", sent)                     # asked (401) -> logged in
        public = [auth for path, auth in self.cal.auth_seen if path == "/public.ics"]
        self.assertEqual(public, [])                     # not configured in this test

    def test_wrong_password(self):
        self.cal.password = "changed"
        _, body, _ = self.req("/")
        self.assertIn('<div class="terr">Calendar', body)
        self.assertIn("CALDAV_USER/CALDAV_PASSWORD", body)


class CalendarAuthTests(unittest.TestCase):
    def test_public_feed_never_gets_password_and_url_credentials(self):
        cal = FakeCalDav()
        server = serve(ThreadingHTTPServer(("127.0.0.1", 0), cal.handler()))
        try:
            base = f"127.0.0.1:{server.server_port}"
            cfg = kv.Config({"CALDAV_URL": f"http://{base}/public.ics http://me:app-pass@{base}/cal/",
                             "CALDAV_USER": "global", "CALDAV_PASSWORD": "secret"})
            events = kv.Calendar(cfg).upcoming()
            self.assertTrue(events)
            self.assertTrue(all(auth is None for path, auth in cal.auth_seen if path == "/public.ics"))
            self.assertIn(("/cal/", "Basic " + kv.base64.b64encode(b"me:app-pass").decode()), cal.auth_seen)
            cfg = kv.Config({"CALDAV_URL": f"http://me:wrong@{base}/cal/?x=1"})
            with self.assertRaises(kv.ApiError) as ctx:
                kv.Calendar(cfg).upcoming()
            self.assertNotIn("wrong", str(ctx.exception))   # password never in error messages
        finally:
            server.shutdown()
            server.server_close()


class KitchenOwlOnlyTests(ServerTest):
    vikunja = False
    kitchenowl = True

    def test_dashboard_without_vikunja(self):
        status, body, _ = self.req("/")
        self.assertEqual(status, 200)
        self.assertIn('href="/s/7"', body)
        self.assertNotIn('href="/p/', body)
        self.assertEqual(self.req("/p/1")[0], 404)


class AccessKeyTests(ServerTest):
    access_key = "s3cret"

    def test_requires_key_then_uses_cookie(self):
        self.assertEqual(self.req("/p/1")[0], 403)
        self.assertEqual(self.req("/healthz")[0], 200)
        status, _, headers = self.req("/p/1?key=s3cret")
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"].split(";")[0]
        self.assertEqual(self.req("/p/1/list", headers={"Cookie": cookie})[0], 200)
        self.assertEqual(self.req("/p/1/list", headers={"Cookie": "kv_key=wrong"})[0], 403)


class UnitTests(unittest.TestCase):
    def test_config_appends_api_path(self):
        self.assertEqual(kv.Config({"VIKUNJA_URL": "https://v.example/"}).api_url, "https://v.example/api/v1")
        self.assertEqual(kv.Config({"VIKUNJA_URL": "https://v.example/api/v1"}).api_url, "https://v.example/api/v1")

    def test_plain_text_description(self):
        self.assertEqual(kv.description_html("a <3\nb"), "a &lt;3<br>b")
        self.assertEqual(kv.description_html("<p></p>"), "")
        self.assertEqual(kv.description_html("<b>x"), "<strong>x</strong>")

    def test_assignee_names(self):
        self.assertEqual(kv.assignee_names({"assignees": None}), [])
        self.assertEqual(kv.assignee_names({"assignees": [{"username": "x", "name": " "}, {}]}), ["x"])

    def test_checklist_helpers(self):
        html = ('<ul data-type="taskList"><li data-checked="false" data-type="taskItem"><label>'
                '<input type="checkbox"><span></span></label><div><p>a</p></div></li>'
                '<li data-checked="true" data-type="taskItem"><label><input type="checkbox" checked="checked">'
                '</label><div><p>b</p></div></li></ul>')
        self.assertEqual(kv.checklist_progress(html), (1, 2))
        ticked = kv.toggle_checklist(html, 0, True)
        self.assertEqual(kv.checklist_progress(ticked), (2, 2))
        self.assertEqual(ticked.count('checked="checked"'), 2)
        unticked = kv.toggle_checklist(html, 1, False)
        self.assertEqual(kv.checklist_progress(unticked), (0, 2))
        self.assertNotIn("checked=", unticked.replace("data-checked=", ""))
        self.assertIsNone(kv.toggle_checklist(html, 2, True))
        self.assertEqual(kv.checklist_progress("1. [X] a\r\n  - [ ] b\nno [ ] item"), (1, 2))
        self.assertEqual(kv.toggle_checklist("1. [X] a\r\n  - [ ] b", 1, True), "1. [X] a\r\n  - [x] b")

    def test_checklist_editing_helpers(self):
        plain = "Todo:\r\n- [x] a\r\n- [ ] b"
        self.assertEqual(kv.set_checklist_text(plain, 1, "c"), "Todo:\r\n- [x] a\r\n- [ ] c")
        self.assertEqual(kv.append_checklist_item(plain, "d"), plain + "\n- [ ] d")
        self.assertIsNone(kv.set_checklist_text(plain, 2, "x"))
        nested = ('<ul data-type="taskList"><li data-checked="false"><div><p>a</p>'
                  '<ul data-type="taskList"><li data-checked="false"><div><p>a1</p></div></li></ul>'
                  '</div></li></ul><p>notes</p>')
        added = kv.append_checklist_item(nested, "b")
        self.assertTrue(added.endswith('<p>b</p></div></li></ul><p>notes</p>'))   # top level, not nested
        self.assertIn("<p>a1 x</p>", kv.set_checklist_text(nested, 1, "a1 x"))
        self.assertEqual(kv.append_checklist_item("<p>hi</p>", "x").count('data-type="taskList"'), 1)

    def test_null_date(self):
        self.assertIsNone(kv.parse_date("0001-01-01T00:00:00Z"))
        self.assertIsNotNone(kv.parse_date("2026-10-10T12:00:00+02:00"))

    def test_sort_due_then_priority(self):
        tasks = [{"id": 1, "due_date": None, "priority": 5},
                 {"id": 2, "due_date": "2026-01-02T00:00:00Z"},
                 {"id": 3, "due_date": "2026-01-01T00:00:00Z"}]
        roots, _ = kv.build_tree(tasks, "due")
        self.assertEqual([t["id"] for t in roots], [3, 2, 1])
        roots, _ = kv.build_tree(tasks, "priority")
        self.assertEqual(roots[0]["id"], 1)


if __name__ == "__main__":
    unittest.main()
