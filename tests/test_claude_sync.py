"""Tests for bin/claude-sync: rendering, storage, import and the localhost listener.

Run with:  python3 -m unittest discover -s tests
"""

import argparse
import contextlib
import http.client
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
import zipfile

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "claude-sync")
ORIGIN = "chrome-extension://digbgfehhflmjfbgoehmjfpokokfobjk"
ROOT = "00000000-0000-4000-8000-000000000000"
CID = "11111111-2222-4333-8444-555555555555"
ORG = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def load(config: dict, tmp: str):
    """Import the script as a fresh module reading CONFIG from a temp file."""
    path = os.path.join(tmp, "config.json")
    with open(path, "w") as f:
        json.dump(config, f)
    os.environ["CLAUDE_SYNC_CONFIG"] = path
    try:
        name = f"claude_sync_{abs(hash(path))}"
        loader = importlib.machinery.SourceFileLoader(name, SCRIPT)
        module = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
        loader.exec_module(module)
        return module
    finally:
        del os.environ["CLAUDE_SYNC_CONFIG"]


def git_identity(path):
    for args in (["config", "user.email", "t@example.com"], ["config", "user.name", "Test"]):
        subprocess.run(["git", "-C", path, *args], check=True)


def msg(uuid, parent, sender, content, created, **extra):
    m = {"uuid": uuid, "parent_message_uuid": parent, "sender": sender, "text": "",
         "content": content, "created_at": created, "updated_at": created,
         "attachments": [], "files": [], "index": 0, "truncated": False}
    m.update(extra)
    return m


def text(t):
    return {"type": "text", "text": t, "citations": []}


def conversation(cid=CID, name="Test chat", updated="2026-10-03T15:00:00.123456Z"):
    """A conversation as the app's endpoint returns it, with a retried reply."""
    long_result = "line\n" * 1000
    return {
        "uuid": cid, "name": name, "summary": "", "model": "claude-test-1",
        "created_at": "2026-10-03T14:00:00.000000Z", "updated_at": updated,
        "settings": {"enabled_web_search": True}, "is_starred": False, "is_archived": False,
        "is_temporary": False, "current_leaf_message_uuid": "m4",
        "chat_messages": [
            msg("m1", ROOT, "human", [text("# not a heading\nWhat is *x*?")], "2026-10-03T14:00:01Z"),
            msg("m2", "m1", "assistant", [
                {"type": "thinking", "thinking": "Let me look it up.", "summaries": []},
                {"type": "tool_use", "id": "t1", "name": "web_search", "input": {"query": "x"}},
                {"type": "tool_result", "tool_use_id": "t1", "name": "web_search", "is_error": False,
                 "content": [text(long_result), {"type": "knowledge", "title": "Ex", "url": "https://e.x"}]},
                text("# Answer\n\nIt is y.\n\n```\n# code\n```"),
            ], "2026-10-03T14:00:10Z"),
            msg("m2b", "m1", "assistant", [text("abandoned branch")], "2026-10-03T14:00:09Z"),
            msg("m3", "m2", "human", [text("Thanks")], "2026-10-03T14:01:00Z",
                attachments=[{"file_name": "notes.txt", "extracted_content": "attached text"}],
                files=[{"file_name": "chart.png", "file_kind": "blob"}]),
            msg("m4", "m3", "assistant", [text("You're welcome.")], "2026-10-03T14:01:05Z"),
        ],
    }


def export_conversation(cid="99999999-2222-4333-8444-555555555555"):
    """A conversation as the official export writes it: no tree, text duplicated."""
    return {
        "uuid": cid, "name": "Exported chat", "summary": "",
        "created_at": "2025-01-02T10:00:00.000000Z", "updated_at": "2025-01-02T10:05:00.000000Z",
        "account": {"uuid": "00000000-1111-4222-8333-444444444444"},
        "chat_messages": [
            {"uuid": "e2", "sender": "assistant", "text": "Hello back.", "content": [text("Hello back.")],
             "created_at": "2025-01-02T10:00:05Z", "updated_at": "2025-01-02T10:00:05Z",
             "attachments": [], "files": []},
            {"uuid": "e1", "sender": "human", "text": "Hello", "content": [text("Hello")],
             "created_at": "2025-01-02T10:00:00Z", "updated_at": "2025-01-02T10:00:00Z",
             "attachments": [], "files": []},
        ],
    }


def entry_for(conv):
    return {"name": conv["name"], "created_at": conv["created_at"], "updated_at": conv["updated_at"],
            "model": conv.get("model"), "org": ORG, "org_name": "Test org"}


class RenderMarkdownTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.cs = load({"data_dir": os.path.join(self.tmp, "data")}, self.tmp)

    def render(self, conv=None):
        conv = conv or conversation()
        return self.cs.render_markdown(conv, entry_for(conv))

    def test_default_format_is_markdown_under_data(self):
        self.assertEqual(self.cs.FORMAT, "markdown")
        self.assertEqual(self.cs.OUTPUT, os.path.join(self.tmp, "data", "markdown"))

    def test_front_matter_and_title(self):
        md = self.render()
        self.assertTrue(md.startswith('---\ntitle: "Test chat"\n'))
        self.assertIn(f'url: "https://claude.ai/chat/{CID}"', md)
        self.assertIn('model: "claude-test-1"', md)
        self.assertIn('organization: "Test org"', md)
        self.assertIn("\n# Test chat\n", md)

    def test_renders_the_current_branch_only(self):
        md = self.render()
        self.assertNotIn("abandoned branch", md)
        self.assertLess(md.index("It is y."), md.index("Thanks"))
        self.assertIn("You're welcome.", md)

    def test_branch_follows_current_leaf(self):
        conv = conversation()
        conv["current_leaf_message_uuid"] = "m2b"   # the user picked the retry
        md = self.render(conv)
        self.assertIn("abandoned branch", md)
        self.assertNotIn("It is y.", md)

    def test_turns_nest_under_turn_headings(self):
        md = self.render()
        self.assertIn("## User · ", md)
        self.assertIn("## Claude · ", md)
        self.assertIn("\n### Answer\n", md)       # assistant heading demoted
        self.assertIn("```\n# code\n```", md)      # but not inside code fences

    def test_user_text_is_literal(self):
        self.assertIn("\\# not a heading", self.render())

    def test_thinking_and_tools_are_compact(self):
        md = self.render()
        self.assertIn("> **Thinking**\n>\n> Let me look it up.", md)
        self.assertIn('**Tool call:** `web_search`\n\n```json\n{"query": "x"}\n```', md)
        self.assertIn("**Tool result:** `web_search`", md)
        self.assertIn("more characters in the raw JSON", md)   # shortened
        self.assertLess(len(md), 4000)

    def test_attachments_and_files_are_listed(self):
        md = self.render()
        self.assertIn("**Attachment:** notes.txt\n\n```\nattached text\n```", md)
        self.assertIn("**File:** chart.png", md)

    def test_export_without_tree_renders_in_time_order(self):
        conv = export_conversation()
        md = self.render(conv)
        self.assertLess(md.index("Hello\n"), md.index("Hello back."))


