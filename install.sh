#!/usr/bin/env bash
# Install summarize-context into ~/.claude.
#
# - Symlinks ~/.claude/skills/conversation-summary -> <this repo>
#   so the repo is the single source of truth.
# - Idempotently merges a SessionEnd hook into ~/.claude/settings.json
#   that runs scripts/write_summary.sh on session close.
#
# Re-running this script is safe.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="${HOME}/.claude"
SKILL_LINK="${CLAUDE_DIR}/skills/conversation-summary"
SETTINGS="${CLAUDE_DIR}/settings.json"

for tool in jq python3 claude; do
  command -v "${tool}" >/dev/null 2>&1 || {
    echo "error: '${tool}' is required but not on PATH" >&2
    exit 1
  }
done

mkdir -p "${CLAUDE_DIR}/skills"

link_target() {
  local link="$1" target="$2"
  if [[ -L "${link}" ]]; then
    local current
    current="$(readlink "${link}")"
    if [[ "${current}" == "${target}" ]]; then
      echo "  already linked: ${link}"
      return
    fi
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

echo "Installing symlink..."
link_target "${SKILL_LINK}" "${REPO}"

echo "Merging hook into ${SETTINGS}..."
python3 - "${SETTINGS}" <<'PY'
import json
import sys
from pathlib import Path

settings_path = Path(sys.argv[1])
data = json.loads(settings_path.read_text()) if settings_path.exists() else {}
hooks = data.setdefault("hooks", {})

CMD = "bash $HOME/.claude/skills/conversation-summary/scripts/write_summary.sh"
TIMEOUT = 10
EVENT = "SessionEnd"
MARKER = "conversation-summary/scripts/write_summary.sh"

arr = hooks.setdefault(EVENT, [])
found = False
for group in arr:
    for h in group.get("hooks", []):
        if MARKER in h.get("command", ""):
            h["command"] = CMD
            h["type"] = "command"
            h["timeout"] = TIMEOUT
            found = True
            break
    if found:
        break

if found:
    print(f"  [{EVENT}] hook already present (normalized)")
else:
    arr.append({
        "hooks": [{
            "type": "command",
            "command": CMD,
            "timeout": TIMEOUT,
        }],
    })
    print(f"  [{EVENT}] hook added")

settings_path.parent.mkdir(parents=True, exist_ok=True)
settings_path.write_text(json.dumps(data, indent=2) + "\n")
PY

echo
echo "Done. Restart Claude Code (or open /hooks once) to pick up the hook."
echo "summary.json will be written to the session's CWD when the next session ends."
