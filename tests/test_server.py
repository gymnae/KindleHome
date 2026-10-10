"""End-to-end tests against a fake Vikunja API.  Run: python3 -m unittest discover tests"""

import json
import os
import re
import sys
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import kindle_vikunja as kv  # noqa: E402


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
                    return self.reply(200, {"id": 1, "title": "Home"})
                if p == "/api/v1/projects/1/views":
                    return self.reply(200, [{"id": 9, "view_kind": "kanban"}, {"id": 10, "view_kind": "list"}])
                if p == "/api/v1/projects/1/views/10/tasks":
                    assert q["filter"] == ["done = false"], q
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
                if self.path == "/api/v1/projects/1/tasks":
                    data = self.body()
                    t = {"id": fake.next_id, "title": data["title"], "done": False, "project_id": 1}
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


def serve(server):
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class ServerTest(unittest.TestCase):
    access_key = ""

    extra_env = {}

    def setUp(self):
        self.fake = FakeVikunja()
        self.vik = serve(ThreadingHTTPServer(("127.0.0.1", 0), self.fake.handler()))
        cfg = kv.Config({
            "VIKUNJA_URL": f"http://127.0.0.1:{self.vik.server_port}",
            "VIKUNJA_TOKEN": "secret", "VIKUNJA_PROJECT_ID": "1",
            "LISTEN_HOST": "127.0.0.1", "LISTEN_PORT": "0", "ACCESS_KEY": self.access_key,
            **self.extra_env,
        })
        self.app = serve(kv.make_server(cfg))
        self.base = f"http://127.0.0.1:{self.app.server_port}"
        self.opener = urllib.request.build_opener(NoRedirect)

    def tearDown(self):
        for s in (self.app, self.vik):
            s.shutdown()
            s.server_close()

    def req(self, path, data=None, headers=None):
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        r = urllib.request.Request(self.base + path, data=body, headers=headers or {})
        try:
            resp = self.opener.open(r)
            return resp.status, resp.read().decode(), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(), e.headers


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
        self.assertIn('<option value="3">– Wipe counters</option>', body)

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
        self.assertIn('<span class="title">Clean &lt;kitchen&gt;</span>', body)
        # description is sanitized
        self.assertIn("<p>Use <strong>soap</strong></p>", body)
        self.assertNotIn("alert(1)", body)
        self.assertNotIn("onerror", body)
        self.assertNotIn("javascript:", body)
        self.assertIn('<li class="ck on"><form class="ckf" method="post" action="/check">'
                      '<input type="hidden" name="id" value="2"><input type="hidden" name="i" value="0">'
                      '<input type="hidden" name="done" value="0">', body)
        # subtasks are tickable rows that link further down
        self.assertIn('name="id" value="3"', body)
        self.assertIn('class="task done" data-id="4"', body)
        self.assertIn('<a class="open" href="/t/4?p=1">', body)
        # add-subtask form
        self.assertIn('name="parent" value="2"', body)
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
        self.assertIn('<span class="ckt">bread</span>', body)
        status, _, headers = self.req("/check", {"id": "1", "i": "2", "done": "1", "back": "/t/1?p=1"})
        self.assertEqual((status, headers["Location"]), (303, "/t/1?p=1"))
        self.assertEqual(self.fake.tasks[1]["description"], "Shopping:\n- [ ] eggs\n- [x] milk\n* [x] bread")
        _, body, _ = self.req("/p/1/list")
        self.assertIn('<span class="cnt">2/3</span>', body)

    def test_missing_item_is_an_error(self):
        status, body, _ = self.req("/check", {"id": "2", "i": "5", "done": "1"}, self.ajax)
        self.assertEqual(status, 502)
        self.assertIn("reload", body)


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