@unittest.skipUnless(shutil.which("pandoc"), "pandoc not installed")
class RenderOrgTests(unittest.TestCase):
    def test_org_output(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        cs = load({"format": "org", "data_dir": os.path.join(tmp, "data")}, tmp)
        conv = conversation()
        org = cs.render_org(conv, dict(entry_for(conv), org_mode_id="ABC-123"))
        self.assertTrue(org.startswith(":PROPERTIES:\n:ID:       ABC-123\n"))
        self.assertIn(f":CLAUDE_URL: https://claude.ai/chat/{CID}", org)
        self.assertIn("#+title: Test chat", org)
        self.assertIn("#+begin_example\n# not a heading", org)
        self.assertIn("\n** Answer\n", org)
        self.assertIn("* Claude [", org)
        self.assertNotIn("abandoned branch", org)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.cs = load({"data_dir": os.path.join(self.tmp, "data")}, self.tmp)
        self.cs.ensure_data_repo()

    def stored(self, conv, meta=None, index=None, **kw):
        conv["_claude_sync"] = {"org": ORG, "source": "api"}
        index = {} if index is None else index
        return self.cs.store(conv, dict(meta or {}, org=ORG), index, **kw), index

    def test_index_entry_has_the_documented_fields(self):
        _, index = self.stored(conversation(), {"project_uuid": "p1", "project": "Proj",
                                                "org_name": "Test org"})
        entry = index[CID]
        for key in ("org", "name", "created_at", "updated_at", "model", "project_uuid", "file", "fetched_at"):
            self.assertIn(key, entry)
        self.assertEqual((entry["org"], entry["project_uuid"], entry["model"]), (ORG, "p1", "claude-test-1"))
        self.assertEqual(entry["updated_at"], "2026-10-03T15:00:00.123456Z")

    def test_raw_json_is_the_response_plus_one_key(self):
        conv = conversation()
        self.stored(conversation())
        with open(os.path.join(self.cs.RAW, f"{CID}.json")) as f:
            raw = json.load(f)
        self.assertEqual(raw.pop("_claude_sync"), {"org": ORG, "source": "api"})
        self.assertEqual(raw, conv)

    def test_store_skips_unchanged_and_renames_files(self):
        _, index = self.stored(conversation())
        first = index[CID]["file"]
        self.assertTrue(first.endswith("--11111111.md"))
        self.assertFalse(self.stored(conversation(), index=index)[0])  # not newer
        self.assertTrue(self.stored(conversation(name="Renamed", updated="2026-10-03T16:00:00Z"),
                                    index=index)[0])
        second = index[CID]["file"]
        self.assertIn("renamed", second)
        self.assertFalse(os.path.exists(os.path.join(self.cs.OUTPUT, first)))
        self.assertTrue(os.path.exists(os.path.join(self.cs.OUTPUT, second)))

    def test_list_changes_survive_a_rerender(self):
        conv = conversation()
        conv["is_starred"] = True                        # as fetched
        _, index = self.stored(conv)
        changed = self.cs.update_listing([{"uuid": CID, "is_starred": False, "name": "New name"}], index)
        self.assertEqual(changed, [CID])
        self.assertIn("new-name", index[CID]["file"])
        with open(os.path.join(self.cs.RAW, f"{CID}.json")) as f:
            self.cs.store(json.load(f), None, index, force=True)   # what `render` does
        self.assertFalse(index[CID]["is_starred"])
        self.assertEqual(index[CID]["name"], "New name")
        self.assertEqual(index[CID]["org"], ORG)

    def test_app_copy_replaces_an_imported_one(self):
        exported = export_conversation(cid=CID)
        exported["updated_at"] = conversation()["updated_at"]
        exported["_claude_sync"] = {"org": None, "source": "export"}
        index = {}
        self.cs.store(exported, None, index)
        self.assertEqual(index[CID]["source"], "export")
        written, index = self.stored(conversation(), index=index)   # same updated_at
        self.assertTrue(written)
        self.assertEqual((index[CID]["source"], index[CID]["org"]), ("api", ORG))

    def test_data_repo_ignores_rendered_output(self):
        # No trailing slash, so a symlinked output folder is ignored too.
        with open(os.path.join(self.cs.DATA, ".gitignore")) as f:
            self.assertEqual(f.read(), "/markdown\n/org\n")


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.cs = load({"data_dir": os.path.join(self.tmp, "data")}, self.tmp)
        self.cs.ensure_data_repo()
        git_identity(self.cs.DATA)

    def run_import(self, *paths, org=None):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.cs.cmd_import(argparse.Namespace(paths=list(paths), org=org))
        return out.getvalue()

    def test_import_zip_commits_and_keeps_app_copies(self):
        exported = export_conversation()
        stale_app_chat = conversation()
        zpath = os.path.join(self.tmp, "conversations-1.zip")
        with zipfile.ZipFile(zpath, "w") as z:
            z.writestr("conversations.json", json.dumps([exported, stale_app_chat]))
        # The app's copy of CID is already stored; the export must not replace it.
        index = {}
        app_copy = conversation(name="App copy")
        app_copy["_claude_sync"] = {"org": ORG, "source": "api"}
        self.cs.store(app_copy, {"org": ORG}, index)
        self.cs.save_index(index)

        out = self.run_import(zpath, org=ORG)
        self.assertIn("2 in export, 1 new or newer", out)
        index = self.cs.load_index()
        entry = index[exported["uuid"]]
        self.assertEqual((entry["source"], entry["org"], entry["name"]), ("export", ORG, "Exported chat"))
        self.assertEqual(index[CID]["name"], "App copy")
        with open(os.path.join(self.cs.OUTPUT, entry["file"])) as f:
            self.assertIn("Hello back.", f.read())
        log = subprocess.run(["git", "-C", self.cs.DATA, "log", "--format=%s"],
                             capture_output=True, text=True, check=True).stdout
        self.assertIn("import: 1 of 2 conversations from official export", log)

    def test_import_folder_is_idempotent(self):
        folder = os.path.join(self.tmp, "export")
        os.makedirs(folder)
        with open(os.path.join(folder, "conversations.json"), "w") as f:
            json.dump([export_conversation()], f)
        self.assertIn("1 new or newer", self.run_import(folder))
        self.assertIn("0 new or newer", self.run_import(folder))


class ListenerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.cs = load({"data_dir": os.path.join(self.tmp, "data"),
                        "state_dir": os.path.join(self.tmp, "state")}, self.tmp)
        self.server = self.cs.make_server(0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        git_identity(self.cs.DATA)

    def request(self, method, path, body=None, origin=ORIGIN):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        headers = {"Origin": origin} if origin else {}
        if body is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(body)
        conn.request(method, path, body=body, headers=headers)
        r = conn.getresponse()
        return r.status, json.loads(r.read() or b"{}")

    def git_log(self):
        return subprocess.run(["git", "-C", self.cs.DATA, "log", "--format=%s"],
                              capture_output=True, text=True, check=True).stdout.splitlines()

    def test_origin_rules(self):
        self.assertEqual(self.request("GET", "/state", origin="https://evil.example")[0], 403)
        self.assertEqual(self.request("GET", "/state", origin=None)[0], 200)
        self.assertEqual(self.request("POST", "/sync-done", {}, origin=None)[0], 403)
        self.assertEqual(self.request("POST", "/conversations", {"items": []},
                                      origin="chrome-extension://someotherextensionid")[0], 403)

    def test_sync_round_trip_commits(self):
        conv = conversation()
        item = {"org": ORG, "meta": {"uuid": CID, "org_name": "Test org", "is_starred": True,
                                     "updated_at": conv["updated_at"]},
                "conversation": conv}
        self.assertEqual(self.request("POST", "/conversations", {"items": [item]}), (200, {"written": 1}))
        status, state = self.request("GET", "/state")
        self.assertEqual(state["ids"], {CID: self.cs.to_epoch(conv["updated_at"])})
        self.assertIsNone(state["orgs"])
        cowork = {"orgs": [ORG], "sessions": [{"id": "cse_x", "org": ORG, "title": "T", "folders": ["/f"]}]}
        status, done = self.request("POST", "/sync-done", {
            "listing": [{"uuid": CID, "org": ORG, "is_starred": True}],
            "summary": {"listed": 1, "fetched": 1}, "cowork": cowork})
        self.assertEqual(status, 200)
        self.assertTrue(done["commit"])
        with open(self.cs.LAST_SYNC) as f:
            self.assertEqual(json.load(f)["summary"], {"listed": 1, "fetched": 1})
        with open(self.cs.COWORK) as f:
            saved = json.load(f)
        self.assertEqual(saved["sessions"], cowork["sessions"])
        self.assertIn("fetched_at", saved)
        tracked = subprocess.run(["git", "-C", self.cs.DATA, "ls-files"],
                                 capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual(sorted(tracked), ["cowork/sessions.json", "index.json", f"raw/{CID}.json"])
        self.assertEqual(self.git_log(), ["sync: 1 fetched, 0 list changes, 1 Cowork sessions"])
        # An unchanged Cowork list and no new chats make no commit.
        status, done = self.request("POST", "/sync-done", {"listing": [], "summary": {}, "cowork": cowork})
        self.assertIsNone(done["commit"])
        self.assertEqual(len(self.git_log()), 1)

    def test_state_asks_for_imported_chats_again(self):
        exported = export_conversation()
        exported["_claude_sync"] = {"org": None, "source": "export"}
        index = {}
        self.cs.store(exported, None, index)
        self.cs.save_index(index)
        self.assertEqual(self.request("GET", "/state")[1]["ids"], {exported["uuid"]: 0})

    def test_state_reports_extension_code_hash(self):
        status, state = self.request("GET", "/state?v=1.0&code=stale")
        self.assertEqual(status, 200)
        self.assertEqual(state["extension_code"], self.cs.extension_code_hash())
        self.assertRegex(state["extension_code"], r"^[0-9a-f]{64}$")

    def test_extension_code_files_match_extension(self):
        # Both sides must hash the same files, or the extension would reload
        # itself in a loop.
        ext = os.path.join(os.path.dirname(SCRIPT), "..", "extension")
        on_disk = sorted(n for n in os.listdir(ext) if os.path.isfile(os.path.join(ext, n)))
        self.assertEqual(sorted(self.cs.EXTENSION_CODE_FILES), on_disk)
        with open(os.path.join(ext, "background.js")) as f:
            js = f.read()
        self.assertIn("const CODE_FILES = " + json.dumps(self.cs.EXTENSION_CODE_FILES) + ";", js)

    def test_listener_and_extension_agree_on_port_and_origin(self):
        ext = os.path.join(os.path.dirname(SCRIPT), "..", "extension")
        with open(os.path.join(ext, "background.js")) as f:
            self.assertIn(f"const SERVER = 'http://127.0.0.1:{self.cs.PORT}';", f.read())
        with open(os.path.join(ext, "manifest.json")) as f:
            manifest = json.load(f)
        self.assertIn(f"http://127.0.0.1:{self.cs.PORT}/*", manifest["host_permissions"])
        # Chrome derives the extension ID from the manifest key.
        import base64
        import hashlib
        digest = hashlib.sha256(base64.b64decode(manifest["key"])).hexdigest()[:32]
        ext_id = "".join(chr(ord("a") + int(c, 16)) for c in digest)
        self.assertEqual(self.cs.EXTENSION_ORIGIN, f"chrome-extension://{ext_id}")

    def test_pause_file_refuses_requests(self):
        open(self.cs.PAUSED, "w").close()
        self.assertEqual(self.request("GET", "/state")[0], 503)
        self.assertEqual(self.request("POST", "/conversations", {"items": []})[0], 503)


if __name__ == "__main__":
    unittest.main()
