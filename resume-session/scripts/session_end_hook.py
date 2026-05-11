#!/usr/bin/env python3
"""SessionEnd hook: keep the named-session registry in sync on quit.

Behavior:
  1. If the session is already registered, refresh last_updated + summary.
  2. Otherwise, auto-register it under a name derived from the first user
     prompt (falling back to the short session id if no usable prompt).

Always exits 0 so shutdown is never interrupted.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REGISTRY = Path(__file__).resolve().parent / "session_registry.py"
MAX_SUMMARY_LEN = 200
SLUG_MAX_WORDS = 6
SLUG_MAX_LEN = 40

# Prompts that are about *managing* sessions rather than the session's actual
# work. Skipping them yields better auto-names: a session that started with
# "help me find my conversation about proxmox" gets named after its second,
# substantive prompt instead of `help-me-find-my-conversation-about`.
META_PROMPT_PATTERNS = [
    re.compile(r"^\s*help me (find|resume|continue|restore|reopen|recover)\b", re.I),
    re.compile(r"^\s*(resume|continue|reopen|restore|pick up|go back to)\b", re.I),
    re.compile(r"^\s*(list|show)\s+(my|all|the)\s+(sessions|chats|conversations)\b", re.I),
    re.compile(r"^\s*what (sessions|chats|conversations)\b", re.I),
    re.compile(r"^\s*(save|name|rename)\s+(this|the)\b", re.I),
    re.compile(r"^\s*(forget|delete|remove)\s+(the|my)?\s*session\b", re.I),
]


def _is_meta_prompt(text: str) -> bool:
    return any(p.search(text) for p in META_PROMPT_PATTERNS)


def extract_first_user_text(transcript_path: str) -> str | None:
    """Return the first substantive user prompt from the transcript.

    "Substantive" = not empty after stripping system-reminder/command noise,
    and not a meta-prompt about session management itself. Falls back to the
    first non-empty cleaned prompt if every prompt looks meta (better than
    `None`, which would cause us to name the entry `session-<shortid>`).
    """
    path = Path(transcript_path)
    if not path.exists():
        return None
    fallback: str | None = None
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") != "user":
                    continue
                msg = entry.get("message", {})
                text = _content_to_text(msg.get("content"))
                if not text:
                    continue
                cleaned = _strip_noise(text).strip()
                if not cleaned:
                    continue
                if fallback is None:
                    fallback = cleaned[:MAX_SUMMARY_LEN]
                if not _is_meta_prompt(cleaned):
                    return cleaned[:MAX_SUMMARY_LEN]
    except OSError:
        return None
    return fallback


def _content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if isinstance(text, str):
                    return text
    return ""


def _strip_noise(text: str) -> str:
    """Drop system-reminder blocks, command tags, and leading slash commands."""
    text = re.sub(r"<system-reminder>.*?</system-reminder>", " ", text, flags=re.DOTALL)
    text = re.sub(r"<command-[a-z-]+>[^<]*</command-[a-z-]+>", " ", text)
    text = re.sub(r"<local-command-stdout>.*?</local-command-stdout>", " ", text, flags=re.DOTALL)
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("/")]
    return "\n".join(lines)


def slugify(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9\s-]", " ", text)
    words = [w for w in text.split() if w][:SLUG_MAX_WORDS]
    slug = "-".join(w.lower() for w in words)
    return slug[:SLUG_MAX_LEN].strip("-")


def load_registry_names() -> set[str]:
    index_path = Path.home() / ".claude" / "session-names" / "index.json"
    if not index_path.exists():
        return set()
    try:
        return set(json.loads(index_path.read_text()).keys())
    except (OSError, json.JSONDecodeError):
        return set()


def pick_unique_name(base: str, session_id: str) -> str:
    existing = load_registry_names()
    if base and base not in existing:
        return base
    suffix = session_id[:6] if session_id else "x"
    candidate = f"{base}-{suffix}" if base else f"session-{suffix}"
    return candidate if candidate not in existing else f"{candidate}-{session_id[6:10]}"


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    session_id = data.get("session_id", "")
    if not session_id:
        return 0

    transcript_path = data.get("transcript_path", "") or ""
    cwd = data.get("cwd", "") or ""
    summary = extract_first_user_text(transcript_path) if transcript_path else None

    touch_cmd = [sys.executable, str(REGISTRY), "touch", "--session-id", session_id]
    if summary:
        touch_cmd += ["--summary", summary]
    touched = subprocess.run(
        touch_cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    if touched.returncode == 0:
        return 0

    # Opt-out for subprocess/subagent Claude runs (e.g. SAST pipeline iteration
    # workers). The orchestrator sets CLAUDE_NO_AUTO_REGISTER=1; the existing-
    # entry touch above still runs, so manually-named sessions stay fresh, but
    # short-lived workers don't pollute the registry with auto entries.
    if os.environ.get("CLAUDE_NO_AUTO_REGISTER", "").strip().lower() not in ("", "0", "false", "no"):
        return 0

    if not cwd:
        return 0

    base = slugify(summary) if summary else ""
    name = pick_unique_name(base, session_id)
    register_cmd = [
        sys.executable, str(REGISTRY), "register", name,
        "--session-id", session_id,
        "--cwd", cwd,
        "--auto",
    ]
    if summary:
        register_cmd += ["--summary", summary]
    subprocess.run(
        register_cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
