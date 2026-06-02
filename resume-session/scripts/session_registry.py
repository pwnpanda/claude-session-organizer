#!/usr/bin/env python3
"""Named-session registry for Claude Code, Codex, and Gemini.

Persists mappings from human-friendly names to agent session metadata.
Claude keeps the original registry path for backward compatibility. Codex
also updates its native thread title database so `codex resume <name>` works.

Storage:
  Claude: ~/.claude/session-names/index.json
  Codex:  ~/.codex/session-names/index.json
  Gemini: ~/.gemini/session-names/index.json
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

AGENTS = ("claude", "codex", "gemini")
CURRENT_AGENT = "claude"

REGISTRY_PATHS = {
    "claude": Path.home() / ".claude" / "session-names" / "index.json",
    "codex": Path.home() / ".codex" / "session-names" / "index.json",
    "gemini": Path.home() / ".gemini" / "session-names" / "index.json",
}
BACKUP_DIRS = {
    agent: path.parent / "backups"
    for agent, path in REGISTRY_PATHS.items()
}
CC_PROJECTS_DIR = Path.home() / ".claude" / "projects"
CODEX_STATE_DB = Path.home() / ".codex" / "state_5.sqlite"
CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"
GEMINI_TMP_DIR = Path.home() / ".gemini" / "tmp"
GEMINI_PROJECTS_PATH = Path.home() / ".gemini" / "projects.json"


def load_index() -> dict:
    path = REGISTRY_PATHS[CURRENT_AGENT]
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def save_index(index: dict) -> None:
    path = REGISTRY_PATHS[CURRENT_AGENT]
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".index.", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(index, f, indent=2, sort_keys=True)
        os.replace(tmp_path, path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _set_current_agent(agent: str) -> None:
    global CURRENT_AGENT
    if agent not in AGENTS:
        raise ValueError(f"unsupported agent: {agent}")
    CURRENT_AGENT = agent


def _encoded_cwd(cwd: str) -> str:
    """Encode a filesystem path the way Claude Code names its project dirs.

    Real layout: ~/.claude/projects/-home-robin-Hacking/<uuid>.jsonl — every
    `/` becomes `-` and the leading slash also becomes `-` (so absolute paths
    keep their leading dash). Relative paths shouldn't appear here, but if
    they do they pass through unchanged except for slash substitution.
    """
    return cwd.replace("/", "-")


def cmd_register(args: argparse.Namespace) -> int:
    index = load_index()

    # Drop any other name pointing at the same session id (rename case).
    for other_name, other in list(index.items()):
        if other_name != args.name and other.get("session_id") == args.session_id:
            del index[other_name]

    entry = index.get(args.name, {})
    entry.setdefault("created", now_iso())
    entry.update({
        "agent": CURRENT_AGENT,
        "session_id": args.session_id,
        "cwd": args.cwd,
        "last_updated": now_iso(),
    })
    if args.summary:
        entry["summary"] = args.summary
    if args.auto:
        entry["auto"] = True
    else:
        entry.pop("auto", None)
    index[args.name] = entry
    if CURRENT_AGENT == "codex":
        _set_codex_thread_title(args.session_id, args.name)
    save_index(index)
    print(f"Registered '{args.name}' -> {CURRENT_AGENT}:{args.session_id[:8]} ({args.cwd})")
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    index = load_index()
    entry = index.get(args.name)
    if not entry:
        print(f"No session named '{args.name}'", file=sys.stderr)
        return 1
    print(json.dumps(entry, indent=2))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    index = load_index()
    if not index:
        print("No named sessions yet.")
        return 0
    rows = sorted(
        index.items(),
        key=lambda kv: kv[1].get("last_updated", ""),
        reverse=True,
    )
    shown = 0
    for name, entry in rows:
        sid_full = entry.get("session_id") or ""
        sid = (sid_full or "?")[:8]
        cwd = entry.get("cwd", "?")
        updated = (entry.get("last_updated") or "?")[:19]
        tags = []
        if entry.get("keep"):
            tags.append("KEEP")
        if entry.get("auto"):
            tags.append("auto")
        gone = bool(sid_full) and _find_transcript(sid_full) is None
        if gone:
            tags.append("GONE")
        if args.alive and gone:
            continue
        suffix = f" [{','.join(tags)}]" if tags else ""
        print(f"{name:30s}  {sid}  {updated}  {cwd}{suffix}")
        shown += 1
        if args.limit and shown >= args.limit:
            break
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    """Substring search across name, summary, cwd, and optionally transcript bodies."""
    index = load_index()
    needle = args.query.lower()
    hits: list[tuple[str, dict]] = []

    content_hits: set[str] = set()
    if args.content:
        content_hits = _search_transcripts(args.query)

    for name, entry in index.items():
        haystack = " ".join(
            [
                name,
                entry.get("summary") or "",
                entry.get("cwd") or "",
            ]
        ).lower()
        sid_full = entry.get("session_id") or ""
        if needle in haystack or (sid_full and sid_full in content_hits):
            hits.append((name, entry))

    if not hits:
        scope = "name/summary/cwd or transcripts" if args.content else "name/summary/cwd"
        print(f"No matches for '{args.query}' in {scope}.")
        return 1
    hits.sort(key=lambda kv: kv[1].get("last_updated", ""), reverse=True)
    for name, entry in hits:
        sid_full = entry.get("session_id") or ""
        sid = (sid_full or "?")[:8]
        updated = (entry.get("last_updated") or "?")[:19]
        cwd = entry.get("cwd", "?")
        tags = []
        if sid_full and _find_transcript(sid_full) is None:
            tags.append("GONE")
        if sid_full and sid_full in content_hits:
            tags.append("content")
        suffix = f" [{','.join(tags)}]" if tags else ""
        summary = (entry.get("summary") or "").strip().replace("\n", " ")
        if len(summary) > 80:
            summary = summary[:77] + "..."
        print(f"{name:30s}  {sid}  {updated}  {cwd}{suffix}")
        if summary:
            print(f"    {summary}")
    return 0


def _search_transcripts(query: str) -> set[str]:
    """Return the set of session ids whose transcript files contain `query`.

    Uses `rg` when available (much faster on large transcript corpora), falls
    back to a pure-Python scan otherwise. Either way the scan is case-insensitive.
    """
    if CURRENT_AGENT != "claude":
        return set()

    import shutil
    import subprocess

    if not CC_PROJECTS_DIR.is_dir():
        return set()

    rg = shutil.which("rg")
    if rg:
        try:
            proc = subprocess.run(
                [
                    rg,
                    "--files-with-matches",
                    "-i",
                    "--glob",
                    "*.jsonl",
                    query,
                    str(CC_PROJECTS_DIR),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
            paths = proc.stdout.splitlines()
        except (OSError, subprocess.TimeoutExpired):
            paths = []
    else:
        needle = query.lower().encode("utf-8")
        paths = []
        for path in CC_PROJECTS_DIR.glob("*/*.jsonl"):
            try:
                with path.open("rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        if needle in chunk.lower():
                            paths.append(str(path))
                            break
            except OSError:
                continue

    return {Path(p).stem for p in paths}


def cmd_remove(args: argparse.Namespace) -> int:
    index = load_index()
    if args.name not in index:
        print(f"No session named '{args.name}'", file=sys.stderr)
        return 1
    if index[args.name].get("keep") and not args.force:
        print(
            f"'{args.name}' is marked MUSTKEEP. Use --force to remove anyway.",
            file=sys.stderr,
        )
        return 1
    del index[args.name]
    save_index(index)
    print(f"Removed '{args.name}'")
    return 0


def _find_transcript(session_id: str) -> Path | None:
    if CURRENT_AGENT == "claude":
        if not CC_PROJECTS_DIR.is_dir():
            return None
        matches = list(CC_PROJECTS_DIR.glob(f"*/{session_id}.jsonl"))
        return matches[0] if matches else None

    if CURRENT_AGENT == "codex":
        path = _codex_rollout_path(session_id)
        if path and path.is_file():
            return path
        matches = list(CODEX_SESSIONS_DIR.glob(f"**/*{session_id}.jsonl"))
        return matches[0] if matches else None

    if CURRENT_AGENT == "gemini":
        record = _find_gemini_session_record(session_id=session_id)
        if record:
            return record["path"]
        return None

    return None


def _resolve_resume_cwd(session_id: str, fallback: str) -> tuple[str, Path | None]:
    """Find the transcript and return the canonical cwd to resume from.

    Claude Code resolves `--resume <id>` against the current shell's cwd by
    looking under `~/.claude/projects/<encoded-cwd>/<id>.jsonl`. The encoded
    cwd is the directory that actually holds the transcript, which can drift
    from the value cached in the registry. Read the cwd from the transcript's
    first message so the printed `cd ...` line is always correct.
    """
    transcript = _find_transcript(session_id)
    if transcript is None:
        return fallback, None
    try:
        with transcript.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cwd = obj.get("cwd") or _codex_cwd_from_event(obj)
                if cwd:
                    return cwd, transcript
    except OSError:
        pass
    return fallback, transcript


def cmd_keep(args: argparse.Namespace) -> int:
    """Mark a session MUSTKEEP and snapshot its transcript to a durable backup."""
    import shutil

    index = load_index()
    entry = index.get(args.name)
    if not entry:
        print(f"No session named '{args.name}'", file=sys.stderr)
        return 1

    session_id = entry.get("session_id", "")
    transcript = _find_transcript(session_id) if session_id else None
    if not transcript:
        print(
            f"Warning: no transcript/log found for {CURRENT_AGENT} session {session_id[:8]}. "
            "Marking as keep, but no backup taken.",
            file=sys.stderr,
        )
        entry["keep"] = True
        index[args.name] = entry
        save_index(index)
        return 0

    backup_dir = BACKUP_DIRS[CURRENT_AGENT]
    backup_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".jsonl" if CURRENT_AGENT != "gemini" else ".json"
    dest = backup_dir / f"{args.name}-{session_id[:8]}{suffix}"
    shutil.copy2(transcript, dest)
    entry["keep"] = True
    entry["backup_path"] = str(dest)
    entry["backed_up_at"] = now_iso()
    index[args.name] = entry
    save_index(index)
    print(f"Kept '{args.name}'. Backup: {dest}")
    return 0


def cmd_unkeep(args: argparse.Namespace) -> int:
    """Remove the MUSTKEEP marker. Leaves the backup file on disk."""
    index = load_index()
    entry = index.get(args.name)
    if not entry:
        print(f"No session named '{args.name}'", file=sys.stderr)
        return 1
    entry.pop("keep", None)
    index[args.name] = entry
    save_index(index)
    backup = entry.get("backup_path")
    msg = f"Unkept '{args.name}'."
    if backup:
        msg += f" Backup file kept at {backup} (delete manually if unwanted)."
    print(msg)
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    """Delete stale auto-registered entries.

    Default: remove entries marked `auto: true` whose `last_updated` is older
    than `--days` (default 60). Manually-named entries are never touched unless
    the user opts in via `--include-manual` (not currently exposed).
    `--all-auto` purges every auto entry regardless of age.
    """
    from datetime import timedelta

    index = load_index()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=args.days)
    victims: list[tuple[str, dict]] = []

    for name, entry in index.items():
        if entry.get("keep"):
            continue
        if args.orphans:
            sid = entry.get("session_id") or ""
            if sid and _find_transcript(sid) is None:
                victims.append((name, entry))
            continue
        if not entry.get("auto"):
            continue
        if args.all_auto:
            victims.append((name, entry))
            continue
        last = entry.get("last_updated") or entry.get("created")
        if not last:
            continue
        try:
            last_dt = datetime.fromisoformat(last)
        except ValueError:
            continue
        if last_dt < cutoff:
            victims.append((name, entry))

    if not victims:
        print("Nothing to prune.")
        return 0

    for name, entry in victims:
        sid = (entry.get("session_id") or "?")[:8]
        updated = (entry.get("last_updated") or "?")[:10]
        prefix = "[dry-run] " if args.dry_run else ""
        print(f"{prefix}remove {name:30s}  {sid}  {updated}")

    if args.dry_run:
        print(f"\n{len(victims)} entries would be removed (dry run).")
        return 0

    for name, _ in victims:
        del index[name]
    save_index(index)
    print(f"\nRemoved {len(victims)} auto-registered entries.")
    return 0


def cmd_touch(args: argparse.Namespace) -> int:
    """Update last_updated (and optionally summary) for a session id."""
    index = load_index()
    changed = False
    for entry in index.values():
        if entry.get("session_id") == args.session_id:
            entry["last_updated"] = now_iso()
            if args.summary:
                entry["summary"] = args.summary
            changed = True
    if changed:
        save_index(index)
    return 0 if changed else 1


CC_SESSIONS_DIR = Path.home() / ".claude" / "sessions"


def _find_current_session_id(cwd: str) -> str | None:
    """Look up the active Claude Code session for a given cwd.

    Claude Code writes a PID-keyed JSON file per running session at
    ~/.claude/sessions/<pid>.json containing {sessionId, cwd, startedAt, ...}.
    Pick the most recently started one whose cwd matches.
    """
    if not CC_SESSIONS_DIR.is_dir():
        return None
    target = str(Path(cwd).resolve())
    best: tuple[int, str] | None = None
    for path in CC_SESSIONS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if str(Path(data.get("cwd", "")).resolve()) != target:
            continue
        sid = data.get("sessionId")
        started = int(data.get("startedAt", 0))
        if not sid:
            continue
        if best is None or started > best[0]:
            best = (started, sid)
    return best[1] if best else None


def _find_current_codex_thread_id(cwd: str) -> str | None:
    env_thread_id = os.environ.get("CODEX_THREAD_ID")
    if env_thread_id:
        return env_thread_id
    if not CODEX_STATE_DB.is_file():
        return None
    target = str(Path(cwd).resolve())
    try:
        with sqlite3.connect(CODEX_STATE_DB, timeout=5) as conn:
            row = conn.execute(
                """
                SELECT id
                FROM threads
                WHERE cwd = ? AND archived = 0
                ORDER BY updated_at_ms DESC, updated_at DESC
                LIMIT 1
                """,
                (target,),
            ).fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def _codex_rollout_path(session_id: str) -> Path | None:
    if not CODEX_STATE_DB.is_file():
        return None
    try:
        with sqlite3.connect(CODEX_STATE_DB, timeout=5) as conn:
            row = conn.execute(
                "SELECT rollout_path FROM threads WHERE id = ?",
                (session_id,),
            ).fetchone()
    except sqlite3.Error:
        return None
    if not row or not row[0]:
        return None
    return Path(row[0]).expanduser()


def _codex_cwd_from_event(event: dict) -> str | None:
    payload = event.get("payload")
    if isinstance(payload, dict):
        cwd = payload.get("cwd")
        if isinstance(cwd, str):
            return cwd
    return None


def _set_codex_thread_title(session_id: str, title: str) -> None:
    if not CODEX_STATE_DB.is_file():
        return
    now = int(datetime.now(timezone.utc).timestamp())
    try:
        with sqlite3.connect(CODEX_STATE_DB, timeout=5) as conn:
            conn.execute(
                """
                UPDATE threads
                SET title = ?, updated_at = ?, updated_at_ms = ?
                WHERE id = ?
                """,
                (title, now, now * 1000, session_id),
            )
    except sqlite3.Error as exc:
        print(f"Warning: could not update Codex thread title: {exc}", file=sys.stderr)


def _gemini_project_alias(cwd: str) -> str | None:
    if not GEMINI_PROJECTS_PATH.is_file():
        return None
    try:
        data = json.loads(GEMINI_PROJECTS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    projects = data.get("projects", {})
    if not isinstance(projects, dict):
        return None
    target = str(Path(cwd).resolve())
    for project_cwd, alias in projects.items():
        if str(Path(project_cwd).expanduser().resolve()) == target:
            return str(alias)
    return None


def _parse_gemini_timestamp(value: str | None) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromtimestamp(0, timezone.utc)


def _gemini_log_records() -> list[dict]:
    if not GEMINI_TMP_DIR.is_dir():
        return []
    records: list[dict] = []

    for path in GEMINI_TMP_DIR.glob("*/logs.json"):
        try:
            events = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(events, list):
            continue
        alias = path.parent.name
        for event in events:
            if not isinstance(event, dict):
                continue
            session_id = event.get("sessionId")
            if not session_id:
                continue
            records.append({
                "session_id": str(session_id),
                "alias": alias,
                "path": path,
                "timestamp": _parse_gemini_timestamp(event.get("timestamp")),
            })

    for path in GEMINI_TMP_DIR.glob("*/chats/session-*.jsonl"):
        alias = path.parent.parent.name
        session_id: str | None = None
        timestamp = datetime.fromtimestamp(0, timezone.utc)
        try:
            with path.open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if session_id is None and isinstance(event.get("sessionId"), str):
                        session_id = event["sessionId"]
                    if isinstance(event.get("timestamp"), str):
                        timestamp = max(timestamp, _parse_gemini_timestamp(event["timestamp"]))
                    set_event = event.get("$set")
                    has_last_updated = (
                        isinstance(set_event, dict)
                        and isinstance(set_event.get("lastUpdated"), str)
                    )
                    if has_last_updated:
                        timestamp = max(
                            timestamp,
                            _parse_gemini_timestamp(set_event["lastUpdated"]),
                        )
                    if isinstance(event.get("lastUpdated"), str):
                        timestamp = max(timestamp, _parse_gemini_timestamp(event["lastUpdated"]))
        except OSError:
            continue
        if session_id:
            records.append({
                "session_id": session_id,
                "alias": alias,
                "path": path,
                "timestamp": timestamp,
            })
    return records


def _find_gemini_session_record(
    cwd: str | None = None,
    session_id: str | None = None,
) -> dict | None:
    alias = _gemini_project_alias(cwd) if cwd else None
    best: dict | None = None
    for record in _gemini_log_records():
        if alias and record["alias"] != alias:
            continue
        if session_id and record["session_id"] != session_id:
            continue
        if best is None or record["timestamp"] > best["timestamp"]:
            best = record
    return best


def _find_current_gemini_session_id(cwd: str) -> str | None:
    record = _find_gemini_session_record(cwd=cwd)
    if not record:
        return None
    return record["session_id"]


def _gemini_resume_index(session_id: str, cwd: str) -> int | None:
    alias = _gemini_project_alias(cwd)
    if not alias:
        return None
    latest_by_session: dict[str, datetime] = {}
    for record in _gemini_log_records():
        if record["alias"] != alias:
            continue
        sid = record["session_id"]
        timestamp = record["timestamp"]
        if sid not in latest_by_session or timestamp > latest_by_session[sid]:
            latest_by_session[sid] = timestamp

    ordered = sorted(
        latest_by_session.items(),
        key=lambda item: item[1],
        reverse=True,
    )
    for index, (sid, _) in enumerate(ordered, start=1):
        if sid == session_id:
            return index
    return None


def cmd_register_current(args: argparse.Namespace) -> int:
    """Register the current session (looked up by cwd) under a name."""
    if CURRENT_AGENT == "codex":
        sid = _find_current_codex_thread_id(args.cwd)
        if not sid:
            print(
                f"Could not find an active Codex thread for cwd {args.cwd}",
                file=sys.stderr,
            )
            return 1
        _set_codex_thread_title(sid, args.name)
        args.session_id = sid
        return cmd_register(args)

    if CURRENT_AGENT == "gemini":
        sid = _find_current_gemini_session_id(args.cwd)
        if not sid:
            print(
                f"Could not find a Gemini session for cwd {args.cwd}",
                file=sys.stderr,
            )
            return 1
        args.session_id = sid
        return cmd_register(args)

    sid = _find_current_session_id(args.cwd)
    if not sid:
        print(
            f"Could not find an active Claude Code session for cwd {args.cwd}",
            file=sys.stderr,
        )
        return 1
    args.session_id = sid
    return cmd_register(args)


def cmd_resume_cmd(args: argparse.Namespace) -> int:
    """Print the shell command needed to resume the named session.

    Resolves cwd from the transcript file rather than the index entry, so a
    drifted index (cwd changed, project dir renamed) still yields a working
    command. Errors out if the transcript is missing — `claude --resume` would
    fail anyway, and printing a doomed command is worse than refusing.
    """
    index = load_index()
    entry = index.get(args.name)
    if not entry:
        print(f"No session named '{args.name}'", file=sys.stderr)
        return 1
    sid = entry["session_id"]
    cwd, transcript = _resolve_resume_cwd(sid, entry.get("cwd", ""))
    if transcript is None:
        print(
            f"Transcript/log for {CURRENT_AGENT} session {sid[:8]} is missing. "
            f"Cannot resume. Use `prune --orphans` to clean up the entry.",
            file=sys.stderr,
        )
        return 1
    if CURRENT_AGENT == "codex":
        print(f"cd {cwd!s} && codex resume {args.name}")
    elif CURRENT_AGENT == "gemini":
        resume_index = _gemini_resume_index(sid, cwd)
        if resume_index is None:
            print(
                f"Could not resolve Gemini resume index for session {sid[:8]}.",
                file=sys.stderr,
            )
            return 1
        print(f"cd {cwd!s} && gemini --resume {resume_index}")
    else:
        print(f"cd {cwd!s} && claude --resume {sid}")
    return 0


def _resume_token(agent: str, name: str, session_id: str, cwd: str) -> str | None:
    """Return the agent-native identifier to hand to its resume command.

    Codex resumes by registered name, Gemini by a per-project resume index,
    Claude by session UUID. Returns None when the identifier can't be resolved.
    """
    if agent == "codex":
        return name
    if agent == "gemini":
        index = _gemini_resume_index(session_id, cwd)
        return str(index) if index is not None else None
    return session_id


def _candidate(agent: str, name: str, session_id: str, entry: dict, cwd: str) -> dict:
    """Build the candidate dict shared by the prefix and search resolvers."""
    return {
        "agent": agent,
        "name": name,
        "session_id": session_id,
        "cwd": cwd,
        "updated": (entry.get("last_updated") or "")[:19],
    }


def _emit_resume(chosen: dict) -> int:
    """Print the resolver's machine-readable result line for `chosen`.

    Writes `<agent>\\t<cwd>\\t<token>` to stdout and returns 0, or an error to
    stderr and returns 2 when the agent-native resume token can't be resolved.
    """
    token = _resume_token(
        chosen["agent"], chosen["name"], chosen["session_id"], chosen["cwd"]
    )
    if token is None:
        print(
            f'airesume: could not resolve a resume target for {chosen["agent"]} '
            f'session "{chosen["name"]}".',
            file=sys.stderr,
        )
        return 2
    print(f"{chosen['agent']}\t{chosen['cwd']}\t{token}")
    return 0


def _all_candidates(prefix: str, agents: tuple[str, ...]) -> list[dict]:
    """Resumable sessions across `agents` whose name starts with `prefix`.

    Scans each given agent's registry. Each result carries the cwd resolved
    from the transcript itself, so a drifted index still yields a working
    resume target. Sessions whose transcript is gone are dropped — they cannot
    be resumed. Sorted most-recently-updated first.
    """
    needle = prefix.lower()
    found: list[dict] = []
    for agent in agents:
        _set_current_agent(agent)
        for name, entry in load_index().items():
            if not name.lower().startswith(needle):
                continue
            session_id = entry.get("session_id") or ""
            if not session_id:
                continue
            cwd, transcript = _resolve_resume_cwd(session_id, entry.get("cwd", ""))
            if transcript is None:
                continue
            found.append(_candidate(agent, name, session_id, entry, cwd))
    found.sort(key=lambda s: s["updated"], reverse=True)
    return found


def _pick_session(sessions: list[dict], header: str, show_cwd: bool) -> dict | None:
    """Arrow-key picker over `sessions`; returns the chosen entry or None.

    Renders to and reads keys from /dev/tty directly so the caller's stdout
    (which carries the machine-readable result) stays clean. Returns None when
    the user cancels or when no controlling terminal is available.
    """
    import select as _select
    import shutil
    import termios
    import tty as _tty

    try:
        tty_in = open("/dev/tty", "rb", buffering=0)
        tty_out = open("/dev/tty", "w")
    except OSError:
        print(
            "airesume: several matches but no terminal to pick from — "
            "narrow the prefix.",
            file=sys.stderr,
        )
        return None

    n = len(sessions)
    selected = 0
    name_w = min(max((len(s["name"]) for s in sessions), default=4), 44)

    def render(first: bool) -> None:
        cols = shutil.get_terminal_size((100, 24)).columns
        lines = [f"\033[1m{header}\033[0m"]
        for i, s in enumerate(sessions):
            cursor = "›" if i == selected else " "
            row = f"{cursor} {s['agent']:6s}  {s['name']:{name_w}s}  {s['updated']}"
            if show_cwd:
                row += f"  {s['cwd']}"
            row = row[: cols - 1]
            lines.append(f"\033[7m{row}\033[0m" if i == selected else row)
        lines.append(
            "\033[2m  ↑/↓ or j/k move · Enter select "
            "· q/Esc cancel\033[0m"
        )
        if not first:
            tty_out.write(f"\033[{len(lines)}A")
        for line in lines:
            tty_out.write("\r\033[K" + line + "\n")
        tty_out.flush()

    fd = tty_in.fileno()
    saved = termios.tcgetattr(fd)
    result: dict | None = None
    try:
        _tty.setraw(fd)
        render(first=True)
        while True:
            ch = tty_in.read(1)
            if not ch or ch in (b"q", b"\x03"):
                break
            if ch in (b"\r", b"\n"):
                result = sessions[selected]
                break
            if ch == b"\x1b":
                ready, _, _ = _select.select([fd], [], [], 0.05)
                if not ready:
                    break
                seq = tty_in.read(2)
                if seq == b"[A":
                    selected = (selected - 1) % n
                elif seq == b"[B":
                    selected = (selected + 1) % n
                else:
                    continue
            elif ch == b"k":
                selected = (selected - 1) % n
            elif ch == b"j":
                selected = (selected + 1) % n
            else:
                continue
            render(first=False)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        tty_out.write("\n")
        tty_out.flush()
        tty_in.close()
        tty_out.close()
    return result


def cmd_prefix_resume(args: argparse.Namespace) -> int:
    """Resolve a name-prefix to one resumable session.

    Scans every agent's registry, or only one when the global `--agent` flag
    names a specific agent. A sole match in the calling shell's cwd wins
    outright; several in-cwd matches open the picker. With nothing in-cwd, a
    unique exact-name (or sole overall) match elsewhere wins, otherwise the
    picker runs across folders. Prints `<agent>\\t<cwd>\\t<token>` on success.
    """
    shell_cwd = os.path.normpath(os.path.realpath(args.cwd or os.getcwd()))
    agents = AGENTS if args.agent == "auto" else (args.agent,)
    candidates = _all_candidates(args.prefix, agents)
    if not candidates:
        print(
            f"airesume: no resumable session matching '{args.prefix}' "
            f"in {'/'.join(agents)}.",
            file=sys.stderr,
        )
        return 2

    def in_cwd(session: dict) -> bool:
        return os.path.normpath(os.path.realpath(session["cwd"])) == shell_cwd

    here = [s for s in candidates if in_cwd(s)]
    elsewhere = [s for s in candidates if not in_cwd(s)]

    if len(here) == 1:
        chosen = here[0]
    elif len(here) > 1:
        chosen = _pick_session(
            here, f"{len(here)} sessions match '{args.prefix}' in this folder:", False
        )
    else:
        exact = [s for s in elsewhere if s["name"].lower() == args.prefix.lower()]
        if len(exact) == 1:
            chosen = exact[0]
        elif len(elsewhere) == 1:
            chosen = elsewhere[0]
        else:
            chosen = _pick_session(
                elsewhere,
                f"{len(elsewhere)} sessions match '{args.prefix}' in other folders:",
                True,
            )

    if chosen is None:
        print("airesume: cancelled.", file=sys.stderr)
        return 3

    where = "this folder" if in_cwd(chosen) else chosen["cwd"]
    print(
        f'airesume → {chosen["agent"]} "{chosen["name"]}" [{where}]',
        file=sys.stderr,
    )
    return _emit_resume(chosen)


_SEARCH_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "at",
    "for", "with", "where", "when", "was", "were", "is", "are", "we", "it",
    "that", "this", "these", "those", "about", "discuss", "discussed",
    "discussing", "conversation", "conversations", "chat", "session",
    "sessions", "talk", "talking", "regarding", "from", "into", "our",
    "you", "your", "had", "have", "been", "did", "do",
})

_SEARCH_PICK_LIMIT = 25


def _query_terms(query: str) -> list[str]:
    """Reduce a free-text query to its meaningful lowercase search terms.

    Splits on non-alphanumeric runs, then drops stopwords and tokens shorter
    than three characters, so 'the chat where we discussed slack hooks'
    reduces to ['slack', 'hooks']. Order-preserving and de-duplicated.

    Args:
        query: The raw search string typed after `airesume -s`.

    Returns:
        The distinct keyword tokens, in first-seen order.
    """
    import re

    words = re.findall(r"[a-z0-9]+", query.lower())
    return [
        word
        for word in dict.fromkeys(words)
        if len(word) >= 3 and word not in _SEARCH_STOPWORDS
    ]


def _score_metadata(terms: list[str], agents: tuple[str, ...]) -> list[dict]:
    """Score sessions by how many `terms` appear in name + summary + cwd."""
    scored: list[dict] = []
    for agent in agents:
        _set_current_agent(agent)
        for name, entry in load_index().items():
            session_id = entry.get("session_id") or ""
            if not session_id:
                continue
            haystack = " ".join(
                [name, entry.get("summary") or "", entry.get("cwd") or ""]
            ).lower()
            score = sum(1 for term in terms if term in haystack)
            if not score:
                continue
            cwd, transcript = _resolve_resume_cwd(session_id, entry.get("cwd", ""))
            if transcript is None:
                continue
            hit = _candidate(agent, name, session_id, entry, cwd)
            hit["score"] = score
            scored.append(hit)
    return scored


def _score_content(terms: list[str], agents: tuple[str, ...]) -> list[dict]:
    """Score Claude sessions by how many `terms` appear in their transcripts.

    Fallback used only when the metadata pass finds nothing. Codex and Gemini
    transcripts are not scanned (`_search_transcripts` is Claude-only), so this
    pass is skipped unless the claude registry is in scope.
    """
    if "claude" not in agents:
        return []
    _set_current_agent("claude")
    term_hits: dict[str, int] = {}
    for term in terms:
        for session_id in _search_transcripts(term):
            term_hits[session_id] = term_hits.get(session_id, 0) + 1
    if not term_hits:
        return []
    scored: list[dict] = []
    for name, entry in load_index().items():
        session_id = entry.get("session_id") or ""
        if session_id not in term_hits:
            continue
        cwd, transcript = _resolve_resume_cwd(session_id, entry.get("cwd", ""))
        if transcript is None:
            continue
        hit = _candidate("claude", name, session_id, entry, cwd)
        hit["score"] = term_hits[session_id]
        scored.append(hit)
    return scored


def _search_candidates(terms: list[str], agents: tuple[str, ...]) -> list[dict]:
    """Best-matching resumable sessions for `terms`, tied at the top score.

    Tries a metadata pass (name/summary/cwd) first, falling back to a Claude
    transcript-content pass. Returns the sessions tied at the highest
    term-hit count, most-recent first, capped at `_SEARCH_PICK_LIMIT`.
    """
    scored = _score_metadata(terms, agents) or _score_content(terms, agents)
    if not scored:
        return []
    top = max(hit["score"] for hit in scored)
    winners = [hit for hit in scored if hit["score"] == top]
    winners.sort(key=lambda hit: hit["updated"], reverse=True)
    return winners[:_SEARCH_PICK_LIMIT]


def cmd_search_resume(args: argparse.Namespace) -> int:
    """Resolve a free-text query to one resumable session and emit it.

    Scores sessions across every agent (or one, with `--agent`) by keyword
    overlap. A sole top-scoring session resumes outright; a tie opens the
    picker — the same flow as `prefix-resume`. Prints `<agent>\\t<cwd>\\t<token>`
    on success.
    """
    agents = AGENTS if args.agent == "auto" else (args.agent,)
    terms = _query_terms(args.query)
    if not terms:
        print(
            f"airesume: search query '{args.query}' has no usable keywords.",
            file=sys.stderr,
        )
        return 2
    winners = _search_candidates(terms, agents)
    if not winners:
        print(
            f"airesume: no session matching [{' '.join(terms)}] "
            f"in {'/'.join(agents)}.",
            file=sys.stderr,
        )
        return 2

    if len(winners) == 1:
        chosen: dict | None = winners[0]
    else:
        label = args.query if len(args.query) <= 50 else args.query[:47] + "..."
        chosen = _pick_session(
            winners, f'{len(winners)} sessions match "{label}":', True
        )

    if chosen is None:
        print("airesume: cancelled.", file=sys.stderr)
        return 3

    print(
        f'airesume → {chosen["agent"]} "{chosen["name"]}" [{chosen["cwd"]}]',
        file=sys.stderr,
    )
    return _emit_resume(chosen)


def cmd_move(args: argparse.Namespace) -> int:
    """Relocate a recorded session's transcript to a different cwd.

    Claude Code stores each session's JSONL under
    ~/.claude/projects/<encoded-cwd>/<uuid>.jsonl, where encoded-cwd is the
    launch shell's cwd with `/` replaced by `-`. To make `cc-resume <name>`
    invoke claude from a different working directory, we need to move the
    transcript under the new encoded path AND update the registry.

    Refuses to act on live sessions: a running claude rebinds the transcript
    to its launch-cwd and recreates the JSONL within seconds. Detection is
    conservative — if the JSONL was modified within --live-mtime-secs (or any
    process holds the file open), abort.
    """
    import re
    import time

    if CURRENT_AGENT != "claude":
        print("move is only supported for Claude Code transcripts.", file=sys.stderr)
        return 1

    index = load_index()
    entry = index.get(args.name)
    if not entry:
        print(f"No session named '{args.name}'", file=sys.stderr)
        return 1

    sid = entry["session_id"]
    old_cwd = entry.get("cwd", "")
    new_cwd = os.path.realpath(os.path.expanduser(args.new_cwd))

    if not os.path.isdir(new_cwd):
        print(f"new cwd does not exist: {new_cwd}", file=sys.stderr)
        return 1

    if old_cwd == new_cwd:
        print(f"Registry already shows cwd={new_cwd}; nothing to do.")
        return 0

    old_dir = CC_PROJECTS_DIR / _encoded_cwd(old_cwd)
    new_dir = CC_PROJECTS_DIR / _encoded_cwd(new_cwd)
    old_file = old_dir / f"{sid}.jsonl"
    new_file = new_dir / f"{sid}.jsonl"

    if not old_file.is_file():
        print(f"transcript not at expected old path: {old_file}", file=sys.stderr)
        return 1
    if new_file.exists():
        print(f"refusing to overwrite existing transcript: {new_file}", file=sys.stderr)
        return 1

    # Live-session guard via mtime. Claude Code does not keep the JSONL open
    # between turns (open-write-close), so an fd scan alone misses it. The
    # mtime check is the strongest signal a turn was recently written.
    age = time.time() - old_file.stat().st_mtime
    if not args.force and age < args.live_mtime_secs:
        print(
            f"ABORT: transcript was modified {age:.0f}s ago — session is likely live.\n"
            f"Exit the claude session, then re-run. Use --force or "
            f"--live-mtime-secs to override.",
            file=sys.stderr,
        )
        return 1

    # Fd-based defence in depth.
    in_use_pid = _pid_holding_file(old_file)
    if in_use_pid is not None and not args.force:
        print(f"ABORT: transcript is open by PID {in_use_pid}.", file=sys.stderr)
        return 1

    # 1. Move the JSONL.
    new_dir.mkdir(parents=True, exist_ok=True)
    os.replace(old_file, new_file)
    print(f"moved: {old_file} -> {new_file}")

    # 2. Rewrite embedded "cwd" fields so resume-cmd shows the new path.
    pattern = re.compile(
        r'"cwd":\s*' + re.escape(json.dumps(old_cwd))
    )
    replacement = '"cwd":' + json.dumps(new_cwd)
    text = new_file.read_text(encoding="utf-8", errors="replace")
    rewritten, n = pattern.subn(replacement, text)
    tmp = new_file.with_suffix(new_file.suffix + ".tmp")
    tmp.write_text(rewritten, encoding="utf-8")
    os.replace(tmp, new_file)
    print(f"rewrote {n} embedded cwd field(s)")

    # 3. Update the registry entry. Drop empty source dir if we created it.
    entry["cwd"] = new_cwd
    entry["last_updated"] = now_iso()
    index[args.name] = entry
    save_index(index)
    print(f"registry: '{args.name}' -> {new_cwd}")

    try:
        old_dir.rmdir()
    except OSError:
        pass

    return 0


def _pid_holding_file(path: Path) -> int | None:
    """Return a PID currently holding `path` open, or None.

    Tries lsof first; falls back to a /proc fd scan on Linux.
    """
    import shutil
    import subprocess

    target = str(path)
    if shutil.which("lsof"):
        try:
            out = subprocess.run(
                ["lsof", "-t", "--", target],
                capture_output=True,
                text=True,
                timeout=5,
            )
            pids = [line for line in out.stdout.splitlines() if line.strip().isdigit()]
            if pids:
                return int(pids[0])
        except (subprocess.SubprocessError, OSError):
            pass

    proc = Path("/proc")
    if not proc.is_dir():
        return None
    for pid_dir in proc.glob("[0-9]*"):
        fd_dir = pid_dir / "fd"
        if not fd_dir.is_dir():
            continue
        try:
            for fd_link in fd_dir.iterdir():
                try:
                    if os.readlink(fd_link) == target:
                        return int(pid_dir.name)
                except OSError:
                    continue
        except OSError:
            continue
    return None


TOP_EPILOG = """\
Common workflows:

  Find a session you remember by topic:
    %(prog)s search jellyfin

  Resume a session by name (prints `cd ... && claude --resume ...`):
    %(prog)s resume-cmd proxmox-backup

  See recent sessions, hiding ones whose transcripts are gone:
    %(prog)s list --alive --limit 20

  Mark an important session for permanent backup:
    %(prog)s keep proxmox-backup

  Clean up dead entries (transcript file missing):
    %(prog)s prune --orphans --dry-run
    %(prog)s prune --orphans

