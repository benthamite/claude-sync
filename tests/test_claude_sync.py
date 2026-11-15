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
ORIGIN = "chrome-extension://mfoglmpaedcenmkbdclbjljncebkflal"
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
                attachments=[{"file_name": "notes.txt", "extracted_content": "attached text"},
                             {"file_name": "", "file_type": "txt", "extracted_content": "pasted"}],
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


DOC = "dddddddd-1111-4222-8333-444444444444"
TAB1, TAB2, TAB3, NODE = "aaaa1111-0001", "bbbb2222-0002", "cccc3333-0003", "eeee5555-0005"


def docs_call(tid, verb, args):
    return {"type": "tool_use", "id": tid, "name": f"mcp__Claude_Docs__{verb}", "input": args,
            "integration_name": "Claude Docs", "mcp_server_url": "https://api.anthropic.com/v1/pages/mcp"}


def docs_result(tid, verb, payload, trailer=""):
    return {"type": "tool_result", "tool_use_id": tid, "name": f"mcp__Claude_Docs__{verb}",
            "is_error": False, "content": [text(json.dumps(payload) + trailer)]}


def doc_conversation():
    """A chat that creates a Claude Doc, edits it, adds a tab, exports and reads it."""
    conv = conversation()
    here = {"id": DOC, "kind": "project"}
    conv["chat_messages"].append(msg("m5", "m4", "assistant", [
        # The guide's examples name tabs that belong to no doc.
        docs_call("g1", "guide", {"items": ["topic.instructions"]}),
        docs_result("g1", "guide", {"example": True}, '\nits ack: "lids":{"f":"ffff6666-0006"}'),
        docs_call("b1", "batch", {"container": {"kind": "project", "create": {"name": "Paper"}},
                                  "batch": []}),
        docs_result("b1", "batch", {"verdict": "allow", "lids": {"f": TAB1, "n": NODE},
                                    "created": {"verdict": "allow", "minted": DOC, "bound": True},
                                    "frame": {"slug": DOC}},
                    "\n\nThe doc's viewer is at https://claude.ai/artifact/Short"),
        docs_call("u1", "update", {"container": here, "engine": "prose",
                                   "ref": {"object": "node", "id": NODE}, "payload": {"ops": []}}),
        docs_result("u1", "update", {"verdict": "allow", "rev": 2}),
        docs_call("b2", "batch", {"container": here, "batch": [
            {"$lid": "f2", "object": "file", "verb": "create", "payload": {}},
            {"$lid": "n2", "object": "node", "verb": "create", "payload": {}}]}),
        docs_result("b2", "batch", {"verdict": "allow", "lids": {"f2": TAB2, "n2": "eeee5555-0007"}}),
        docs_call("e1", "export", {"container": here, "file": TAB1, "format": "markdown"}),
        docs_result("e1", "export", {"verdict": "allow", "rev": 2, "data": {"name": "Paper.md"}}),
        docs_call("r1", "read", {"container": here, "ref": {"object": "project", "id": DOC}}),
        docs_result("r1", "read", {"verdict": "allow", "value": {"tabs": {TAB1: {}, TAB3: {}}}}),
        text("The doc is ready."),
    ], "2026-10-03T14:02:00Z"))
    conv["current_leaf_message_uuid"] = "m5"
    return conv


def b64(s):
    import base64
    return base64.b64encode(s.encode()).decode()


