#!/usr/bin/env bash
# Uninstall the three session skills from ~/.claude.
#
# - Removes symlinks at ~/.claude/skills/{conversation-summary,load-context,resume-session}
#   and ~/.claude/commands/{register.md,rn.md} if they still point at this repo.
# - Removes the SessionEnd, SessionStart, and UserPromptSubmit hook entries
#   added by this repo's install.sh from ~/.claude/settings.json.
#
# Leaves any .context-handoff.json files in working directories alone,
# leaves backups (.bak.*) intact, and leaves the session-name registry at
# ~/.claude/session-names/ alone.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="${HOME}/.claude"
SKILLS_DIR="${CLAUDE_DIR}/skills"
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

echo "Removing symlinks..."
remove_link_if_ours "${SKILLS_DIR}/conversation-summary" "${REPO}/conversation-summary"
remove_link_if_ours "${SKILLS_DIR}/load-context"         "${REPO}/load-context"
remove_link_if_ours "${SKILLS_DIR}/resume-session"       "${REPO}/resume-session"
remove_link_if_ours "${CLAUDE_DIR}/commands/register.md" "${REPO}/resume-session/commands/register.md"
remove_link_if_ours "${CLAUDE_DIR}/commands/rn.md"       "${REPO}/resume-session/commands/rn.md"

if [[ -f "${SETTINGS}" ]]; then
  echo "Removing hooks from ${SETTINGS}..."
  python3 - "${SETTINGS}" <<'PY'
import json
import sys
from pathlib import Path

settings_path = Path(sys.argv[1])
data = json.loads(settings_path.read_text())
hooks = data.get("hooks", {})

REMOVALS = [
    ("SessionEnd",       "conversation-summary/scripts/write_summary.sh"),
    ("SessionEnd",       "resume-session/scripts/session_end_hook.py"),
    ("UserPromptSubmit", "resume-session/scripts/rename_hook.py"),
    ("SessionStart",     "load-context/scripts/load_context_hook.sh"),
]

removed = 0
for event, marker in REMOVALS:
    arr = hooks.get(event)
    if not arr:
        continue
    new_arr = []
    for group in arr:
        kept = [
            h for h in group.get("hooks", [])
            if marker not in h.get("command", "")
        ]
        diff = len(group.get("hooks", [])) - len(kept)
        removed += diff
        if kept:
            new_group = dict(group)
            new_group["hooks"] = kept
            new_arr.append(new_group)
    if new_arr:
        hooks[event] = new_arr
    else:
        hooks.pop(event, None)

if not hooks:
    data.pop("hooks", None)

settings_path.write_text(json.dumps(data, indent=2) + "\n")
print(f"  removed {removed} hook entr{'y' if removed == 1 else 'ies'}")
PY
fi

echo
echo "Done. Existing .context-handoff.json files in working directories were left alone."
