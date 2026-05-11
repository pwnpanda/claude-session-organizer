#!/usr/bin/env bash
# SessionEnd hook: spawn a headless `claude --print --bare` against the
# transcript and write a structured .context-handoff.json at the git repo
# root of the session cwd (falling back to cwd if not in a repo). When
# the output landed in a clean git repo, also auto-commit the file so it
# travels with the project across machines. The companion load-context
# skill ingests this file when a fresh session starts in the same project.
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

# Auto-commit the handoff so it travels with the project across machines.
# Only runs when OUT lives inside a git repo (i.e. $OUT_DIR == repo root)
# and the repo is in a state where committing is safe. Hooks are skipped
# (--no-verify) because this is a background, non-interactive write and
# pre-commit hooks would block it; signing is left to user config.
maybe_commit_handoff() {
  local out_dir="$1"

  # Only commit when the file lives at a real git repo root.
  local toplevel
  toplevel=$(git -C "$out_dir" rev-parse --show-toplevel 2>/dev/null) || return 0
  [[ "$toplevel" != "$out_dir" ]] && return 0

  # Skip if mid-rebase/merge/cherry-pick/bisect — committing would corrupt state.
  local gitdir
  gitdir=$(git -C "$out_dir" rev-parse --git-dir 2>/dev/null) || return 0
  case "$gitdir" in /*) ;; *) gitdir="$out_dir/$gitdir";; esac
  for marker in MERGE_HEAD CHERRY_PICK_HEAD REBASE_HEAD BISECT_LOG rebase-merge rebase-apply; do
    if [[ -e "$gitdir/$marker" ]]; then
      echo "skip-commit: $marker present" >>"$LOG"
      return 0
    fi
  done

  # Skip detached HEAD — auto-commits there create unreachable objects.
  if ! git -C "$out_dir" symbolic-ref --quiet HEAD >/dev/null 2>&1; then
    echo "skip-commit: detached HEAD" >>"$LOG"
    return 0
  fi

  # Respect a local .gitignore that excludes the file; user opted out explicitly.
  if git -C "$out_dir" check-ignore -q .context-handoff.json 2>/dev/null; then
    echo "skip-commit: .context-handoff.json is gitignored" >>"$LOG"
    return 0
  fi

  git -C "$out_dir" add -- .context-handoff.json 2>>"$LOG" || {
    echo "skip-commit: git add failed" >>"$LOG"
    return 0
  }

  # Nothing staged (file unchanged since last commit) → nothing to do.
  if git -C "$out_dir" diff --cached --quiet -- .context-handoff.json 2>/dev/null; then
    echo "skip-commit: handoff unchanged" >>"$LOG"
    return 0
  fi

  if git -C "$out_dir" commit --no-verify --only \
      -m "chore: update claude session handoff" \
      -- .context-handoff.json >>"$LOG" 2>&1; then
    local sha
    sha=$(git -C "$out_dir" rev-parse --short HEAD 2>/dev/null || echo "?")
    echo "committed $sha" >>"$LOG"
  else
    echo "skip-commit: git commit failed (see above)" >>"$LOG"
  fi
}

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
    maybe_commit_handoff "$OUT_DIR"
  else
    echo "fail status=$STATUS size=$(wc -c <"$OUT.tmp" 2>/dev/null || echo 0)" >>"$LOG"
    rm -f "$OUT.tmp"
  fi
} >>"$LOG" 2>&1 &

disown 2>/dev/null || true
exit 0
