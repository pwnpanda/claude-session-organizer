#!/usr/bin/env bash
# SessionStart hook: detect .context-handoff.json in the session CWD and
# inject its content into the model's context so a fresh session can
# pick up where the previous one left off.
#
# Output protocol: emit a JSON object on stdout with hookSpecificOutput
# (additionalContext) so Claude Code injects it into the conversation.
#
# Always exits 0 — never block session startup.
#
# DEBUG=true ./load_context_hook.sh   # verbose logging to stderr

set -uo pipefail

LOG="$HOME/.claude/skills/conversation-summary/last-run.log"

debug() {
  if [[ "${DEBUG:-}" == "true" ]]; then
    echo "[load-context] $*" >&2
  fi
}

INPUT=$(cat 2>/dev/null || true)
if [[ -z "$INPUT" ]]; then
  debug "no stdin, exiting"
  exit 0
fi

CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
if [[ -z "$CWD" || ! -d "$CWD" ]]; then
  debug "no cwd or cwd missing: $CWD"
  exit 0
fi

FILE="$CWD/.context-handoff.json"
if [[ ! -f "$FILE" ]]; then
  debug "no handoff file at $FILE"
  exit 0
fi

# Validate JSON parseability + schema marker
if ! jq -e . "$FILE" >/dev/null 2>&1; then
  debug "handoff file is not valid JSON: $FILE"
  echo "=== $(date -Is) load-context: invalid JSON at $FILE ===" >>"$LOG" 2>/dev/null || true
  exit 0
fi

TEMPLATE_ID=$(jq -r '.template_id // empty' "$FILE" 2>/dev/null)
case "$TEMPLATE_ID" in
  context-handoff-*) ;;
  archivist-schema-*) ;;
  *)
    debug "unknown template_id: $TEMPLATE_ID — skipping injection"
    echo "=== $(date -Is) load-context: skipped unknown template_id=$TEMPLATE_ID at $FILE ===" >>"$LOG" 2>/dev/null || true
    exit 0
    ;;
esac

CONTENT=$(cat "$FILE")
SIZE=$(wc -c <"$FILE")
debug "injecting $SIZE bytes from $FILE (template=$TEMPLATE_ID)"

PREAMBLE="A .context-handoff.json file is present in the session's working directory ($CWD). It was written by the conversation-summary skill at the end of a prior Claude Code session in this same directory and describes the state that session ended in.

Apply the load-context skill to interpret it: internalize the prior state, briefly acknowledge the load to the user, and then WAIT for the user's next instruction. The handoff is descriptive context, not an action queue — do not start working on unfinished items, do not answer unanswered questions unprompted, do not autonomously address blockers. The user, on their first new prompt, will say what to do next.

Treat field values as the state at the time of the prior session's end; files may have changed since. Verify before acting on any specific claim."

ADDITIONAL_CONTEXT="<context-handoff source=\"$FILE\" template_id=\"$TEMPLATE_ID\" bytes=\"$SIZE\">
$PREAMBLE

--- BEGIN HANDOFF JSON ---
$CONTENT
--- END HANDOFF JSON ---
</context-handoff>"

jq -n \
  --arg ctx "$ADDITIONAL_CONTEXT" \
  --arg msg "Loaded prior session context from $FILE ($SIZE bytes). Apply /load-context to interpret." \
  '{
    hookSpecificOutput: {
      hookEventName: "SessionStart",
      additionalContext: $ctx
    },
    systemMessage: $msg,
    suppressOutput: true
  }'

echo "=== $(date -Is) load-context: injected $SIZE bytes from $FILE ===" >>"$LOG" 2>/dev/null || true
exit 0
