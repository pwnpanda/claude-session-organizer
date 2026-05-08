#!/usr/bin/env bash
# SessionEnd hook: spawn a headless `claude --print --bare` against the
# transcript and write a structured summary.json into the session CWD.
#
# `--bare` is critical here: it skips hooks in the child, preventing this
# very hook from re-triggering recursively when the child exits.

set -uo pipefail

LOG="$HOME/.claude/skills/conversation-summary/last-run.log"
SKILL_DIR="$HOME/.claude/skills/conversation-summary"

INPUT=$(cat 2>/dev/null || true)
if [[ -z "$INPUT" ]]; then
  exit 0
fi

CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
TRANSCRIPT=$(printf '%s' "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null)
SESSION_ID=$(printf '%s' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)

[[ -z "$CWD" || -z "$TRANSCRIPT" ]] && exit 0
[[ ! -d "$CWD" ]] && exit 0
[[ ! -f "$TRANSCRIPT" ]] && exit 0

# Skip trivial sessions (no real conversation happened).
TRANSCRIPT_LINES=$(wc -l <"$TRANSCRIPT" 2>/dev/null || echo 0)
if [[ "$TRANSCRIPT_LINES" -lt 4 ]]; then
  exit 0
fi

OUT="$CWD/summary.json"
SKILL_BODY=$(cat "$SKILL_DIR/SKILL.md" 2>/dev/null || true)
if [[ -z "$SKILL_BODY" ]]; then
  exit 0
fi

PROMPT="$SKILL_BODY

---

Apply the conversation-summary skill above to this session:
- transcript_path: $TRANSCRIPT
- session_id: $SESSION_ID
- cwd: $CWD

Read the transcript with the Read tool. Output ONLY the JSON object specified in the schema. No markdown fences, no preamble, no commentary."

TRANSCRIPT_DIR=$(dirname "$TRANSCRIPT")

{
  echo "=== $(date -Is) session=$SESSION_ID cwd=$CWD ==="
  claude --print \
    --bare \
    --no-session-persistence \
    --permission-mode bypassPermissions \
    --allowed-tools "Read" \
    --add-dir "$TRANSCRIPT_DIR" \
    --max-budget-usd 1 \
    --output-format text \
    "$PROMPT" >"$OUT.tmp" 2>>"$LOG"
  STATUS=$?
  if [[ $STATUS -eq 0 && -s "$OUT.tmp" ]] && jq -e . "$OUT.tmp" >/dev/null 2>&1; then
    mv "$OUT.tmp" "$OUT"
    echo "ok -> $OUT" >>"$LOG"
  else
    echo "fail status=$STATUS size=$(wc -c <"$OUT.tmp" 2>/dev/null || echo 0)" >>"$LOG"
    rm -f "$OUT.tmp"
  fi
} >>"$LOG" 2>&1 &

disown 2>/dev/null || true
exit 0
