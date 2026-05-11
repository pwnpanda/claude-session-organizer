#!/usr/bin/env python3
"""UserPromptSubmit hook: capture `/rename <name>` and register the session.

Hooks input JSON from stdin (fields: session_id, cwd, prompt, ...).
On match, invoke session_registry.py register.
Always exits 0 so the prompt is never blocked.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

RENAME_RE = re.compile(r"^\s*/rename\s+(\S.*?)\s*$")
REGISTRY = Path(__file__).resolve().parent / "session_registry.py"


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    prompt = data.get("prompt", "") or ""
    match = RENAME_RE.match(prompt)
    if not match:
        return 0

    name = match.group(1).strip().strip('"').strip("'")
    session_id = data.get("session_id", "")
    cwd = data.get("cwd", "")
    if not name or not session_id or not cwd:
        return 0

    subprocess.run(
        [
            sys.executable,
            str(REGISTRY),
            "register",
            name,
            "--session-id", session_id,
            "--cwd", cwd,
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
