#!/usr/bin/env bash
# Install the three session skills into ~/.claude.
#
# - Symlinks ~/.claude/skills/conversation-summary -> <repo>/conversation-summary
# - Symlinks ~/.claude/skills/load-context         -> <repo>/load-context
# - Symlinks ~/.claude/skills/resume-session       -> <repo>/resume-session
# - Symlinks ~/.claude/commands/save.md            -> <repo>/resume-session/commands/save.md
# - Symlinks ~/.claude/commands/rn.md              -> <repo>/resume-session/commands/rn.md
# - Idempotently merges hooks into ~/.claude/settings.json:
#     SessionEnd       -> conversation-summary/scripts/write_summary.sh
#     SessionEnd       -> resume-session/scripts/session_end_hook.py
#     UserPromptSubmit -> resume-session/scripts/rename_hook.py
# - Cleans up any legacy SessionStart hook left over from earlier
#   versions that auto-loaded the handoff. Loading is now manual-only
#   via the /load-context skill.
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

mkdir -p "${SKILLS_DIR}" "${CLAUDE_DIR}/commands"

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
link_target "${SKILLS_DIR}/resume-session"       "${REPO}/resume-session"
link_target "${CLAUDE_DIR}/commands/save.md"     "${REPO}/resume-session/commands/save.md"
link_target "${CLAUDE_DIR}/commands/rn.md"       "${REPO}/resume-session/commands/rn.md"

echo "Merging hooks into ${SETTINGS}..."
python3 - "${SETTINGS}" <<'PY'
import json
import sys
from pathlib import Path

settings_path = Path(sys.argv[1])
data = json.loads(settings_path.read_text()) if settings_path.exists() else {}
hooks = data.setdefault("hooks", {})

# Hooks to ensure are present
ENSURE = [
    {
        "event": "SessionEnd",
        "marker": "conversation-summary/scripts/write_summary.sh",
        "command": "bash $HOME/.claude/skills/conversation-summary/scripts/write_summary.sh",
        "timeout": 10,
    },
    {
        "event": "SessionEnd",
        "marker": "resume-session/scripts/session_end_hook.py",
        "command": "python3 $HOME/.claude/skills/resume-session/scripts/session_end_hook.py",
    },
    {
        "event": "UserPromptSubmit",
        "marker": "resume-session/scripts/rename_hook.py",
        "command": "python3 $HOME/.claude/skills/resume-session/scripts/rename_hook.py",
    },
]

# Hooks to ensure are absent (cleanup of deprecated entries from older
# versions that auto-loaded the handoff on SessionStart).
PURGE = [
    ("SessionStart", "load-context/scripts/load_context_hook.sh"),
]

for spec in ENSURE:
    arr = hooks.setdefault(spec["event"], [])
    found = False
    for group in arr:
        for h in group.get("hooks", []):
            if spec["marker"] in h.get("command", ""):
                h["type"] = "command"
                h["command"] = spec["command"]
                if "timeout" in spec:
                    h["timeout"] = spec["timeout"]
                else:
                    h.pop("timeout", None)
                found = True
                break
        if found:
            break

    if found:
        print(f"  [{spec['event']}] hook already present (normalized)")
    else:
        hook_entry = {"type": "command", "command": spec["command"]}
        if "timeout" in spec:
            hook_entry["timeout"] = spec["timeout"]
        arr.append({"hooks": [hook_entry]})
        print(f"  [{spec['event']}] hook added")

purged = 0
for event, marker in PURGE:
    arr = hooks.get(event)
    if not arr:
        continue
    new_arr = []
    for group in arr:
        kept = [h for h in group.get("hooks", []) if marker not in h.get("command", "")]
        diff = len(group.get("hooks", [])) - len(kept)
        purged += diff
        if kept:
            new_group = dict(group)
            new_group["hooks"] = kept
            new_arr.append(new_group)
    if new_arr:
        hooks[event] = new_arr
    else:
        hooks.pop(event, None)

if purged:
    print(f"  purged {purged} legacy hook entr{'y' if purged == 1 else 'ies'}")

if not hooks:
    data.pop("hooks", None)

settings_path.parent.mkdir(parents=True, exist_ok=True)
settings_path.write_text(json.dumps(data, indent=2) + "\n")
PY

SOURCE_LINE="source \"\$HOME/.claude/skills/resume-session/shell/cc-resume.sh\""
echo
echo "Optional: install the cc-resume shell function for one-shot restore by name."
echo "  Add this line to your ~/.zshrc or ~/.bashrc:"
echo "    ${SOURCE_LINE}"
for rc in "${HOME}/.zshrc" "${HOME}/.bashrc"; do
  [[ -f "${rc}" ]] || continue
  if grep -Fq "skills/resume-session/shell/cc-resume.sh" "${rc}"; then
    echo "  ${rc}: already sources cc-resume.sh"
  fi
done

ALIAS_FILE="${HOME}/.zsh_alias"
echo
echo "Optional: the 'airesume' shell function resumes a named session"
echo "(claude/codex/gemini) by name prefix — restrict to one agent with"
echo "-c (claude), -g (gemini), or -x (codex). It is sourced from ~/.zsh_alias,"
echo "after your claude/codex/gemini aliases."
if grep -Fq "skills/resume-session/shell/airesume.sh" "${ALIAS_FILE}" 2>/dev/null; then
  echo "  ${ALIAS_FILE}: already sources airesume.sh"
elif [[ -f "${ALIAS_FILE}" ]]; then
  airesume_reply="y"
  if [[ -t 0 ]]; then
    read -r -p "  Append the airesume source line to ${ALIAS_FILE}? [Y/n] " airesume_reply || true
  fi
  if [[ "${airesume_reply}" =~ ^[Yy]?$ ]]; then
    cat >>"${ALIAS_FILE}" <<'AIRESUME_RC'

# airesume: resume a named AI session (claude/codex/gemini) by name-prefix.
# Sourced last so the claude/codex/gemini aliases above are baked into the
# function body (each picked session launches with your normal per-agent flags).
[ -f "$HOME/.claude/skills/resume-session/shell/airesume.sh" ] && \
  source "$HOME/.claude/skills/resume-session/shell/airesume.sh"
AIRESUME_RC
    echo "  appended airesume to ${ALIAS_FILE}"
  else
    echo "  skipped — add this to ${ALIAS_FILE} yourself:"
    echo "    source \"\$HOME/.claude/skills/resume-session/shell/airesume.sh\""
  fi
else
  echo "  ${ALIAS_FILE} not found — add this to your shell rc yourself:"
  echo "    source \"\$HOME/.claude/skills/resume-session/shell/airesume.sh\""
fi

echo
echo "Done. Restart Claude Code (or open /hooks once) so the watcher picks up the changes."
echo
echo "Round-trip:"
echo "  - SessionEnd writes .context-handoff.json (at git repo root if available, else cwd)"
echo "  - To load it in a new session, ask Claude to /load-context (manual only)"
echo "  - /save my-session registers the current session under a human-friendly name"
echo "  - /rn my-session does the same with a shorter alias"
