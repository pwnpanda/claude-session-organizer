# shellcheck shell=bash
# airesume — resume a named AI session (Claude Code / Codex / Gemini).
# Scans the session registries, finds the most likely target, and resumes
# it — picking interactively when several match.
#
# Usage:
#   airesume [-c|-g|-x] <prefix>        resolve a name-prefix and resume
#   airesume [-c|-g|-x] -s '<text>'     find a session by description and resume
#   airesume [-c|-g|-x]                 list recent named sessions
#   airesume -h                         show help
#
# Optional agent filter (must be the FIRST argument):
#   -c / --claude   restrict to Claude Code sessions
#   -g / --gemini   restrict to Gemini sessions
#   -x / --codex    restrict to Codex sessions
# With no filter, all three registries are scanned.
#
# Resolution (both prefix and -s search):
#   · exactly one match  -> resume it
#   · several matches    -> arrow-key picker
#   · no match           -> error, nothing resumed
# Prefix mode additionally prefers a match in the current folder.
#
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
airesume — resume a named AI session.

  airesume [-c|-g|-x] <prefix>      resume a session whose name starts with
                                    <prefix>
  airesume [-c|-g|-x] -s '<text>'   resume a session matched by a free-text
                                    description (keywords scored against
                                    name, summary, cwd, then transcript body)
  airesume [-c|-g|-x]               list recent named sessions
  airesume -h                       show this help

Resolution: one match resumes directly, several open an arrow-key picker,
none is an error. Prefix mode also prefers a match in the current folder.

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

  # Pick the resolver: -s switches from name-prefix to free-text search.
  local subcmd="prefix-resume"
  local -a query_args
  if [ "$1" = "-s" ] || [ "$1" = "--search" ]; then
    shift
    if [ -z "${1:-}" ]; then
      echo "airesume: -s requires a search query" >&2
      return 2
    fi
    subcmd="search-resume"
    query_args=("$1")
  else
    query_args=("$1" --cwd "$PWD")
  fi

  local -a agent_args=()
  [ -n "$agent_filter" ] && agent_args=(--agent "$agent_filter")

  local out rc
  out="$(python3 "$registry" "${agent_args[@]}" "$subcmd" "${query_args[@]}")"
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
