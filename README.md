# summarize-context

A Claude Code skill + SessionEnd hook that writes a structured `summary.json` into the working directory whenever a session closes. The output is a portable, schema-versioned snapshot of session state designed to be **loaded as context** by a fresh agent — including agents on other LLM providers — so a long-running task can survive across sessions, machines, and models.

## What it does

When a Claude Code session ends, a hook fires that:

1. Reads the JSONL transcript Claude Code already writes for the session.
2. Spawns a headless `claude --print --bare` child process against that transcript.
3. The child runs the `conversation-summary` skill (the `SKILL.md` in this repo), which produces a single JSON object conforming to the `context-handoff-merged-v3` schema.
4. The JSON is atomically written to `<session-cwd>/summary.json`.

The schema covers both **operational** state (files created/modified/deleted, commands run, errors hit, decisions and their rationale) and **interpretive** state (tone shifts, drift, user signals, prompting strategies, ethical flags). It is intentionally **descriptive, not prescriptive** — the next agent receives a state snapshot, not an action queue. The user, on their next prompt, decides what to do next.

## Intended use case

- **Cross-session continuity.** Resume a long task in a new Claude Code session without re-explaining context.
- **Cross-model handoff.** Hand off a session from Claude Opus to Sonnet (or vice versa), or from Claude to a different provider entirely. The schema is provider-agnostic — any model that reads the JSON can orient itself.
- **Cross-agent ingestion.** Other automation (scripts, agents, orchestrators) can parse the JSON to pull out files touched, decisions made, or open blockers without reading the transcript itself.
- **Audit trail.** Each session leaves a structured record of what happened, useful for retrospective review.

## Installation

```bash
git clone git@github.com:pwnpanda/summarize-context.git ~/git/Private/summarize-context
cd ~/git/Private/summarize-context
./install.sh
```

`install.sh` is idempotent. It:

- Symlinks `~/.claude/skills/conversation-summary` to this repo (so the repo is the single source of truth).
- Merges a `SessionEnd` hook entry into `~/.claude/settings.json` (preserving any existing hooks).

Restart Claude Code (or open `/hooks` once) so the watcher picks up the new hook.

### Requirements

- `claude` CLI (Claude Code) on `PATH`
- `jq`
- `bash`

## Uninstall

```bash
./uninstall.sh
```

Removes the symlink (only if it still points at this repo) and removes the hook entry from `~/.claude/settings.json`. Existing `summary.json` files in working directories are left alone.

## How a session ends up using it

1. You work in Claude Code as normal in some project directory.
2. You exit the session (`/quit`, `Ctrl+C`, etc.).
3. The `SessionEnd` hook fires asynchronously; you don't wait for it.
4. A child `claude --print --bare` reads the transcript and emits the JSON.
5. `<your-project-dir>/summary.json` now contains the snapshot.
6. Next session — in any tool — start by feeding `summary.json` into the prompt. The next agent reads it and orients itself, then waits for your next instruction.

## Manual invocation

You can also produce a summary mid-session without ending it:

```
/conversation-summary
```

The skill summarizes the visible context window and writes `summary.json` to the current `cwd`. Useful for checkpointing before a risky operation, or for handing off a session that's still running.

## Output schema

See `SKILL.md` for the authoritative schema. High-level shape:

```
session              → id, cwd, model, timestamps, turn count
session_summary      → 2–4 sentence plain-language summary
conversation_type    → technical | creative | research | planning | debugging | mixed
session_scope        → narrow | moderate | broad
objective            → primary goal, explicit constraints, implicit assumptions
outcome              → status, what was achieved, value delivered
artifacts            → files_created / modified / deleted / read
actions              → commands executed, tools used, external resources
decisions            → what + why + alternatives_rejected + reversible
knowledge_acquired   → topic + fact + source
errors_encountered   → error + context + resolution
open_threads         → unanswered_questions, unfinished_work, blockers
interaction_dynamics → roles, tone fragments, drift, annotation_notes
user_signals         → preferences, formatting, communication style, vocabulary
prompting_strategies → techniques observed, micro_prompts_used, model_adaptations
ethical_notes        → sensitive topics or null
redactions           → secret count + categories
handoff              → context_brief, what_next_session_should_know, files_relevant
```

## Design notes

- **`--bare` is critical.** The hook spawns a child `claude` process. Without `--bare`, that child would inherit the same `SessionEnd` hook and recurse on its own exit. `--bare` skips hooks (and CLAUDE.md auto-discovery, plugin sync, and other heavy startup behavior), which is exactly what we want for a one-shot subprocess.
- **Async + disowned.** The actual `claude` invocation runs in a backgrounded subshell with `disown` so it survives the parent session's exit.
- **Cost capped.** `--max-budget-usd 1` bounds per-session spend; in practice summaries cost a fraction of a cent.
- **Trivial sessions skipped.** Transcripts under 4 lines are treated as no-op sessions and produce no `summary.json`.
- **Atomic writes.** The hook writes to `summary.json.tmp` first, validates the output is valid JSON via `jq`, then renames. A failed run leaves no garbage.
- **Logs.** Errors and outcomes are appended to `~/.claude/skills/conversation-summary/last-run.log`.
- **Descriptive, not prescriptive.** The schema deliberately avoids `next_steps` style action queues. Loading a `summary.json` orients an agent; it does not instruct it.

## File layout

```
.
├── SKILL.md                      # Skill definition + JSON output schema
├── scripts/
│   └── write_summary.sh          # SessionEnd hook command
├── install.sh                    # Symlink + settings.json merge
├── uninstall.sh                  # Reverse of install
└── README.md
```
