# session-organizer

Name and resume Claude Code, Codex, and Gemini sessions by
human-friendly name instead of hunting for UUIDs.

Provides:

- The `/save <session-name>` slash command.
- The `/rn <session-name>` alias for `/save`.
- The `resume-session` skill (Claude knows when to invoke it from
  phrases like "resume foo" or "list my sessions").
- Two Claude Code hooks that keep the Claude registry fresh automatically:
  - `UserPromptSubmit` catches `/rename <name>` (legacy path).
  - `SessionEnd` refreshes `last_updated` and auto-registers any
    unnamed session under a slug derived from its first prompt.
- A registry CLI at `scripts/session_registry.py` (list, get, resume,
  keep, prune, etc). It supports `--agent auto|claude|codex|gemini`.

Registry data is per-agent, per-machine runtime state:

- Claude: `~/.claude/session-names/index.json`
- Codex: `~/.codex/session-names/index.json`
- Gemini: `~/.gemini/session-names/index.json`

Codex registrations also update `~/.codex/state_5.sqlite` so
`codex resume <name>` works with the native Codex resume command.

## Install

```bash
cd ~/git/priv/session-organizer  # or wherever you cloned it
./install.sh
```

The installer:

1. Symlinks `~/.claude/skills/resume-session` -> this repo.
2. Symlinks `~/.claude/commands/save.md` -> `commands/save.md` here.
3. Symlinks `~/.claude/commands/rn.md` -> `commands/rn.md` here.
4. Merges the two hooks into `~/.claude/settings.json` (idempotent —
   safe to re-run).

Restart Claude Code afterwards so the new hook config takes effect.

## Use

In any Claude Code session:

- `/save my-session` — register the current session under a name.
- `/rn my-session` — same as `/save`, shorter to type.
- "Resume my-session" — the skill looks up the entry and prints the
  matching `cd <cwd> && <agent resume command>`.
- "List my sessions" — Claude shows the registry sorted newest-first.
- "Keep my-session" — marks the entry MUSTKEEP and snapshots its
  transcript to `~/.claude/session-names/backups/`.

From the shell, the registry CLI is the source of truth:

```bash
python3 ~/.claude/skills/resume-session/scripts/session_registry.py list
python3 ~/.claude/skills/resume-session/scripts/session_registry.py resume-cmd my-session
python3 ~/.claude/skills/resume-session/scripts/session_registry.py --agent codex list
python3 ~/.claude/skills/resume-session/scripts/session_registry.py --agent gemini list
```

## Relocating a session to a different directory

`claude --resume <id>` finds the transcript under
`~/.claude/projects/<encoded-cwd>/<id>.jsonl`, where encoded-cwd is the
launch shell's cwd with `/` replaced by `-`. To make `cc-resume my-name`
launch from a *different* directory than the session originally used:

```bash
session_registry.py move <name> <new-cwd>
```

This moves the JSONL under the new encoded path, rewrites the embedded
`"cwd"` fields, and updates the registry. **Only run after the session
has exited** — a live claude rebinds the transcript to its launch-cwd
and recreates the JSONL at the original path within seconds, undoing
the move. The command refuses to act if the JSONL was modified in the
last 300 s (override with `--live-mtime-secs` or `--force`) or if any
process has the file open.

Pairs cleanly with [claude-auto-resume](https://github.com/pwnpanda/claude-auto-resume),
which keeps long-running sessions alive across rate-limit windows; once
those sessions end, `move` lets you file the transcript under the
project directory it actually belongs to.

## Move to another machine

1. Clone (or `rsync`) this directory onto the new machine. Any path
   works; the installer reads its own location at runtime.
2. Run `./install.sh`.
3. (Optional) Copy `~/.claude/session-names/index.json` from the old
   machine if you want past entries to follow you. The `cwd` and
   `session_id` fields will only resolve on a machine that has the
   matching transcript files under `~/.claude/projects/`, so entries
   from one machine usually can't be resumed on another — but the
   names, summaries, and timestamps are still useful as a record.

## Uninstall

```bash
./uninstall.sh
```

Removes the symlinks and the hook entries. Leaves
`~/.claude/session-names/` (runtime data) intact.

## Layout

```
session-organizer/
├── README.md
├── install.sh
├── uninstall.sh
├── SKILL.md             # the resume-session skill body
├── commands/
│   ├── save.md          # the /save slash command
│   └── rn.md            # the /rn alias
├── scripts/
│   ├── session_registry.py
│   ├── rename_hook.py
│   └── session_end_hook.py
└── references/
    └── setup.md         # hook wiring reference (read by Claude)
```
