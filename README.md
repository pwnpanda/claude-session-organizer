# claude-session-organizer

Three paired Claude Code skills + hooks that organize and round-trip session context across sessions, machines, and LLM providers.

## What this does

- **`conversation-summary`** — at session end, a `SessionEnd` hook spawns a headless `claude --print --bare` child that reads the transcript and writes `.context-handoff.json` at the **git repo root** of the cwd (falling back to cwd if not in a repo). The summary is a structured `context-handoff-merged-v3` snapshot. When the file lands inside a clean git repo (no rebase/merge in progress, not detached HEAD, not gitignored), the hook also **auto-commits** it on the current branch as `chore: update claude session handoff` so the handoff travels with the project across machines via your normal `git push` / `git pull`. To opt out per-repo, add `.context-handoff.json` to that repo's `.gitignore`.
- **`load-context`** — in a new session, invoke `/load-context` (or say "load context", "load the handoff", etc.). The skill reads `.context-handoff.json` from the git repo root (falling back to cwd), validates the schema, orients to the prior state, and waits for the user's next instruction. Loading is **always explicit** — there is no auto-load on session start.
- **`resume-session`** — a name registry mapping human-friendly names (e.g. `my-feature-work`) to Claude Code session IDs so prior conversations can be resumed by name instead of UUID. A `UserPromptSubmit` hook catches `/save <name>`, a `SessionEnd` hook auto-registers any session that wasn't manually named, and `cc-resume <name>` (optional shell wrapper) prints/runs the exact `cd ... && claude --resume <id>` command. Subprocess Claude invocations that set `CLAUDE_NO_AUTO_REGISTER=1` in their env are skipped so pipeline workers don't pollute the registry.

## Use cases

- Resume a long task in a new Claude Code session without re-explaining context.
- Hand off a session between Claude models (Opus → Sonnet) or between Claude and a different provider.
- Resume an old session by a human-friendly name months later instead of hunting for a UUID.
- Search prior conversations by topic, name, or content via `session_registry.py search`.
- Feed `.context-handoff.json` into other automation that needs to know what a session did without parsing the raw transcript.

## Requirements

- Claude Code (`claude` CLI on `PATH`)
- `jq`, `python3`, `bash`
- `git` (for resolving the git repo root when writing the handoff)
- Linux/macOS (paths assume `$HOME/.claude/`; not tested on Windows)

## Installation

```bash
git clone git@github.com:pwnpanda/claude-session-organizer.git ~/git/priv/claude-session-organizer
cd ~/git/priv/claude-session-organizer
./install.sh
```

`install.sh` is idempotent. It:

- Symlinks `~/.claude/skills/{conversation-summary,load-context,resume-session}` → the corresponding subdirectories in this repo.
- Symlinks `~/.claude/commands/save.md` → `<repo>/resume-session/commands/save.md`.
- Merges three hooks into `~/.claude/settings.json`: `SessionEnd` → `write_summary.sh`, `SessionEnd` → `session_end_hook.py`, `UserPromptSubmit` → `rename_hook.py`. Existing hooks are preserved.
- Purges any legacy `SessionStart` auto-load entry left over from older versions.

After install, restart Claude Code (or open `/hooks` once) so the file watcher picks up the new hooks.

Optional: add the `cc-resume` shell wrapper to `.zshrc`/`.bashrc` so you can restore by name from any shell:

```bash
source "$HOME/.claude/skills/resume-session/shell/cc-resume.sh"
```

To uninstall:

```bash
./uninstall.sh
```

Removes the symlinks (only if they still point at this repo) and removes the hook entries. Leaves `.context-handoff.json` files, the session-name registry at `~/.claude/session-names/`, and any `.bak.*` directories alone.

## Usage

### Save & resume a session by name

```text
/save my-feature-work        # in a Claude Code session, registers it by name
```

Later, from any shell:

```bash
cc-resume my-feature-work    # if you sourced cc-resume.sh
# or manually:
python3 ~/.claude/skills/resume-session/scripts/session_registry.py resume-cmd my-feature-work
# → prints: cd <cwd> && claude --resume <session-id>
```

### Context round-trip

1. Work in Claude Code as normal inside a project.
2. Exit the session (`/quit`, `Ctrl+C`, etc.).
3. The `SessionEnd` hook fires asynchronously and writes `<repo-root>/.context-handoff.json`.
4. Start a new Claude Code session anywhere inside the same repo.
5. Tell Claude to load the context: `/load-context` or "load the handoff" — it reads the file, validates the schema, and acknowledges the prior state briefly.
6. Give the next instruction. The agent now has the prior context loaded and waits for direction.

