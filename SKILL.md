---
name: conversation-summary
description: Use when generating a structured JSON snapshot of a Claude Code conversation for cross-LLM and cross-agent context handoff. Produces a descriptive state-of-the-session document — not an instruction set — covering session metadata, objective, outcome, artifacts touched, decisions, errors, open threads, interaction dynamics (tone, drift), user signals, and a self-contained orientation brief. Invoked automatically by the SessionEnd hook to write summary.json into the session CWD; can also be invoked manually mid-session via /conversation-summary to capture state without ending the session. The output is a context load for a fresh agent to read at the start of a new session — never an action queue.
---

# Conversation Summary

Compress the current conversation into a single JSON object that describes **where the session ended up** so a fresh model — from any provider — can load that context and continue collaborating with the user. The output is a state snapshot, not a directive: it describes what happened, what was decided, and what is unresolved. It does **not** instruct the next agent to do anything. The user, on their next prompt, will say what to do next.

## Inputs

When invoked from the SessionEnd hook, the prompt provides:

- `transcript_path` — absolute path to a JSONL transcript file
- `session_id` — UUID of the session
- `cwd` — absolute working directory of the session

Read the transcript with the Read tool. Each line is a JSON object with at least `type` (`user`, `assistant`, `system`) and `message.content`. Tool calls and results appear inside `assistant` and `user` content blocks. Parse selectively — long tool outputs and repeated reads are low-signal.

Apply Tree-of-Thought reasoning when interpreting ambiguous exchanges. Detect tone shifts, sarcasm, goal drift, and contradictions. Flag them in `interaction_dynamics` — don't silently discard them.

## Output schema

Emit exactly one JSON object. **All fields are required** — use `null`, `""`, or `[]` for fields with no applicable data; never omit them. All paths must be absolute.

```json
{
  "schema_version": "3.0",
  "template_id": "context-handoff-merged-v3",
  "generated_at": "<ISO 8601 UTC, e.g. 2026-05-08T14:23:00Z>",

  "session": {
    "id": "<session uuid>",
    "cwd": "<absolute path>",
    "model": "<model id or null>",
    "started_at": "<ISO 8601 of first user turn or null>",
    "ended_at": "<ISO 8601 of last turn or null>",
    "approximate_turns": 0
  },

  "session_summary": "<2-4 plain-language sentences describing what happened>",
  "conversation_type": "technical|creative|research|planning|debugging|mixed",
  "session_scope": "narrow|moderate|broad",
  "session_tags": ["<lowercase-hyphenated grep-friendly tag>"],
  "key_topics": ["<topic>"],

  "objective": {
    "primary": "<one sentence: what the user was trying to accomplish>",
    "explicit_constraints": ["<user-stated constraints, requirements, rules>"],
    "implicit_assumptions": ["<assumptions made without explicit user confirmation>"]
  },

  "outcome": {
    "status": "completed|partial|abandoned|in_progress",
    "summary": "<what was actually achieved this session>",
    "value_provenance": "<the core value delivered, in one sentence>"
  },

  "artifacts": {
    "files_created": [{"path": "<absolute>", "purpose": "<why>"}],
    "files_modified": [{"path": "<absolute>", "change_summary": "<what changed and why>"}],
    "files_deleted": [{"path": "<absolute>", "reason": "<why>"}],
    "files_read": ["<absolute path>"]
  },

  "actions": {
    "commands_executed": [
      {"command": "<shell cmd>", "purpose": "<why>", "outcome": "success|failure|partial"}
    ],
    "tools_used": ["<tool name, e.g. Read, Edit, Bash, WebFetch>"],
    "external_resources": [
      {"type": "url|doc|api|package", "value": "<identifier>", "purpose": "<why referenced>"}
    ]
  },

  "decisions": [
    {
      "decision": "<what was decided>",
      "rationale": "<why>",
      "alternatives_rejected": ["<options considered and dropped>"],
      "reversible": true
    }
  ],

  "knowledge_acquired": [
    {"topic": "<area>", "fact": "<concrete finding>", "source": "code|user|search|inference"}
  ],

  "errors_encountered": [
    {"error": "<message or symptom>", "context": "<what was being attempted>", "resolution": "<fix applied, or 'unresolved'>"}
  ],

  "open_threads": {
    "unanswered_questions": ["<question still needing an answer>"],
    "unfinished_work": ["<work that was in progress, described as state, not as a directive>"],
    "blockers": ["<unresolved blocker preventing further progress>"]
  },

  "interaction_dynamics": {
    "roles_and_personas": "<user role, Claude's role, any persona shifts>",
    "tone_fragments": [
      {"turn_range": "1-5", "tone": "<e.g. exploratory>", "notes": ""}
    ],
    "drift_or_contradictions": ["<turn reference + what shifted or conflicted>"],
    "annotation_notes": "<meta-commentary: unusual patterns, sarcasm, surprising moves>"
  },

  "user_signals": {
    "preferred_verbosity": "brief|moderate|detailed",
    "formatting_preferences": "markdown|plain|code-heavy",
    "communication_style": "<terse|detailed|technical|exploratory|...>",
    "domain_vocabulary": ["<technical terms the user used>"],
    "preferences_observed": ["<observed coding/communication preferences>"],
    "feedback_given": ["<corrections or affirmations the user explicitly stated>"]
  },

  "prompting_strategies": {
    "techniques_observed": "<chain-of-thought, few-shot, role prompting, ToT, etc.>",
    "micro_prompts_used": ["<short reusable prompt patterns extracted from the session>"],
    "model_adaptations": "<how Claude adjusted behavior, tone, or depth>"
  },

  "context_artifacts": {
    "multimodal_elements": ["<images, diagrams, attached files referenced>"],
    "tool_notes": "<tool errors, workarounds, notable behavior>"
  },

  "ethical_notes": "<sensitive topics, bias, security/safety concerns — null if none>",

  "redactions": {
    "count": 0,
    "categories": ["<types of secrets removed: api_key|token|password|credential|other>"]
  },

  "handoff": {
    "context_brief": "<self-contained paragraph describing the state of the session: where we are, what's been established, what's pending. Descriptive, not imperative — orient the next agent without telling it what to do.>",
    "what_next_session_should_know": ["<key facts, hidden constraints, surprising decisions, gotchas>"],
    "files_relevant_to_continuation": ["<absolute paths the next session likely needs to read>"],
    "handoff_format_suggestion": "code|chat|doc|structured"
  }
}
```