def doc_record(rev=3, title="Paper", body="# Paper\n\n## Intro\n\nVersion two.\n", tabs=2):
    """A doc as the extension sends it: its metadata and each tab's export result."""
    exports = [(TAB1, rev, "Paper.md", body), (TAB2, 1, "Referees.md", "# Referees\n\nThanks.\n")]
    return {
        "id": DOC, "org": ORG,
        "artifact": {"kind": "page", "id": DOC, "title": title, "type_title": "Docs",
                     "created_at": "2026-10-03T14:01:30Z", "updated_at": "2026-10-03T14:01:30Z"},
        "tabs": [{"id": tab, "export": {"verdict": "allow", "rev": r,
                                        "data": {"name": name, "format": "markdown", "bytes_b64": b64(md)}}}
                 for tab, r, name, md in exports[:tabs]],
        "deleted_tabs": [TAB3],
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
        return self.cs.render_markdown(conv, entry_for(conv), {})

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

    def test_pasted_text_is_labelled_as_such(self):
        self.assertIn("**Pasted text**\n\n```\npasted\n```", self.render())

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
        org = cs.render_org(conv, dict(entry_for(conv), org_mode_id="ABC-123"), {})
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
        return self.cs.store(conv, dict(meta or {}, org=ORG), index, {}, **kw), index

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
        changed = self.cs.update_listing([{"uuid": CID, "is_starred": False, "name": "New name"}], index, {})
        self.assertEqual(changed, [CID])
        self.assertIn("new-name", index[CID]["file"])
        with open(os.path.join(self.cs.RAW, f"{CID}.json")) as f:
            self.cs.store(json.load(f), None, index, {}, force=True)   # what `render` does
        self.assertFalse(index[CID]["is_starred"])
        self.assertEqual(index[CID]["name"], "New name")
        self.assertEqual(index[CID]["org"], ORG)

    def test_app_copy_replaces_an_imported_one(self):
        exported = export_conversation(cid=CID)
        exported["updated_at"] = conversation()["updated_at"]
        exported["_claude_sync"] = {"org": None, "source": "export"}
        index = {}
        self.cs.store(exported, None, index, {})
        self.assertEqual(index[CID]["source"], "export")
        written, index = self.stored(conversation(), index=index)   # same updated_at
        self.assertTrue(written)
        self.assertEqual((index[CID]["source"], index[CID]["org"]), ("api", ORG))

    def test_data_repo_ignores_rendered_output(self):
        # No trailing slash, so a symlinked output folder is ignored too.
        with open(os.path.join(self.cs.DATA, ".gitignore")) as f:
            self.assertEqual(f.read(), "/markdown\n/org\n")


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.cs = load({"data_dir": os.path.join(self.tmp, "data")}, self.tmp)
        self.cs.ensure_data_repo()

    def store_chat(self):
        conv = doc_conversation()
        conv["_claude_sync"] = {"org": ORG, "source": "api"}
        index, docs = {}, {}
        self.cs.note_document_refs(conv, ORG, docs)
        self.cs.store(conv, {"org": ORG}, index, docs)
        return index, docs

    def read_output(self, rel):
        with open(os.path.join(self.cs.OUTPUT, rel)) as f:
            return f.read()

    def test_refs_name_the_doc_and_its_tabs(self):
        self.assertEqual(self.cs.document_refs(doc_conversation()), {DOC: [TAB1, TAB2, TAB3]})
        self.assertEqual(self.cs.document_refs(conversation()), {})

    def test_chat_links_its_doc_before_and_after_the_doc_is_fetched(self):
        index, docs = self.store_chat()
        entry = index[CID]
        self.assertEqual(entry["documents"], [DOC])
        self.assertEqual(docs[DOC], {"kind": "doc", "org": ORG, "tabs": [TAB1, TAB2, TAB3]})
        self.assertIn(f"## Documents\n\n- [{DOC}](https://claude.ai/code/artifact/{DOC})\n",
                      self.read_output(entry["file"]))
        self.assertTrue(self.cs.store_document(doc_record(), docs, index))
        doc = docs[DOC]
        self.assertEqual(doc["file"], f"documents/2026/2026-10-03--paper--{DOC[-12:]}.md")
        link = os.path.relpath(doc["file"], os.path.dirname(entry["file"]))
        self.assertIn(f"- [Paper]({link})", self.read_output(entry["file"]))   # relinked

    def test_stored_doc_has_the_documented_fields(self):
        index, docs = self.store_chat()
        self.cs.store_document(doc_record(), docs, index)
        doc = docs[DOC]
        self.assertEqual((doc["kind"], doc["title"], doc["org"]), ("doc", "Paper", ORG))
        self.assertEqual(doc["tabs"], [TAB1, TAB2])            # TAB3 no longer exists
        self.assertEqual(doc["deleted_tabs"], [TAB3])
        self.assertEqual(doc["version"], {"title": "Paper", "tabs": {TAB1: 3, TAB2: 1}})
        self.assertEqual(doc["created_at"], "2026-10-03T14:01:30Z")
        self.assertIn("fetched_at", doc)
        with open(os.path.join(self.cs.DOCS_RAW, f"{DOC}.json")) as f:
            raw = json.load(f)
        self.assertEqual(raw["tabs"][0]["export"]["data"]["text"], "# Paper\n\n## Intro\n\nVersion two.\n")
        self.assertNotIn("bytes_b64", raw["tabs"][0]["export"]["data"])
        self.assertNotIn("deleted_tabs", raw)
        # The chats no longer offer the deleted tab for fetching.
        self.cs.note_document_refs(doc_conversation(), ORG, docs)
        self.assertEqual(docs[DOC]["tabs"], [TAB1, TAB2])

    def test_doc_file_holds_each_tab(self):
        index, docs = self.store_chat()
        self.cs.store_document(doc_record(), docs, index)
        md = self.read_output(docs[DOC]["file"])
        self.assertTrue(md.startswith('---\ntitle: "Paper"\n'))
        self.assertIn(f'claude_artifact_id: "{DOC}"', md)
        self.assertIn("\n# Paper\n\n## Paper\n\n### Paper\n\n#### Intro\n\nVersion two.\n", md)
        self.assertIn("\n## Referees\n\n### Referees\n\nThanks.\n", md)
        single = dict(doc_record(tabs=1), id=DOC)
        self.cs.store_document(single, docs, index, force=True)
        self.assertIn("\n# Paper\n\n## Paper\n\n### Intro\n", self.read_output(docs[DOC]["file"]))

    def test_only_a_changed_doc_is_rewritten(self):
        index, docs = self.store_chat()
        self.assertTrue(self.cs.store_document(doc_record(), docs, index))
        self.assertFalse(self.cs.store_document(doc_record(), docs, index))
        self.assertTrue(self.cs.store_document(doc_record(rev=4, body="Version three.\n"), docs, index))
        first = docs[DOC]["file"]
        self.assertIn("Version three.", self.read_output(first))
        self.assertTrue(self.cs.store_document(doc_record(rev=4, title="Renamed", body="x\n"), docs, index))
        self.assertFalse(os.path.exists(os.path.join(self.cs.OUTPUT, first)))
        self.assertIn("[Renamed](", self.read_output(index[CID]["file"]))

    def test_documents_to_check(self):
        index, docs = self.store_chat()
        now = self.cs.to_epoch("2026-11-15T12:00:00Z")
        self.assertEqual(self.cs.documents_to_check(index, docs, now),
                         {DOC: {"org": ORG, "tabs": [TAB1, TAB2, TAB3], "version": None}})
        self.cs.store_document(doc_record(), docs, index)
        self.assertEqual(self.cs.documents_to_check(index, docs, now)[DOC]["version"],
                         docs[DOC]["version"])
        self.cs.mark_missing(DOC, docs)                     # claude.ai answered 404
        self.assertIsNone(self.cs.documents_to_check(index, docs, now)[DOC]["version"])
        self.assertTrue(os.path.exists(os.path.join(self.cs.OUTPUT, docs[DOC]["file"])))
        docs[DOC]["missing_since"] = "2026-10-01T12:00:00+00:00"   # long gone
        self.assertEqual(self.cs.documents_to_check(index, docs, now), {})
        self.assertTrue(self.cs.store_document(doc_record(), docs, index))   # it came back
        self.assertNotIn("missing_since", docs[DOC])

    def test_listener_backfills_chats_stored_before_docs_were_synced(self):
        index, _ = self.store_chat()
        del index[CID]["documents"]
        self.cs.save_index(index)
        self.cs.backfill_document_refs()
        self.assertEqual(self.cs.load_index()[CID]["documents"], [DOC])
        self.assertEqual(self.cs.load_documents()[DOC]["tabs"], [TAB1, TAB2, TAB3])


@unittest.skipUnless(shutil.which("pandoc"), "pandoc not installed")
class DocumentOrgTests(unittest.TestCase):
    def test_org_doc_and_link(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        cs = load({"format": "org", "data_dir": os.path.join(tmp, "data")}, tmp)
        cs.ensure_data_repo()
        conv = doc_conversation()
        conv["_claude_sync"] = {"org": ORG, "source": "api"}
        index, docs = {}, {}
        cs.note_document_refs(conv, ORG, docs)
        cs.store(conv, {"org": ORG}, index, docs)
        cs.store_document(doc_record(tabs=1), docs, index)
        with open(os.path.join(cs.OUTPUT, docs[DOC]["file"])) as f:
            org = f.read()
        self.assertTrue(org.startswith(f":PROPERTIES:\n:CLAUDE_ARTIFACT_ID: {DOC}\n"))
        self.assertIn(":FETCHED: [", org)
        self.assertIn("#+title: Paper\n\n* Paper\n** Intro\nVersion two.", org)
        with open(os.path.join(cs.OUTPUT, index[CID]["file"])) as f:
            chat = f.read()
        link = os.path.relpath(docs[DOC]["file"], os.path.dirname(index[CID]["file"]))
        self.assertIn(f"* Documents\n\n- [[file:{link}][Paper]]", chat)
        cs.store_document(doc_record(), docs, index, force=True)   # two tabs
        with open(os.path.join(cs.OUTPUT, docs[DOC]["file"])) as f:
            self.assertIn("* Paper\n\n** Paper\n*** Intro", f.read())


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
        self.cs.store(app_copy, {"org": ORG}, index, {})
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
        self.assertEqual(self.git_log(), ["sync: 1 fetched, 0 list changes, 0 documents, 1 Cowork sessions"])
        # An unchanged Cowork list and no new chats make no commit.
        status, done = self.request("POST", "/sync-done", {"listing": [], "summary": {}, "cowork": cowork})
        self.assertIsNone(done["commit"])
        self.assertEqual(len(self.git_log()), 1)

    def test_documents_round_trip_commits(self):
        item = {"org": ORG, "meta": {"uuid": CID}, "conversation": doc_conversation()}
        self.assertEqual(self.request("POST", "/conversations", {"items": [item]})[0], 200)
        status, checks = self.request("GET", "/documents")
        self.assertEqual(checks, {"documents": {DOC: {"org": ORG, "tabs": [TAB1, TAB2, TAB3],
                                                      "version": None}}})
        self.assertEqual(self.request("POST", "/documents", {"documents": [doc_record()], "missing": []}),
                         (200, {"written": 1}))
        status, done = self.request("POST", "/sync-done", {"listing": [],
                                                           "summary": {"fetched": 1, "documents": 1}})
        self.assertTrue(done["commit"])
        tracked = subprocess.run(["git", "-C", self.cs.DATA, "ls-files"],
                                 capture_output=True, text=True, check=True).stdout.split()
        self.assertIn(f"documents/{DOC}.json", tracked)
        self.assertIn("documents.json", tracked)
        self.assertEqual(self.git_log(), ["sync: 1 fetched, 0 list changes, 1 documents"])
        version = self.request("GET", "/documents")[1]["documents"][DOC]["version"]
        self.assertEqual(version, {"title": "Paper", "tabs": {TAB1: 3, TAB2: 1}})
        self.assertEqual(self.request("POST", "/documents", {"documents": [], "missing": [DOC]})[0], 200)
        self.assertIsNone(self.request("GET", "/documents")[1]["documents"][DOC]["version"])

    def test_state_asks_for_imported_chats_again(self):
        exported = export_conversation()
        exported["_claude_sync"] = {"org": None, "source": "export"}
        index = {}
        self.cs.store(exported, None, index, {})
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
