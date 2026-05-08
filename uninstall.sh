#!/usr/bin/env bash
# Uninstall summarize-context from ~/.claude.
#
# - Removes the symlink ~/.claude/skills/conversation-summary if it
#   still points at this repo.
# - Removes the SessionEnd hook entry from ~/.claude/settings.json.
#
# Leaves any summary.json files in working directories alone, and
# leaves backups (.bak.*) intact.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="${HOME}/.claude"
SKILL_LINK="${CLAUDE_DIR}/skills/conversation-summary"
SETTINGS="${CLAUDE_DIR}/settings.json"

remove_link_if_ours() {
  local link="$1" expected="$2"
  if [[ -L "${link}" ]]; then
    local current
    current="$(readlink "${link}")"
    if [[ "${current}" == "${expected}" ]]; then
      rm "${link}"
      echo "  removed: ${link}"
      return
    fi
    echo "  skipped: ${link} (points elsewhere: ${current})"
    return
  fi
  if [[ -e "${link}" ]]; then
    echo "  skipped: ${link} (not a symlink — leave it alone)"
    return
  fi
  echo "  not present: ${link}"
}

echo "Removing symlink..."
remove_link_if_ours "${SKILL_LINK}" "${REPO}"

if [[ -f "${SETTINGS}" ]]; then
  echo "Removing hook from ${SETTINGS}..."
  python3 - "${SETTINGS}" <<'PY'
import json
import sys
from pathlib import Path

settings_path = Path(sys.argv[1])
data = json.loads(settings_path.read_text())
hooks = data.get("hooks", {})

MARKER = "conversation-summary/scripts/write_summary.sh"
removed = 0

arr = hooks.get("SessionEnd")
if arr:
    new_arr = []
    for group in arr:
        kept = [
            h for h in group.get("hooks", [])
            if MARKER not in h.get("command", "")
        ]
        diff = len(group.get("hooks", [])) - len(kept)
        removed += diff
        if kept:
            new_group = dict(group)
            new_group["hooks"] = kept
            new_arr.append(new_group)
    if new_arr:
        hooks["SessionEnd"] = new_arr
    else:
        hooks.pop("SessionEnd", None)

if not hooks:
    data.pop("hooks", None)

settings_path.write_text(json.dumps(data, indent=2) + "\n")
print(f"  removed {removed} hook entr{'y' if removed == 1 else 'ies'}")
PY
fi

echo
echo "Done. Existing summary.json files in working directories were left alone."
