---
name: resume-session
description: Look up and resume previously-named Claude Code, Codex, and Gemini sessions by human-friendly name. Use when the user says "resume NAME", "continue NAME", "reopen NAME", "pick up NAME where we left off", "go back to NAME", "list my sessions", "what named sessions do I have", or similar phrasing referring to restoring a prior conversation by its registered name. Also use when the user wants to manually register, rename, or remove a named session.
---

# Resume Session

## Overview

A registry that maps human-friendly names (e.g. `MyNewChat`) to
Claude Code, Codex, and Gemini session IDs so the user can resume prior
conversations by name instead of hunting for UUIDs. Registry data lives
in one per-agent index:

- Claude: `~/.claude/session-names/index.json`
- Codex: `~/.codex/session-names/index.json`
- Gemini: `~/.gemini/session-names/index.json`

Two Claude hooks (see `references/setup.md`) keep the Claude registry up
to date automatically on `/rename` and on session exit. Codex entries
also update Codex's native SQLite thread title so `codex resume <name>`
works.

## When to trigger

Trigger whenever the user asks to:

- **Resume a named session:** "Resume `<name>`", "continue `<name>`",
  "reopen `<name>`", "pick up `<name>`".
- **Find a session by topic:** "Find my conversation about `<topic>`",
  "what session did we work on `<thing>` in?", "where was the chat
  where we did X?". Use `search` first (covers names + summaries), and
  only fall back to greping `~/.claude/projects/` transcripts if the
  registry comes back empty.
- **List named sessions:** "What sessions do I have?", "list my
  conversations", "show my saved chats".
- **Inspect one:** "Where was `<name>`?", "what's in `<name>`?".
- **Manually register:** "Register this session as `<name>`", "save
  this chat as `<name>`".
- **Remove one:** "Forget `<name>`", "delete the `<name>` session".

## How to use

The skill is driven entirely by `scripts/session_registry.py`. Always
call the script with `python3` and its absolute path so it works from
any `cwd`.

```
python3 ~/.claude/skills/resume-session/scripts/session_registry.py <command>
```

Use `--agent auto|claude|codex|gemini` before the command when you need
a specific backend. `auto` detects Codex from `CODEX_THREAD_ID`, then
active Claude/Gemini state for the current working directory.

### Resume by name

1. Run `session_registry.py resume-cmd <name>`. It prints the exact
   shell command needed for the detected agent.
   The cwd is read from the transcript file itself, so the command
   works even when the registry's cached cwd has drifted.
2. If the transcript file is missing, `resume-cmd` exits non-zero with
   a clear error — `claude --resume` would fail anyway. Offer to run
   `prune --orphans` to clean up dangling entries.
3. If the name is not found, run `search <query>` (or `list`) and show
   the user the closest matches.
4. A fresh Claude Code process cannot replace its own parent shell's
   working directory. Present the printed command to the user. They
   can either run it themselves, or prefix it with `!` in the current
   prompt to execute it in the harness shell.

Example:

```
$ python3 ~/.claude/skills/resume-session/scripts/session_registry.py resume-cmd MyNewChat
cd /home/robin/Hacking/SSTI && claude --resume 36e45e92-22e2-44e1-ac20-e426694616aa
```

### Search by keyword

When the user knows the topic but not the name, run
`session_registry.py search <query>`. It does a case-insensitive
substring match across each entry's name, first-prompt summary, and
cwd, and prints matches newest-first with summaries inline. Entries
whose transcript file is gone show `[GONE]` so the user (and you)
don't try to resume them.

```
$ python3 ~/.claude/skills/resume-session/scripts/session_registry.py search jellyfin
Jellyfin                        febcc061  2026-04-23T22:06:10  /home/robin/git/priv/homelabs/MediaServer
    Help me find a homelab server software for casting media to our tvs ...
```

If that comes up empty (e.g. the topic appears only in the body of the
conversation, not in the first prompt or cwd), retry with `--content`:

```
$ session_registry.py search "lxc.idmap" --content
proxmox-backup                  4f18e7e1  2026-04-30T18:52:06  /home/robin [content]
    Can windows read linux ext4 partitions?
```

`--content` greps the transcript files under `~/.claude/projects/`
(uses `rg` if installed, falls back to a Python scan). Hits found that
way are tagged `[content]`. Slower than the default search, so reach
for it only when the metadata search misses.

### List named sessions

Run `session_registry.py list`. Output is one row per session, sorted
newest-first: `name  short-id  last-updated  cwd  [tags]`. Tags are:

- `KEEP` — marked MUSTKEEP (snapshotted, exempt from prune).
- `auto` — auto-registered by the `SessionEnd` hook.
- `GONE` — transcript file is missing under `~/.claude/projects/`;
  the entry can't be resumed and is a candidate for `prune --orphans`.

Relay the rows as-is (or reformat as a small table) so they can pick
one. Two flags worth knowing:

- `--limit N` — only the N most recent entries (the registry can grow
  to hundreds of rows; the user usually wants the recent ones).
- `--alive` — hide `[GONE]` entries.

Combine them: `list --alive --limit 20`.

### Inspect one

Run `session_registry.py get <name>`. Prints the full JSON entry
(session id, cwd, created, last_updated, summary).

### Register the current session manually

Needed when the built-in `/rename` slash command doesn't reach the
`UserPromptSubmit` hook (see `references/setup.md` for why this can
happen). Gather:

- `<name>` from the user
- `<session-id>` — from the harness environment (`CLAUDE_SESSION_ID`)
  or from the `SessionStart` hook context if available; otherwise
  ask the user
- `<cwd>` — `pwd` in the Bash tool

Then:

