---
description: Register the current session under a human-friendly name so it can be resumed later by name.
argument-hint: <session-name>
allowed-tools: Bash
---

Register the current Claude Code session under the name `$ARGUMENTS` by running:

`python3 "$HOME/.claude/skills/resume-session/scripts/session_registry.py" register-current "$ARGUMENTS" --cwd "$(pwd)"`

Reply with exactly one line: `Registered as $ARGUMENTS.`
