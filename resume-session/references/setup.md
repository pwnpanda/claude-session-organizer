# Hook Setup for resume-session

This skill relies on two Claude Code hooks to keep the Claude registry
fresh automatically. The registry CLI also supports Codex and Gemini via
`--agent auto|claude|codex|gemini`, but those agents do not use these
Claude hook entries.

## What gets wired up

| Hook event | Script | Purpose |
|------------|--------|---------|
| `UserPromptSubmit` | `rename_hook.py` | Detect `/rename <name>` and register the current session under that name. |
| `SessionEnd` | `session_end_hook.py` | Refresh `last_updated` and a one-line summary for the session on quit (Ctrl+C x2 or `/exit`). |

Both hooks always exit 0 — they never block prompts or delay shutdown.

## settings.json fragment

Add (or merge) the following into `~/.claude/settings.json`.

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 $HOME/.claude/skills/resume-session/scripts/rename_hook.py"
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 $HOME/.claude/skills/resume-session/scripts/session_end_hook.py"
          }
        ]
      }
    ]
  }
}
```

If `hooks` already exists in settings.json, merge the `UserPromptSubmit`
and `SessionEnd` arrays rather than overwriting.

## Known limitation: built-in `/rename`

Claude Code may consume the built-in `/rename` slash command before the
`UserPromptSubmit` hook sees it. If the hook never fires on `/rename`,
fall back to one of these:

1. **Manual registration in-session:** ask Claude to register the
   current session — it will call
   `session_registry.py register <name> --session-id <id> --cwd <cwd>`.
2. **Custom slash command:** add a project or user slash command (e.g.
   `/save` or `/rn`) whose body calls the registry script directly.
   This bypasses the built-in handler.

## Verifying the hooks

After editing settings.json, restart Claude Code, then:

```bash
# List registered sessions
python3 ~/.claude/skills/resume-session/scripts/session_registry.py list

# Check the index file directly
cat ~/.claude/session-names/index.json
```

A session registered via `/rename MyNewChat` should appear in the list
after submitting that prompt in a session.

## Index file schema

Per-agent registry paths:

- Claude: `~/.claude/session-names/index.json`
- Codex: `~/.codex/session-names/index.json`
- Gemini: `~/.gemini/session-names/index.json`

Index entry schema:

```json
{
  "MyNewChat": {
    "agent": "claude",
    "session_id": "36e45e92-22e2-44e1-ac20-e426694616aa",
    "cwd": "/home/robin/Hacking/SSTI",
    "created": "2026-04-18T10:30:00+00:00",
    "last_updated": "2026-04-18T11:45:12+00:00",
    "summary": "Working on the auth middleware rewrite"
  }
}
```

Names are unique: registering an existing `session_id` under a new name
removes the old entry (rename semantics).
