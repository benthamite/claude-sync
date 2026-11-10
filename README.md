# claude-sync

Keep a local, always-current copy of your claude.ai conversations as Markdown
(or Org) files, with every change versioned in git.

A small Chrome extension uses your logged-in claude.ai session to fetch new
and changed conversations every 30 minutes and hands them to a listener on
`127.0.0.1`, which saves the raw JSON, commits it, and renders one file per
chat. After the first backfill, each sync downloads only what changed. It also
saves the list of your Cowork sessions and the folders attached to them.

> [!WARNING]
> claude-sync reads your chats through claude.ai's **undocumented internal web
> API** (the one claude.ai itself uses). It is not affiliated with or endorsed
> by Anthropic. The API can change without notice and break the sync, and
> automated access to the service may conflict with
> [Anthropic's terms](https://www.anthropic.com/legal/consumer-terms). Use it at
> your own risk, on your own account only. If you just want a one-off copy,
> claude.ai's official export (Settings → Privacy → Export data) is the
> sanctioned route; `claude-sync import` can read it.

## What you get

```
data/
├── raw/<conversation-uuid>.json   # full conversations with every branch (source of truth)
├── index.json                     # names, timestamps, organizations, file names
├── cowork/sessions.json           # your Cowork sessions and their folders
├── markdown/2026/2026-11-10--planning-the-trip--1a2b3c4d.md
└── .git/                          # one commit per sync that changed something
```

Each Markdown file has YAML front matter (title, URL, created/updated, model,
organization, project, starred/archived) and one `##` heading per turn. Only
the branch you see in claude.ai is rendered; edits and retries stay in the raw
JSON. Claude's replies are rendered in full; its thinking is quoted, tool calls
show the tool's name and input, and tool results and attachments are cut to
2,000 characters in the rendered file (the raw JSON keeps them whole). Chats
you delete in claude.ai are kept locally.

If your login belongs to several organizations (say a personal one and a Team
one), chats from every organization with chats are synced, each recorded with
its organization.

## Requirements

- macOS or Linux with Python 3.9+ and git
- Google Chrome (or another Chromium browser that loads unpacked extensions)
- [pandoc](https://pandoc.org/installing.html), only for Org output

## Setup

1. **Clone** the repo anywhere, e.g. `~/claude-sync`.
2. **Start the listener** and leave it running:
   ```sh
   ~/claude-sync/bin/claude-sync serve
   ```
   To start it at login on macOS, adapt `contrib/claude-sync.plist` (the
   comments inside say how). On Linux, a systemd user service running the same
   command works.
3. **Install the extension** from the
   [Chrome Web Store](https://chromewebstore.google.com/detail/mfoglmpaedcenmkbdclbjljncebkflal),
   in the Chrome profile in which you are logged in to claude.ai. To run the
   code in your clone instead (say, to change it), open `chrome://extensions`,
   turn on Developer mode, click *Load unpacked* and choose the `extension/`
   folder; both installs have the same ID, so use one or the other.
4. **Sync:** open claude.ai, or click the extension's toolbar icon and then
   *Sync now*. The first run downloads every conversation, about one a second.
   Progress continues across runs.

Check progress with `bin/claude-sync status`.

## Configuration

Optional settings live in `~/.config/claude-sync/config.json`:

```json
{
  "format": "org",
  "output_dir": "~/notes/claude"
}
```

| Key | Default | Meaning |
|---|---|---|
| `format` | `"markdown"` | `"markdown"` or `"org"` |
| `output_dir` | `<data_dir>/<format>` | Where rendered files go |
| `data_dir` | `data/` in the clone | Raw JSON, index and its git repo |
| `state_dir` | `~/.local/state/claude-sync` | Last-sync record and pause file |
| `orgs` | every organization with chats | List of organization UUIDs to sync |

`bin/claude-sync status` lists the organizations synced so far with their
UUIDs. Restart the listener after changing settings, then run
`bin/claude-sync render` to re-render everything in the new format or
location. Instead of setting `output_dir`, you can also replace
`data/<format>` with a symlink.

## Commands

```sh
bin/claude-sync serve                  # run the listener (127.0.0.1:8767)
bin/claude-sync status                 # counts, paths and the last sync
bin/claude-sync render                 # re-render all chats from raw JSON
bin/claude-sync import EXPORT [--org UUID]   # merge an official export (.zip, folder or conversations.json)
```

To pause syncing, `touch ~/.local/state/claude-sync/paused`; the listener
refuses requests and the extension stops before contacting claude.ai. Delete
the file to resume.

## How it works

- **Extension** (`extension/`): its background service worker lists your
  organizations via `/api/organizations`, then each one's conversations via
  `/api/organizations/<org>/chat_conversations_v2` (and each project's, in
  case the main list leaves project chats out), compares each `updated_at`
  with what the listener already has, and fetches changed ones via
  `/api/organizations/<org>/chat_conversations/<uuid>`. It then lists your
  Cowork sessions via `/v1/code/sessions`. It runs every 30 minutes while
  Chrome is open, when you load claude.ai (at most every 10 minutes), and
  when you click *Sync now* in its popup, which also refetches the chat open
  in the current tab. No claude.ai tab needs to stay open. It waits out rate
  limits.
- **Self-reload**: on each sync the extension sends the listener a hash of
  the code it has loaded; the listener compares it with the files in
  `extension/` and logs a mismatch. The extension then reloads itself, so
  edits to its code take effect without clicking reload on the Extensions
  page. Only unpacked installs do this; Chrome updates a Web Store install.
- **Listener** (`bin/claude-sync`): a dependency-free Python script bound to
  `127.0.0.1`. It only accepts writes from the extension's fixed origin
  (`chrome-extension://mfoglmpaedcenmkbdclbjljncebkflal`, pinned by the `key`
  in the manifest), writes `raw/`, `index.json` and `cowork/sessions.json`,
  commits them, and renders.

**Errors** show as a red `!` badge on the toolbar icon. Click the icon for the
message; if claude.ai logged you out, the popup links to the login page.
The listener records each completed sync in
`~/.local/state/claude-sync/last-sync.json`, which you can monitor.

## Data layout

Other tools can read the data directory. These files and fields are kept
stable:

- **`raw/<conversation-uuid>.json`**: the response of
  `GET /api/organizations/<org>/chat_conversations/<uuid>?tree=True&rendering_mode=messages&render_all_tools=true`,
  unchanged apart from formatting (keys sorted, indented): every message on
  every branch, with its content blocks (`text`, `thinking`, `tool_use`,
  `tool_result`, in full), `attachments` (with their extracted text) and
  `files`, plus the conversation's `model`, `settings` and
  `current_leaf_message_uuid`. claude-sync adds one top-level key,
  `_claude_sync`, with `org` (the organization's UUID, or `null` if unknown)
  and `source` (`"api"`, or `"export"` for a chat imported from the official
  export, which has the export's shape until the next sync replaces it with
  the app's copy).
- **`index.json`**: an object keyed by conversation UUID. Each entry has
  `org`, `org_name`, `name`, `created_at`, `updated_at` (as the app's
  conversation list gives it, or the conversation's own if that is later),
  `model`, `project_uuid`, `project` (its name),
  `is_starred`, `is_archived`, `source`, `file` (the rendered file, relative
  to the output directory) and `fetched_at` (when the listener stored this
  version). Timestamps from claude.ai are ISO 8601 in UTC; `fetched_at` is
  ISO 8601 with the local offset.
- **`cowork/sessions.json`**: the latest list of Cowork sessions:
  `fetched_at`, `orgs` (the organizations listed) and `sessions`, each with
  `id`, `org`, `title`, `created_at`, `updated_at`, `last_event_at`,
  `user_message_count` and `folders` (the folders attached to the session).
  It holds no session content. It is rewritten on every sync but committed
  only when the list changes.
- **`~/.local/state/claude-sync/last-sync.json`**: the last completed sync:
  `at`, `summary` (`listed`, `fetched`, `cowork_sessions`),
  `listing_changes`, `commit` (or `null` if nothing changed), `errors` (a
  list; empty when all went well) and `extension` (`version`,
  `install_type` as Chrome reports it, e.g. `development` for an unpacked
  install, and `code`, the loaded code's hash, or `null` outside unpacked
  installs). A sync that fails before it completes (e.g. when logged out) leaves
  the previous record in place, so a stale `at` means syncing has stopped.

## Limitations

- Images and uploaded files are not downloaded; attachments keep the text
  claude.ai extracted from them, and other files show their names.
- Only the list of Cowork sessions is saved, not their content.
- Project chats are also enumerated project by project, through an endpoint
  that was untested at release for lack of an account with project chats.
  Whether the main list includes archived chats was also untested.
- Only one claude.ai login (the one in your Chrome profile) is synced.

## Development

```sh
python3 -m unittest discover -s tests
```

## License

MIT
