# shellcheck shell=bash
# airesume — resume a named AI session (Claude Code / Codex / Gemini) by
# name-prefix. Scans the session registries, finds the most likely target,
# and resumes it — picking interactively when several match.
#
# Usage:
#   airesume [-c|-g|-x] <prefix>   resolve <prefix> and resume
#   airesume [-c|-g|-x]            list recent named sessions
#   airesume -h                    show help
#
# Optional agent filter (must be the FIRST argument):
#   -c / --claude   restrict to Claude Code sessions
#   -g / --gemini   restrict to Gemini sessions
#   -x / --codex    restrict to Codex sessions
# With no filter, all three registries are scanned.
#
# Behaviour for `airesume <prefix>`:
#   · 1 match in the current folder        -> resume it
#   · several in the current folder        -> arrow-key picker
#   · none here, 1 / exact match elsewhere -> resume it (with a notice)
#   · none here, several elsewhere         -> picker, showing each folder
# Resuming cd's your shell into the session's folder, then launches the
# matching AI: claude via the auto-resume wrapper, codex / gemini via your
# aliases.
#
# Source this AFTER your claude / codex / gemini aliases so the picked session
# launches with your normal per-agent flags. Add to ~/.zsh_alias:
#   source "$HOME/.claude/skills/resume-session/shell/airesume.sh"

airesume() {
  local registry="$HOME/.claude/skills/resume-session/scripts/session_registry.py"
  local claude_wrapper="$HOME/.claude/auto-resume/claude-auto-resume.sh"
  local agent_filter=""

  if [ ! -f "$registry" ]; then
    echo "airesume: registry script not found at $registry" >&2
    return 1
  fi

  # Optional leading single-agent filter.
  case "${1:-}" in
  -c | --claude)
    agent_filter="claude"
    shift
    ;;
  -g | --gemini)
    agent_filter="gemini"
    shift
    ;;
  -x | --codex)
    agent_filter="codex"
    shift
    ;;
  esac

  case "${1:-}" in
  "")
    local agent
    for agent in claude codex gemini; do
      [ -n "$agent_filter" ] && [ "$agent" != "$agent_filter" ] && continue
      printf '\n\033[1m== %s ==\033[0m\n' "$agent"
      python3 "$registry" --agent "$agent" list --alive --limit 15
    done
    return 0
    ;;
  -h | --help)
    cat <<'USAGE'
airesume — resume a named AI session by name-prefix.

  airesume [-c|-g|-x] <prefix>   scan the session registries for sessions
                                 whose name starts with <prefix>, then:
                                   · 1 match in this folder       -> resume it
                                   · several in this folder       -> picker
                                   · none here, 1/exact elsewhere -> resume it
                                   · none here, several elsewhere -> picker
  airesume [-c|-g|-x]            list recent named sessions
  airesume -h                    show this help

Agent filter (optional, first argument only):
  -c / --claude   restrict to Claude Code sessions
  -g / --gemini   restrict to Gemini sessions
  -x / --codex    restrict to Codex sessions
With no filter, claude + codex + gemini are all scanned.

Resuming cd's your shell into the session's folder and launches the matching
AI (claude via the auto-resume wrapper; codex / gemini via your aliases).
USAGE
    return 0
    ;;
  esac

  local out rc
  if [ -n "$agent_filter" ]; then
    out="$(python3 "$registry" --agent "$agent_filter" prefix-resume "$1" --cwd "$PWD")"
  else
    out="$(python3 "$registry" prefix-resume "$1" --cwd "$PWD")"
  fi
  rc=$?
  [ "$rc" -ne 0 ] && return "$rc"

  local agent target_cwd token
  agent="${out%%$'\t'*}"
  out="${out#*$'\t'}"
  target_cwd="${out%%$'\t'*}"
  token="${out#*$'\t'}"

  if [ -z "$agent" ] || [ -z "$target_cwd" ] || [ -z "$token" ]; then
    echo "airesume: internal error — malformed resolver output" >&2
    return 1
  fi

  cd "$target_cwd" || {
    echo "airesume: cannot cd to $target_cwd" >&2
    return 1
  }

  case "$agent" in
  claude)
    if [ -x "$claude_wrapper" ]; then
      "$claude_wrapper" --resume "$token"
    else
      command claude --resume "$token"
    fi
    ;;
  codex)
    codex resume "$token"
    ;;
  gemini)
    gemini --resume "$token"
    ;;
  *)
    echo "airesume: unknown agent '$agent'" >&2
    return 1
    ;;
  esac
}
