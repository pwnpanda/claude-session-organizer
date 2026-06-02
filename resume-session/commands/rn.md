---
description: Register the current session under a short human-friendly name so it can be resumed later by name.
argument-hint: <session-name>
allowed-tools: Bash
---

<!-- DUPLICATE OF register.md (same body, different command name). If you edit this file, edit register.md too. Both call session_registry.py directly because slash commands embedded in command bodies do not re-invoke other slash commands. -->

Register the current Claude Code session under the name `$ARGUMENTS` by running:

`python3 "$HOME/.claude/skills/resume-session/scripts/session_registry.py" register-current "$ARGUMENTS" --cwd "$(pwd)"`

Reply with exactly one line: `Registered as $ARGUMENTS.`