```
python3 ~/.claude/skills/resume-session/scripts/session_registry.py register <name> \
  --session-id <session-id> --cwd <cwd>
```

### Remove a registration

Run `session_registry.py remove <name>`. Confirm with the user before
running since the entry is deleted immediately.

### Mark a session MUSTKEEP (backup)

Use `keep <name>` to protect a session from pruning and snapshot its
transcript to a durable backup:

```
python3 ~/.claude/skills/resume-session/scripts/session_registry.py keep MyImportantChat
```

This sets `"keep": true` on the entry and copies the transcript from
`~/.claude/projects/<dir>/<session-id>.jsonl` to
`~/.claude/session-names/backups/<name>-<shortid>.jsonl`. Kept entries
show `[KEEP]` in `list` output, are skipped by `prune` (even
`--all-auto`), and refuse `remove` unless `--force` is passed.

Undo with `unkeep <name>` (leaves the backup file on disk so the data
survives even if the user later changes their mind).

### Prune stale auto-registered entries

Auto-registered entries (created by the `SessionEnd` fallback) carry
`"auto": true` in the index and show `[auto]` in `list` output.
Manually-named entries are never pruned.

- Preview what would be removed:
  `session_registry.py prune --dry-run`
- Remove auto entries whose `last_updated` is older than 60 days (default):
  `session_registry.py prune`
- Override threshold: `--days N`.
- Purge every auto entry regardless of age: `--all-auto`.
- Remove every entry (auto or manual) whose transcript file is missing:
  `session_registry.py prune --orphans`. Combine with `--dry-run` to
  preview. MUSTKEEP entries are still skipped.

## Automatic updates

Two hooks in `scripts/` maintain the registry without user action:

- `rename_hook.py` — `UserPromptSubmit` — captures `/rename <name>`
  and registers the session. Note: the built-in `/rename` slash
  command is intercepted by Claude Code before this hook fires, so in
  practice this path rarely triggers on current versions — use
  `/save` (see `commands/save.md`) or rely on the auto-register
  fallback below.
- `session_end_hook.py` — `SessionEnd` — on quit (Ctrl+C x2 or
  `/exit`):
  1. If the session is already registered, refresh `last_updated`
     and `summary`.
  2. Otherwise, auto-register it under a slug derived from the first
     user prompt (falling back to `session-<shortid>` if the prompt
     is empty or only slash-commands). This guarantees every session
     ends up in the registry even if the user never ran `/save`.

**First-time setup:** read `references/setup.md` and add the hook
entries to `~/.claude/settings.json`. Offer to do this for the user
the first time the skill is triggered and the hooks aren't installed
yet (check by grepping `resume-session` in `~/.claude/settings.json`).

## Registry location

- Index: `~/.claude/session-names/index.json`
- Not to be confused with Claude Code's own runtime tracking dir
  `~/.claude/sessions/` (PID-keyed files owned by the harness — do
  not write there).

## Optional shell wrapper

For users who want to restore by name without copy-pasting the
`cd ... && claude --resume ...` line, source
`shell/cc-resume.sh` from `~/.zshrc` or `~/.bashrc`:

```bash
source "$HOME/.claude/skills/resume-session/shell/cc-resume.sh"
```

That gives them:

- `cc-resume <name>` — `cd`s and execs `claude --resume` in one go.
- `cc-resume` — lists the 20 most recent live sessions.
- `cc-resume -s <query>` — substring search.
- `cc-resume -h` — usage summary.

`install.sh` prints the source line and (best-effort) reports whether
each rc file already sources it.

## Prefix-resume wrapper (`airesume`)

`shell/airesume.sh` defines `airesume` — resume a named session by typing
only a name *prefix*, matched across all three agents at once. Source it
from `~/.zsh_alias` (after the `claude` / `codex` / `gemini` aliases, so
each picked session launches with the user's normal per-agent flags):

```bash
source "$HOME/.claude/skills/resume-session/shell/airesume.sh"
```

- `airesume <prefix>` — scan the claude/codex/gemini registries for
  sessions whose name starts with `<prefix>`, then resolve:
  - one match in `$PWD` → resume it;
  - several in `$PWD` → arrow-key picker;
  - none in `$PWD`, a unique exact-name (or sole overall) match
    elsewhere → resume it, `cd`-ing into its folder;
  - none in `$PWD`, several elsewhere → picker with a folder column.
- `airesume -s '<text>'` — resolve by *free-text description* instead of a
  name-prefix. The query is reduced to keywords (stopwords and short tokens
  dropped) and scored against each session's name, summary, and cwd; if
  metadata matches nothing, the Claude transcript bodies are scanned. The
  sessions tied at the top score are the result — one resumes outright,
  several open the picker.
- `airesume -c|-g|-x <prefix>` — restrict the scan to a single agent:
  `-c` claude, `-g` gemini, `-x` codex. The flag must be the first
  argument. It maps to the registry script's global `--agent` flag, and
  combines with `-s` (e.g. `airesume -c -s '...'`).
- `airesume` (optionally with `-c|-g|-x`) — list recent named sessions.
- `airesume -h` — usage summary.

It is backed by the `prefix-resume` and `search-resume` subcommands of
`session_registry.py`, which print `<agent>\t<cwd>\t<token>` (token =
session UUID for claude, name for codex, resume index for gemini) and
honour `--agent` to scan a single registry. Unlike the skill workflow,
`airesume` is a shell function the user runs themselves, so resuming from
inside a running session is not a concern.

## Safety

- Hooks always exit 0 so renames and shutdowns are never blocked.
- The registry script writes atomically via `os.replace` so a crashed
  write cannot corrupt `index.json`.
- Never invoke `claude --resume` from inside a running Claude Code
  session — that would spawn a nested instance. Always print the
  command for the user to run.
