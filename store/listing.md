# Chrome Web Store listing

The text entered in the Chrome Web Store developer dashboard, kept here so
each update starts from the published version. `bin/package` builds the zip
to upload.

## Store listing tab

**Name** (from the manifest): Claude Sync

**Summary** (from the manifest's `description`, at most 132 characters):
Keeps a local, git-versioned copy of your claude.ai conversations via the claude-sync listener.

**Description:**

Unofficial; not affiliated with or endorsed by Anthropic.

Claude Sync keeps an always-current copy of your claude.ai conversations on
your own computer, as Markdown or Org files, with every change versioned in
git.

It works with a small companion program, the claude-sync listener, which you
run on your computer (macOS or Linux, Python 3.9+ and git). Setup
instructions: https://github.com/benthamite/claude-sync

How it works:
• Every 30 minutes while Chrome is open, and when you open claude.ai, the
  extension uses your existing claude.ai login to check for new and changed
  conversations.
• It hands each one to the listener at 127.0.0.1 on your own machine, which
  saves the full conversation (every branch, tool call and attachment text),
  commits it to a local git repository, and renders one readable file per
  chat.
• After the first backfill, each sync downloads only what changed.
• It also saves the list of your Cowork sessions and the folders attached to
  them.
• If your login belongs to several organizations (say a personal one and a
  Team one), chats from each are synced.

Privacy: your conversations go only to the listener on your own computer.
The extension has no server, sends nothing to its developer or anyone else,
and has no analytics or tracking.

Caveat: claude.ai offers no public API for reading your chats, so Claude
Sync uses the same internal web endpoints the claude.ai app uses. They can
change without notice and break the sync until it is updated. Use it on your
own account only.

Open source (MIT): https://github.com/benthamite/claude-sync

**Category:** Productivity → Tools

**Language:** English

**Store icon:** `store/icon128.png` (the toolbar icon with the padding the store asks for; `store/icon.svg`)

**Screenshots** (1280×800): `store/screenshot-1.png`, `store/screenshot-2.png`, made by `store/make-screenshots` from invented chats

**Homepage URL:** https://github.com/benthamite/claude-sync

**Support URL:** https://github.com/benthamite/claude-sync/issues

## Privacy practices tab

**Single purpose:**
Keep a local backup of the user's own claude.ai conversations by passing
them to a companion program on the user's own computer.

**Permission justifications:**

- `alarms`: schedules the sync that runs every 30 minutes while Chrome is
  open.
- `storage`: keeps the extension's own status (time of the last sync, the
  last error) for the toolbar popup. No conversation content is stored.
- Host permission `https://claude.ai/*`: reads the user's own conversations
  and Cowork session list from claude.ai, with the user's existing login,
  and runs a content script that only signals that a claude.ai page loaded,
  so a sync can start.
- Host permission `http://127.0.0.1:8767/*`: hands the conversations to the
  claude-sync listener, a program the user runs on their own computer. The
  address never leaves the machine.

**Remote code:** No, I am not using remote code.

**Data usage:** the extension handles
- Personal communications (the user's claude.ai conversations), and
- Website content (the same conversations as the site serves them).

It handles them only to save them on the user's own computer. Nothing is
sent to the developer or any third party.

**Certifications:** all three (not sold to third parties; not used or
transferred for purposes unrelated to the single purpose; not used or
transferred to determine creditworthiness or for lending).

**Privacy policy URL:**
https://github.com/benthamite/claude-sync/blob/main/PRIVACY.md

## Distribution tab

**Visibility:** Public. **Regions:** all.
