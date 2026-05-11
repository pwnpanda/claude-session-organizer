# shellcheck shell=bash
# cc-resume — restore a Claude Code session by its registered name.
#
# Usage:
#   cc-resume <name>          resume the named session
#   cc-resume                 list the 20 most recent live sessions
#   cc-resume -s <query>      search by topic/keyword
#
# Source this file from your shell rc (the install script can do it for you):
#   source "$HOME/.claude/skills/resume-session/shell/cc-resume.sh"

cc-resume() {
  local registry="$HOME/.claude/skills/resume-session/scripts/session_registry.py"
  if [ ! -f "$registry" ]; then
    echo "cc-resume: registry script not found at $registry" >&2
    return 1
  fi

  case "${1:-}" in
    "")
      python3 "$registry" list --alive --limit 20
      ;;
    -s|--search)
      shift
      if [ $# -eq 0 ]; then
        echo "cc-resume: -s requires a query" >&2
        return 2
      fi
      python3 "$registry" search "$@"
      ;;
    -h|--help)
      cat <<'USAGE'
cc-resume — restore a Claude Code session by name.

  cc-resume <name>           resume the named session (cd's and execs claude)
  cc-resume                  list the 20 most recent live sessions
  cc-resume -s <query>       search by topic/keyword
  cc-resume -h               show this help
USAGE
      ;;
    *)
      local cmd
      cmd="$(python3 "$registry" resume-cmd "$1")" || return $?
      eval "$cmd"
      ;;
  esac
}
