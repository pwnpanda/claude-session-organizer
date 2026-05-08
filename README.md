# summarize-context

Two paired Claude Code skills + hooks that round-trip session context across sessions, machines, and LLM providers via a structured JSON file in the working directory.

## What this does

- **At session end (automatic):** the `conversation-summary` skill (driven by a `SessionEnd` hook) reads the Claude Code transcript, spawns a headless `claude --print --bare` child to compress it into a structured snapshot, and atomically writes `.context-handoff.json` to the session's working directory.
- **In a new session (manual):** invoke `/load-context` (or say "load context", "load the handoff", etc.). The `load-context` skill reads `.context-handoff.json` from the current working directory, validates the schema, and orients to the prior state. The agent acknowledges the load and then waits — the handoff is descriptive context, not an action queue. Loading is **always explicit** — there is no auto-load on session start.

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
- Merges a `SessionEnd` hook (writer) into `~/.claude/settings.json`. Existing hooks are preserved.
- Purges any legacy `SessionStart` hook entry left over from a previous version that auto-loaded the handoff.

After install, restart Claude Code (or open `/hooks` once) so the file watcher picks up the new hooks.

To uninstall:

```bash
./uninstall.sh
```

Removes the two symlinks (only if they still point at this repo) and removes the two hook entries. Leaves `.context-handoff.json` files and any `.bak.*` directories alone.

## Usage

### Typical round-trip

1. Work in Claude Code as normal in some project directory.
2. Exit the session (`/quit`, `Ctrl+C`, etc.).
3. The `SessionEnd` hook fires asynchronously and writes `<project-dir>/.context-handoff.json`.
4. Start a new Claude Code session in the same directory.
5. Tell Claude to load the context: `/load-context` or "load the handoff" — it reads the file, validates the schema, and acknowledges briefly ("Loaded prior session context; the previous session was working on X, with Y unresolved.").
6. You give your next instruction. The agent now has the prior context loaded and waits for direction.

### Other modes

- **Mid-session checkpoint:** invoke `/conversation-summary` to produce a fresh `.context-handoff.json` without ending the session.
- **Cross-provider:** copy `.context-handoff.json` into a new directory, paste its contents into another LLM's chat with the instruction "load this prior-session context", and continue.

### Debugging

```bash
tail -f ~/.claude/skills/conversation-summary/last-run.log
```

The log records writer runs (success, failure, skipped trivial sessions). Loading happens entirely inside the model on `/load-context` — no shell-side log.

## Testing

No automated tests — this is a small set of shell scripts wired to Claude Code lifecycle hooks. Verification is manual:

- **Pipe test the writer** with a synthetic SessionEnd payload and a stub `claude` binary; check that `.context-handoff.json` lands in the target dir and is valid JSON.
- **End-to-end:** run a real Claude Code session, exit, restart in the same dir, ask Claude to `/load-context`, and verify it reads the file and acknowledges the prior state without auto-acting.

`shellcheck` runs cleanly on every script in this repo. Run it before committing:

```bash
shellcheck conversation-summary/scripts/*.sh install.sh uninstall.sh
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
- `load-context` skill (manual invocation only via `/load-context` or natural language) that reads `.context-handoff.json` from CWD, validates the schema (accepts `context-handoff-*` and `archivist-schema-*` for backwards-compat), and orients without auto-acting.
- Idempotent `install.sh` / `uninstall.sh` that manage symlinks and merge/unmerge hook entries without disturbing unrelated entries; install.sh also purges legacy `SessionStart` auto-load entries from older versions.
- Logs to `~/.claude/skills/conversation-summary/last-run.log` (gitignored).

## Planned features

- Walk up from CWD to find `.context-handoff.json` in a parent project root (currently only checks the exact CWD).
- Optional `--max-budget-usd` override via env var instead of hardcoded `$1`.
- Schema migration helper for moving older `archivist-schema-v2-resilient` files forward to `context-handoff-merged-v3`.

## File layout

```
.
├── conversation-summary/
│   ├── SKILL.md                       # Schema + write-side instructions
│   └── scripts/
│       └── write_summary.sh           # SessionEnd hook command
├── load-context/
│   └── SKILL.md                       # Read-side interpretation guide (manual /load-context)
├── install.sh                         # Symlink + settings.json merge
├── uninstall.sh                       # Reverse of install
├── README.md
└── .gitignore
```

## Design notes

- **`--bare` is critical for the writer.** Without it, the child `claude` would inherit the same `SessionEnd` hook and recurse. `--bare` skips hooks in the child.
- **Async + disowned writer.** The writer backgrounds + `disown`s its `claude` invocation so it survives the parent session's exit.
- **Standardized filename.** `.context-handoff.json` (hidden, schema-named) avoids collisions with project files and binds to the schema's identity.
- **Schema gating on load.** The loader (the model under `/load-context`) only ingests files whose `template_id` matches a known prefix. Unknown templates are flagged to the user and skipped.
- **Manual-only load.** Earlier iterations auto-injected the handoff via a `SessionStart` hook; that was removed in favor of explicit user invocation. Loading is now always opt-in per session.
- **Descriptive, not prescriptive.** The schema and the load-context skill both make this explicit: after `/load-context`, the agent acknowledges and waits, never starts working on `unfinished_work` items.
- **Atomic writes with validation.** A failed `claude` invocation leaves no partial file; the writer only renames `.tmp` → final after `jq` confirms valid JSON.
