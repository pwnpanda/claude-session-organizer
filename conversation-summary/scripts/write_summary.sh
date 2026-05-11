#!/usr/bin/env bash
# SessionEnd hook: spawn a headless `claude --print --bare` against the
# transcript and write a structured .context-handoff.json into the
# session CWD. The companion load-context skill (and its SessionStart
# hook) ingests this file when a fresh session starts in the same dir.
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

# Write the handoff at the git repo root if cwd is inside a repo, so the
# summary travels with the project rather than being pinned to whichever
# subdirectory the session happened to start in. Fall back to cwd if not
# in a repo (e.g. an ad-hoc shell session in $HOME).
OUT_DIR=$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null || true)
[[ -z "$OUT_DIR" ]] && OUT_DIR="$CWD"
OUT="$OUT_DIR/.context-handoff.json"
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

# Defense in depth: `--bare` already suppresses hooks in the child, but
# setting CLAUDE_NO_AUTO_REGISTER=1 ensures resume-session's SessionEnd
# hook would skip auto-registration even if --bare were ever bypassed.
export CLAUDE_NO_AUTO_REGISTER=1

{
  echo "=== $(date -Is) session=$SESSION_ID cwd=$CWD out=$OUT ==="
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