## Rules

- **Output ONLY the JSON object.** No leading prose, no trailing prose, no markdown code fences, no commentary.
- **Describe state; do not prescribe action.** This is a context load. The next agent will receive its own instructions from the user. Avoid imperative phrasing in `handoff.context_brief`, `open_threads.unfinished_work`, and `what_next_session_should_know`. Frame as "X is in state Y" rather than "do Y next."
- **Absolute paths only.** Resolve any relative path against `cwd`.
- **Redact secrets.** Replace API keys, tokens, passwords, and credentials with `<REDACTED:category>` and increment `redactions.count`. Never include real secrets even if they appeared in tool output.
- **Be concrete, not generic.** "Fixed the auth bug" is useless. "Replaced jose@4 with jose@5 in /home/robin/proj/auth.ts because v4 dropped Node 22 support" is useful.
- **Don't editorialize.** No "successfully", "comprehensive", "robust", "elegant". State what happened.
- **Flag drift.** If the user's goals changed mid-session, log it in `interaction_dynamics.drift_or_contradictions` with a turn reference.
- **Sarcasm/irony detected:** note in `interaction_dynamics.annotation_notes`; do not treat as literal intent.
- **Very short session (<5 turns):** still produce the full schema; use `null`/`""`/`[]` for inapplicable fields.
- **All fields required.** Empty values are fine; omitted keys are not.
- **Prioritize when truncating large transcripts** in this order: user's stated goal → most recent state → unresolved open_threads → decisions → artifacts. Drop low-signal noise (long tool outputs, repeated reads).

## Manual invocation

When invoked via `/conversation-summary` mid-session (no `transcript_path` provided), summarize the conversation visible in your current context window using the same schema. Write the result to `<cwd>/summary.json` using the Write tool, or print it to the chat if no cwd is available.
