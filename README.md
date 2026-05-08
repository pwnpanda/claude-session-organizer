# summarize-context

Two paired Claude Code skills + hooks that round-trip session context across sessions, machines, and LLM providers via a structured JSON file in the working directory.

## What this does

- **At session end:** the `conversation-summary` skill (driven by a `SessionEnd` hook) reads the Claude Code transcript, spawns a headless `claude --print --bare` child to compress it into a structured snapshot, and atomically writes `.context-handoff.json` to the session's working directory.
- **At session start:** the `load-context` skill (driven by a `SessionStart` hook) detects `.context-handoff.json` in the working directory, validates its schema, and injects its contents into the new session's context. The agent acknowledges the load and then waits for the user — the handoff is descriptive context, not an action queue.

The JSON conforms to the `context-handoff-merged-v3` schema, designed to be portable across providers: any model that can read the JSON can orient itself to the prior session's state without having seen the original transcript.

## Use cases

- Resume a long task in a new Claude Code session without re-explaining context.
- Hand off a session between Claude models (e.g. Opus → Sonnet) or between Claude and a different provider.
- Feed `.context-handoff.json` into other automation (scripts, agents, orchestrators) that need to know what happened in a session without parsing the raw transcript.
- Keep a structured audit trail of what each session in a project actually did.

## Requirements

- Claude Code (`claude` CLI on `PATH`)
- `jq`
- `python3` (for `install.sh` / `uninstall.sh` settings merging)
- `bash`
- Linux/macOS (paths assume `$HOME/.claude/`; not tested on Windows)

## Installation / Setup

```bash
git clone git@github.com:pwnpanda/summarize-context.git ~/git/Private/summarize-context
cd ~/git/Private/summarize-context
./install.sh
```

`install.sh` is idempotent. It:

- Symlinks `~/.claude/skills/conversation-summary` → `<repo>/conversation-summary`.
- Symlinks `~/.claude/skills/load-context` → `<repo>/load-context`.
- Merges a `SessionEnd` hook (writer) and `SessionStart` hook (loader) into `~/.claude/settings.json`. Existing hooks are preserved.

After install, restart Claude Code (or open `/hooks` once) so the file watcher picks up the new hooks.

To uninstall:

```bash
./uninstall.sh
```

Removes the two symlinks (only if they still point at this repo) and removes the two hook entries. Leaves `.context-handoff.json` files and any `.bak.*` directories alone.

## Usage

### Automatic (the common case)

1. Work in Claude Code as normal in some project directory.
2. Exit the session (`/quit`, `Ctrl+C`, etc.).
3. The `SessionEnd` hook fires asynchronously and writes `<project-dir>/.context-handoff.json`.
4. Start a new Claude Code session in the same directory.
5. The `SessionStart` hook detects the file and injects its contents. The agent acknowledges briefly: "Loaded prior session context; the previous session was working on X, with Y unresolved."
6. You give your next instruction. The agent now has the prior context loaded.

### Manual

- Mid-session checkpoint: invoke `/conversation-summary` to produce a fresh `.context-handoff.json` without ending the session.
- Manual reload: invoke `/load-context` to re-read `.context-handoff.json` from the current cwd (useful if you edited it, or if you started a session in a different cwd than the file lives in).
- Cross-provider: copy `.context-handoff.json` into a new directory, paste its contents into another LLM's chat with the instruction "load this prior-session context", and continue.

### Debugging

```bash
DEBUG=true bash ~/git/Private/summarize-context/load-context/scripts/load_context_hook.sh < /dev/null
tail -f ~/.claude/skills/conversation-summary/last-run.log
```

The log records both writer and loader runs.

## Testing

No automated tests — this is a small set of shell scripts wired to Claude Code lifecycle hooks. Verification is manual:

- **Pipe test the writer** with a synthetic SessionEnd payload and a stub `claude` binary; check that `.context-handoff.json` lands in the target dir and is valid JSON.
- **Pipe test the loader** with a synthetic SessionStart payload pointing at a CWD that contains a sample `.context-handoff.json`; check that stdout is a valid JSON object containing `hookSpecificOutput.additionalContext`.
- **End-to-end:** run a real Claude Code session, exit, restart in the same dir, observe the systemMessage announcing the load and the agent's acknowledgement.

`shellcheck` runs cleanly on every script in this repo. Run it before committing:

```bash
shellcheck conversation-summary/scripts/*.sh load-context/scripts/*.sh install.sh uninstall.sh
```

## Deployment

This is local infrastructure for a single user's Claude Code installation, not a long-running service.

- The repo itself lives at `~/git/Private/summarize-context` (or wherever you cloned it).
- The skills are exposed to Claude Code via symlinks under `~/.claude/skills/`.
- The hooks are wired into `~/.claude/settings.json`.

To deploy to another machine: clone the repo and run `install.sh`. No daemons, no systemd units, no containers.

## Implemented features

- `conversation-summary` skill with the `context-handoff-merged-v3` JSON schema.
- `SessionEnd` hook that writes `.context-handoff.json` via headless `claude --print --bare` (the `--bare` flag prevents recursive hook spawning in the child).
- Atomic writes (`.tmp` + `jq` validation + rename); skips trivial sessions (<4 transcript lines); cost capped at `$1/run`.
- `load-context` skill that interprets the schema and waits for user instruction (descriptive load, not action queue).
- `SessionStart` hook that detects `.context-handoff.json` in the session CWD, validates `template_id` (accepts `context-handoff-*` and `archivist-schema-*` for backwards-compat), and injects via `hookSpecificOutput.additionalContext`.
- Idempotent `install.sh` / `uninstall.sh` that manage symlinks and merge/unmerge hook entries without disturbing unrelated entries.
- Logs to `~/.claude/skills/conversation-summary/last-run.log` (gitignored).

## Planned features

- Walk up from CWD to find `.context-handoff.json` in a parent project root (currently only checks the exact CWD).
- Optional `--max-budget-usd` override via env var instead of hardcoded `$1`.
- Schema migration helper for moving older `archivist-schema-v2-resilient` files forward.

## File layout

```
.
├── conversation-summary/
│   ├── SKILL.md                       # Schema + write-side instructions
│   └── scripts/
│       └── write_summary.sh           # SessionEnd hook command
├── load-context/
│   ├── SKILL.md                       # Read-side interpretation guide
│   └── scripts/
│       └── load_context_hook.sh       # SessionStart hook command
├── install.sh                         # Symlink + settings.json merge
├── uninstall.sh                       # Reverse of install
├── README.md
└── .gitignore
```

## Design notes

- **`--bare` is critical for the writer.** Without it, the child `claude` would inherit the same `SessionEnd` hook and recurse. `--bare` skips hooks in the child.
- **Async + disowned writer.** The writer backgrounds + `disown`s its `claude` invocation so it survives the parent session's exit.
- **Standardized filename.** `.context-handoff.json` (hidden, schema-named) avoids collisions with project files and binds to the schema's identity.
- **Schema gating on load.** The loader only injects files whose `template_id` matches a known prefix. Unknown templates are logged and skipped, not crashed on.
- **Descriptive, not prescriptive.** The schema and the load-context skill both make this explicit: the loader tells the agent to *acknowledge and wait*, not to start working on `unfinished_work` items.
- **Atomic writes with validation.** A failed `claude` invocation leaves no partial file; the writer only renames `.tmp` → final after `jq` confirms valid JSON.
