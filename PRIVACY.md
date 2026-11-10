# Privacy policy

Claude Sync is a Chrome extension with a companion program (the listener)
that you run on your own computer. Together they keep a copy of your
claude.ai conversations on that computer. This policy covers both.

## What it reads

Using the claude.ai login already in your browser, the extension reads:

- the organizations your claude.ai account belongs to;
- the list of your conversations and, for each new or changed one, its full
  content: messages, Claude's replies and thinking, tool calls and results,
  attachments' extracted text and file names, and the conversation's model
  and settings;
- the list of your Cowork sessions: titles, dates, message counts and the
  folders attached to them (not their content).

It reads nothing from any other website, and it does not read your claude.ai
password or cookies: the browser attaches the cookies to its requests.

## Where it goes

Only to the listener on your own computer, at `http://127.0.0.1:8767`, a
local address that never leaves the machine. The listener writes it to a
folder on your disk and commits it to a local git repository there.

Claude Sync sends no data to its developer or to anyone else. It has no
server, no analytics, no tracking and no advertising, and it does not sell,
share or transfer your data. Nothing is sent to claude.ai except the
ordinary requests your browser makes to read your own conversations.

## What it stores in the browser

The extension keeps only its own status in Chrome's local extension storage:
when it last synced, the last error, and a hash of its own code. It stores
no conversation content there.

## Your control

The copy on your disk is yours: delete the data folder to delete it. To stop
syncing, remove the extension or create the pause file described in the
README. Uninstalling the extension removes its browser storage.

## Contact

Questions: open an issue at <https://github.com/benthamite/claude-sync/issues>.

Last updated: 10 November 2026.