### Mid-session checkpoint

Invoke `/conversation-summary` to produce a fresh `.context-handoff.json` without ending the session.

### Cross-provider handoff

Copy `.context-handoff.json` and paste its contents into another LLM's chat with the instruction "load this prior-session context", and continue.

### Debugging

```bash
tail -f ~/.claude/skills/conversation-summary/last-run.log
```

The log records writer runs (success, failure, skipped trivial sessions). The session registry lives at `~/.claude/session-names/index.json` and is updated atomically.

## Subagent / subprocess Claude invocations

Pipelines that spawn `claude` as a subprocess (e.g. SAST scanners, batch summarizers) can flood the session-name registry with one auto-registered entry per worker run. To suppress that, set `CLAUDE_NO_AUTO_REGISTER=1` in the subprocess's env:

```python
env = os.environ.copy()
env["CLAUDE_NO_AUTO_REGISTER"] = "1"
subprocess.run(["claude", "-p", prompt], env=env, check=True)
```

The `SessionEnd` hook honors the env var: it still refreshes registry entries for sessions that are already manually registered, but skips the "auto-register if not already registered" path. Manually named sessions are never affected. The `--bare` flag on `claude --print --bare` already skips hooks in the child entirely, so this env var is defense-in-depth for callers that don't (or can't) use `--bare`.

## Testing

No automated tests — these are small shell + Python scripts wired to Claude Code lifecycle hooks. Verification is manual:

- **Writer pipe test:** feed a synthetic SessionEnd payload to `write_summary.sh` with a stub `claude` binary; check that `.context-handoff.json` lands at the git repo root and is valid JSON.
- **End-to-end handoff:** run a real session, exit, restart in the same repo, `/load-context`, verify it reads and acknowledges.
- **Registry:** `/save name`, exit, `python3 session_registry.py resume-cmd name` from a fresh shell.

`shellcheck` runs cleanly on every script. Run it before committing:

```bash
shellcheck conversation-summary/scripts/*.sh install.sh uninstall.sh resume-session/shell/*.sh
```

## File layout

```
.
├── conversation-summary/             # write-side: SessionEnd hook + skill prompt
│   ├── SKILL.md
│   └── scripts/write_summary.sh
├── load-context/                     # read-side: /load-context skill prompt
│   └── SKILL.md
├── resume-session/                   # name registry + slash commands + shell wrapper
│   ├── SKILL.md
│   ├── README.md
│   ├── commands/save.md              # /save slash command
│   ├── scripts/
│   │   ├── session_registry.py       # registry CLI (resume-cmd, search, list, …)
│   │   ├── session_end_hook.py       # SessionEnd hook (registers / touches)
│   │   └── rename_hook.py            # UserPromptSubmit hook (/save <name>)
│   ├── shell/cc-resume.sh            # optional shell wrapper
│   └── references/setup.md
├── install.sh
├── uninstall.sh
├── README.md
└── .gitignore
```

## Design notes

- **`--bare` is critical for the writer.** Without it, the child `claude` would inherit the same `SessionEnd` hook and recurse. `--bare` skips hooks in the child.
- **Async + disowned writer.** The writer backgrounds + `disown`s its `claude` invocation so it survives the parent session's exit.
- **Repo-root output.** `.context-handoff.json` lives at the git repo root so the summary travels with the project, not the subdirectory the session happened to start in. The loader checks repo root first, then falls back to cwd.
- **Standardized filename.** `.context-handoff.json` (hidden, schema-named) avoids collisions with project files.
- **Schema gating on load.** The loader only ingests files whose `template_id` matches a known prefix.
- **Manual-only load.** Earlier iterations auto-injected the handoff via a `SessionStart` hook; that was removed in favor of explicit user invocation.
- **Descriptive, not prescriptive.** After `/load-context`, the agent acknowledges and waits — never starts working on `unfinished_work` items.
- **Atomic writes.** A failed `claude` invocation leaves no partial file; the writer only renames `.tmp` → final after `jq` confirms valid JSON.
- **Registry hygiene.** Subprocess Claude invocations that set `CLAUDE_NO_AUTO_REGISTER=1` are skipped by the SessionEnd hook so pipeline workers don't pollute the registry.