Registries:
  Claude: ~/.claude/session-names/index.json
  Codex:  ~/.codex/session-names/index.json
  Gemini: ~/.gemini/session-names/index.json
"""


def detect_agent(requested: str, cwd: str | None = None) -> str:
    if requested != "auto":
        return requested
    if os.environ.get("CODEX_THREAD_ID"):
        return "codex"
    if os.environ.get("GEMINI_SESSION_ID") or os.environ.get("GEMINI_CLI"):
        return "gemini"

    cwd = cwd or os.getcwd()
    if _find_current_session_id(cwd):
        return "claude"
    if _find_current_gemini_session_id(cwd):
        return "gemini"
    if _find_current_codex_thread_id(cwd):
        return "codex"
    return "claude"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="session_registry.py",
        description="Manage named-session registries for Claude Code, Codex, and Gemini.",
        epilog=TOP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--agent",
        choices=("auto", *AGENTS),
        default="auto",
        help="Registry backend to use. auto detects Codex/Claude/Gemini.",
    )
    # Not required: running with no args prints help (see main()).
    sub = parser.add_subparsers(dest="cmd", metavar="COMMAND")

    p_reg = sub.add_parser(
        "register",
        help="Register/update a named session",
        description="Bind a human-friendly name to a Claude Code session id. "
        "Re-registering an existing name updates it in place; if the same "
        "session id is already bound to another name, that other name is "
        "dropped (rename semantics).",
        epilog="Example:\n"
        "  %(prog)s my-chat --session-id 36e45e92-... --cwd /home/robin/proj",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_reg.add_argument("name", help="Human-friendly name (e.g. 'my-chat').")
    p_reg.add_argument("--session-id", required=True, help="Claude Code session UUID.")
    p_reg.add_argument("--cwd", required=True, help="Working directory the session ran in.")
    p_reg.add_argument("--summary", default=None, help="Optional one-line summary.")
    p_reg.add_argument(
        "--auto",
        action="store_true",
        help="Mark the entry as auto-registered (eligible for `prune`).",
    )
    p_reg.set_defaults(func=cmd_register)

    p_get = sub.add_parser(
        "get",
        help="Print one entry's full JSON",
        description="Show the full JSON record for a named session "
        "(session_id, cwd, created, last_updated, summary, tags).",
    )
    p_get.add_argument("name")
    p_get.set_defaults(func=cmd_get)

    p_list = sub.add_parser(
        "list",
        help="List named sessions, newest first",
        description="List every named session as `name  short-id  updated  cwd  [tags]`. "
        "Tags: KEEP (MUSTKEEP), auto (auto-registered), GONE (transcript missing).",
        epilog="Examples:\n"
        "  %(prog)s                  # all entries\n"
        "  %(prog)s --limit 20       # only the 20 most recent\n"
        "  %(prog)s --alive          # hide entries whose transcript is gone",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_list.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Show only the N most recent entries (0 = no limit, default).",
    )
    p_list.add_argument(
        "--alive",
        action="store_true",
        help="Hide entries whose transcript file is missing (the [GONE] tag).",
    )
    p_list.set_defaults(func=cmd_list)

    p_rm = sub.add_parser(
        "remove",
        help="Delete an entry from the registry",
        description="Drop a name from the registry. The transcript file on disk is "
        "untouched. MUSTKEEP entries refuse removal unless --force is given.",
    )
    p_rm.add_argument("name")
    p_rm.add_argument(
        "--force",
        action="store_true",
        help="Remove even if marked MUSTKEEP.",
    )
    p_rm.set_defaults(func=cmd_remove)

    p_keep = sub.add_parser(
        "keep",
        help="Mark MUSTKEEP and snapshot the transcript",
        description="Set keep=true and copy the transcript JSONL to "
        "~/.claude/session-names/backups/<name>-<shortid>.jsonl. The entry is "
        "then exempt from `prune` and `prune --all-auto`.",
    )
    p_keep.add_argument("name")
    p_keep.set_defaults(func=cmd_keep)

    p_unkeep = sub.add_parser(
        "unkeep",
        help="Clear the MUSTKEEP marker",
        description="Remove the keep flag. The backup file under "
        "~/.claude/session-names/backups/ stays on disk; delete it manually if "
        "you want it gone.",
    )
    p_unkeep.add_argument("name")
    p_unkeep.set_defaults(func=cmd_unkeep)

    p_touch = sub.add_parser(
        "touch",
        help="Refresh last_updated for a session id",
        description="Used by the SessionEnd hook to bump last_updated and "
        "(optionally) refresh the summary. Operates by session id, not name.",
    )
    p_touch.add_argument("--session-id", required=True)
    p_touch.add_argument("--summary", default=None)
    p_touch.set_defaults(func=cmd_touch)

    p_resume = sub.add_parser(
        "resume-cmd",
        help="Print the shell command to resume a named session",
        description="Print `cd <cwd> && claude --resume <session-id>` for the "
        "named session. The cwd is read from the transcript file itself, so the "
        "command is robust to a drifted index. Exits non-zero if the transcript "
        "file is missing (running the printed command would fail anyway).",
        epilog="Example:\n"
        "  $(%(prog)s proxmox-backup)\n"
        "  # or, inside Claude Code, prefix with ! to run in the harness shell:\n"
        "  ! $(python3 ~/.claude/skills/resume-session/scripts/"
        "session_registry.py resume-cmd proxmox-backup)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_resume.add_argument("name")
    p_resume.set_defaults(func=cmd_resume_cmd)

    p_prefix = sub.add_parser(
        "prefix-resume",
        help="Resolve a name-prefix across all agents to a resumable session",
        description="Scan the claude, codex, and gemini registries for sessions "
        "whose name starts with PREFIX, then pick the most likely target: a "
        "sole match in --cwd wins outright; several there open an interactive "
        "picker; with nothing in --cwd, a unique exact-name (or sole overall) "
        "match elsewhere wins, otherwise a cross-folder picker runs. Prints one "
        "tab-separated line `<agent>\\t<cwd>\\t<token>` on success — token is "
        "the session UUID (claude), registered name (codex), or resume index "
        "(gemini). Pass the global --agent flag to restrict the scan to a "
        "single registry. Drives the `airesume` shell function.",
        epilog="Exit codes: 0 resume, 2 no match, 3 cancelled.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_prefix.add_argument(
        "prefix", help="Session-name prefix typed after `airesume`."
    )
    p_prefix.add_argument(
        "--cwd", required=True, help="The calling shell's working directory ($PWD)."
    )
    p_prefix.set_defaults(func=cmd_prefix_resume)

    p_search_resume = sub.add_parser(
        "search-resume",
        help="Resolve a free-text query to a resumable session (airesume -s)",
        description="Score sessions across every agent (or one, with --agent) "
        "by how many keywords from QUERY appear in their name, summary, and "
        "cwd — falling back to a Claude transcript-content scan when metadata "
        "matches nothing. The sessions tied at the top score are the result: "
        "one resumes outright, several open the picker. Prints one "
        "tab-separated line `<agent>\\t<cwd>\\t<token>` on success. Drives "
        "`airesume -s`.",
        epilog="Exit codes: 0 resume, 2 no match, 3 cancelled.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_search_resume.add_argument(
        "query", help="Free-text description of the session to resume."
    )
    p_search_resume.set_defaults(func=cmd_search_resume)

    p_move = sub.add_parser(
        "move",
        help="Relocate a recorded session's transcript to a new cwd",
        description="Move the JSONL transcript under "
        "~/.claude/projects/<encoded-cwd>/ for a different working directory, "
        "rewrite its embedded cwd fields, and update the registry. Refuses "
        "to act on live sessions (mtime guard + open-fd check). Run AFTER "
        "you've exited the claude session.",
        epilog="Example:\n"
        "  %(prog)s my-chat /home/me/git/new-project",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_move.add_argument("name", help="Registered session name.")
    p_move.add_argument("new_cwd", help="Destination working directory.")
    p_move.add_argument(
        "--live-mtime-secs",
        type=int,
        default=300,
        help="Refuse to move if the JSONL was modified within this many "
        "seconds (default 300).",
    )
    p_move.add_argument(
        "--force",
        action="store_true",
        help="Skip the live-session safety checks. Use only when you're "
        "certain the session is dead.",
    )
    p_move.set_defaults(func=cmd_move)

    p_prune = sub.add_parser(
        "prune",
        help="Delete stale or orphaned entries",
        description="Default mode removes auto-registered entries whose "
        "last_updated is older than --days. --all-auto removes every auto entry "
        "regardless of age. --orphans removes any entry whose transcript file "
        "is missing (independent of auto/age). MUSTKEEP entries are always "
        "skipped.",
        epilog="Examples:\n"
        "  %(prog)s --dry-run               # preview default (auto + age)\n"
        "  %(prog)s --days 30               # auto + older than 30d\n"
        "  %(prog)s --all-auto              # every auto entry\n"
        "  %(prog)s --orphans --dry-run     # preview transcript-missing entries\n"
        "  %(prog)s --orphans               # actually drop them",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_prune.add_argument(
        "--days",
        type=int,
        default=60,
        help="Age threshold in days for auto-mode (default: 60).",
    )
    p_prune.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview without modifying the registry.",
    )
    p_prune.add_argument(
        "--all-auto",
        action="store_true",
        help="Remove all auto-registered entries regardless of age.",
    )
    p_prune.add_argument(
        "--orphans",
        action="store_true",
        help="Remove entries whose transcript file is missing, regardless of "
        "auto/age. Skips MUSTKEEP entries.",
    )
    p_prune.set_defaults(func=cmd_prune)

    p_search = sub.add_parser(
        "search",
        help="Find sessions by topic / keyword",
        description="Case-insensitive substring match over each entry's name, "
        "first-prompt summary, and cwd. Newest-first; entries with a missing "
        "transcript are tagged [GONE].",
        epilog="Examples:\n"
        "  %(prog)s jellyfin\n"
        "  %(prog)s 'home assistant'\n"
        "  %(prog)s /priv/homelabs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_search.add_argument("query", help="Substring to match (case-insensitive).")
    p_search.add_argument(
        "--content",
        action="store_true",
        help="Also search inside transcript files under ~/.claude/projects/. "
        "Slower but catches topics that appear mid-conversation rather than in "
        "the first prompt. Hits are tagged [content] in the output.",
    )
    p_search.set_defaults(func=cmd_search)

    p_cur = sub.add_parser(
        "register-current",
        help="Register the running session by cwd",
        description="Look up the currently-running Claude Code session by cwd "
        "(reading ~/.claude/sessions/<pid>.json) and register it under a name. "
        "Used by the /register and /rn slash commands so the user doesn't have to type the "
        "session UUID.",
    )
    p_cur.add_argument("name")
    p_cur.add_argument("--cwd", required=True)
    p_cur.add_argument("--summary", default=None)
    p_cur.add_argument(
        "--auto",
        action="store_true",
        help="Mark the entry as auto-registered (eligible for `prune`).",
    )
    p_cur.set_defaults(func=cmd_register_current)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return 0
    cwd = getattr(args, "cwd", None) or os.getcwd()
    _set_current_agent(detect_agent(args.agent, cwd))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
