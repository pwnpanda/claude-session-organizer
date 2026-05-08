---
name: load-context
description: Use when the user says "load context", "load the handoff", "resume from handoff", "pick up from summary", "ingest the handoff file", "/load-context", or otherwise asks to ingest a .context-handoff.json file from the current working directory. Reads the structured cross-agent handoff JSON written by the conversation-summary skill at the end of a prior session, orients the agent to that prior state, and waits for the user's next instruction. Manual invocation only — there is no auto-load on session start.
---

# Load Context

A `.context-handoff.json` file in the current working directory describes the **state of a prior Claude Code session** that ended in this directory. The conversation-summary skill (and its SessionEnd hook) wrote it. Your job, when the user asks you to load it, is to ingest that state into your working memory so you can pick up the collaboration where it ended — but **not** to act on it autonomously.

The handoff is **descriptive, not prescriptive**. It tells you what happened; it does not tell you what to do next. The user will tell you what to do next.

## When you are activated

The user said something like "load context", "/load-context", or another phrase that means *ingest the handoff file in this directory*. There is no auto-trigger — the user always explicitly asks.

## What to do

1. **Read** `<cwd>/.context-handoff.json` with the Read tool. If it doesn't exist, tell the user briefly ("no handoff file in this directory") and stop.
2. **Validate** that the file's `template_id` is `context-handoff-merged-v3` (or a forward-compatible successor like `context-handoff-merged-v4`, or the older `archivist-schema-*` for backwards-compat). If it's a different schema or invalid JSON, tell the user briefly and stop — do not guess.
3. **Internalize** every field as descriptive context:
   - `session.cwd` — verify it matches the current cwd. If not, flag the mismatch.
   - `objective`, `outcome` — what the user was trying to do and what was achieved.
   - `artifacts` — files created, modified, deleted, read in the prior session. Note: these may have been edited or moved since.
   - `decisions` — choices already made; do not relitigate without reason.
   - `open_threads` (`unanswered_questions`, `unfinished_work`, `blockers`) — what was unresolved.
   - `errors_encountered` — pitfalls already hit; resolutions already applied.
   - `user_signals` — preferred verbosity, formatting, communication style, vocabulary; honor these immediately.
   - `interaction_dynamics.drift_or_contradictions` — be alert to ambiguity the previous session also hit.
   - `handoff.context_brief` — the orientation paragraph; this is the highest-signal field.
   - `handoff.what_next_session_should_know` — gotchas to keep top-of-mind.
   - `handoff.files_relevant_to_continuation` — paths the user is most likely to ask about.
4. **Acknowledge the load briefly** — one or two sentences telling the user you've loaded the prior context, what the prior session was doing, and what is open. No bullet-list dump of the JSON. Don't restate everything — the user already knows; the acknowledgement is to confirm orientation.
5. **Stop. Wait for the user's next instruction.**
   - Do **not** start working on `unfinished_work` items.
   - Do **not** answer `unanswered_questions` unprompted.
   - Do **not** try to fix `blockers` autonomously.
   - The handoff is a context load, not a task queue. The user is in charge of what comes next.

## Verification

The handoff was written potentially days ago. Files may have moved, decisions may have been overruled, the user's mood may have changed. Treat the handoff as **the state at session-end-time**, not as ground truth now.

If the user's first instruction conflicts with something in the handoff, trust the user — and consider mentioning the discrepancy if it's load-bearing. Stale memory is worse than no memory; verify before relying on a remembered fact.

If the user references a file from `artifacts` or `handoff.files_relevant_to_continuation`, read the current file before answering — its contents may have changed.

## Schema reference

See `../conversation-summary/SKILL.md` for the authoritative `context-handoff-merged-v3` schema. Top-level shape:

```
session, session_summary, conversation_type, session_scope, session_tags,
key_topics, objective, outcome, artifacts, actions, decisions,
knowledge_acquired, errors_encountered, open_threads,
interaction_dynamics, user_signals, prompting_strategies,
context_artifacts, ethical_notes, redactions, handoff
```

## Anti-patterns

- ❌ Treating `unfinished_work` as a TODO list and starting on it.
- ❌ Reading every file in `artifacts.files_read` to "refresh context" — you already have the handoff; refresh on demand.
- ❌ Restating the entire handoff back to the user. They wrote it (indirectly); they don't need it read aloud.
- ❌ Trusting `errors_encountered` resolutions blindly; the underlying issue may have recurred.
- ❌ Continuing the prior session's tone or persona if the user's first new prompt suggests a different mode.
