#!/usr/bin/env bash
# Install summarize-context into ~/.claude.
#
# - Symlinks ~/.claude/skills/conversation-summary -> <repo>/conversation-summary
# - Symlinks ~/.claude/skills/load-context         -> <repo>/load-context
# - Idempotently merges two hooks into ~/.claude/settings.json:
#     SessionEnd   -> conversation-summary/scripts/write_summary.sh
#     SessionStart -> load-context/scripts/load_context_hook.sh
#
# Re-running this script is safe.
#
# DEBUG=true ./install.sh    # verbose logging

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="${HOME}/.claude"
SKILLS_DIR="${CLAUDE_DIR}/skills"
SETTINGS="${CLAUDE_DIR}/settings.json"

debug() {
  if [[ "${DEBUG:-}" == "true" ]]; then
    echo "[install] $*" >&2
  fi
}

for tool in jq python3 claude; do
  command -v "${tool}" >/dev/null 2>&1 || {
    echo "error: '${tool}' is required but not on PATH" >&2
    exit 1
  }
done

mkdir -p "${SKILLS_DIR}"

link_target() {
  local link="$1" target="$2"
  if [[ -L "${link}" ]]; then
    local current
    current="$(readlink "${link}")"
    if [[ "${current}" == "${target}" ]]; then
      echo "  already linked: ${link}"
      return
    fi
    debug "removing stale symlink ${link} -> ${current}"
    rm "${link}"
  elif [[ -e "${link}" ]]; then
    local bak
    bak="${link}.bak.$(date +%Y%m%d%H%M%S)"
    echo "  backing up existing ${link} -> ${bak}"
    mv "${link}" "${bak}"
  fi
  ln -s "${target}" "${link}"
  echo "  linked: ${link} -> ${target}"
}

echo "Installing symlinks..."
link_target "${SKILLS_DIR}/conversation-summary" "${REPO}/conversation-summary"
link_target "${SKILLS_DIR}/load-context"         "${REPO}/load-context"

echo "Merging hooks into ${SETTINGS}..."
python3 - "${SETTINGS}" <<'PY'
import json
import sys
from pathlib import Path

settings_path = Path(sys.argv[1])
data = json.loads(settings_path.read_text()) if settings_path.exists() else {}
hooks = data.setdefault("hooks", {})

ENTRIES = [
    {
        "event": "SessionEnd",
        "marker": "conversation-summary/scripts/write_summary.sh",
        "command": "bash $HOME/.claude/skills/conversation-summary/scripts/write_summary.sh",
        "timeout": 10,
    },
    {
        "event": "SessionStart",
        "marker": "load-context/scripts/load_context_hook.sh",
        "command": "bash $HOME/.claude/skills/load-context/scripts/load_context_hook.sh",
        "timeout": 10,
    },
]

for spec in ENTRIES:
    arr = hooks.setdefault(spec["event"], [])
    found = False
    for group in arr:
        for h in group.get("hooks", []):
            if spec["marker"] in h.get("command", ""):
                h["type"] = "command"
                h["command"] = spec["command"]
                h["timeout"] = spec["timeout"]
                found = True
                break
        if found:
            break

    if found:
        print(f"  [{spec['event']}] hook already present (normalized)")
    else:
        arr.append({
            "hooks": [{
                "type": "command",
                "command": spec["command"],
                "timeout": spec["timeout"],
            }],
        })
        print(f"  [{spec['event']}] hook added")

settings_path.parent.mkdir(parents=True, exist_ok=True)
settings_path.write_text(json.dumps(data, indent=2) + "\n")
PY

echo
echo "Done. Restart Claude Code (or open /hooks once) so the watcher picks up the changes."
echo
echo "Round-trip:"
echo "  - SessionEnd writes <cwd>/.context-handoff.json"
echo "  - SessionStart in the same dir auto-injects it on the next session"
