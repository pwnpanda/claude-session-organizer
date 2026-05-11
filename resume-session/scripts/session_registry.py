#!/usr/bin/env python3
"""Named-session registry for Claude Code.

Persists a mapping from human-friendly names to Claude Code session
metadata so sessions can be resumed by name via `claude --resume <id>`.

Storage: ~/.claude/session-names/index.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REGISTRY_DIR = Path.home() / ".claude" / "session-names"
INDEX_PATH = REGISTRY_DIR / "index.json"
BACKUP_DIR = REGISTRY_DIR / "backups"
CC_PROJECTS_DIR = Path.home() / ".claude" / "projects"


def load_index() -> dict:
    if not INDEX_PATH.exists():
        return {}
    try:
        return json.loads(INDEX_PATH.read_text())
    except json.JSONDecodeError:
        return {}


def save_index(index: dict) -> None:
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=REGISTRY_DIR, prefix=".index.", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(index, f, indent=2, sort_keys=True)
        os.replace(tmp_path, INDEX_PATH)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
    save_index(index)
    print(f"Registered '{args.name}' -> {args.session_id[:8]} ({args.cwd})")
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
    import shutil
    import subprocess

    if not CC_PROJECTS_DIR.is_dir():
        return set()

    rg = shutil.which("rg")
    if rg:
        try:
            proc = subprocess.run(
                [rg, "--files-with-matches", "-i", "--glob", "*.jsonl", query, str(CC_PROJECTS_DIR)],
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
    if not CC_PROJECTS_DIR.is_dir():
        return None
    matches = list(CC_PROJECTS_DIR.glob(f"*/{session_id}.jsonl"))
    return matches[0] if matches else None


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
                cwd = obj.get("cwd")
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
            f"Warning: no transcript found for session {session_id[:8]} under {CC_PROJECTS_DIR}. "
            "Marking as keep, but no backup taken.",
            file=sys.stderr,
        )
        entry["keep"] = True
        index[args.name] = entry
        save_index(index)
        return 0

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dest = BACKUP_DIR / f"{args.name}-{session_id[:8]}.jsonl"
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


def cmd_register_current(args: argparse.Namespace) -> int:
    """Register the current session (looked up by cwd) under a name."""
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
            f"Transcript for session {sid[:8]} is missing under {CC_PROJECTS_DIR}. "
            f"Cannot resume. Use `prune --orphans` to clean up the entry.",
            file=sys.stderr,
        )
        return 1
    print(f"cd {cwd!s} && claude --resume {sid}")
    return 0


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

Registry: ~/.claude/session-names/index.json
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="session_registry.py",
        description="Manage the Claude Code named-session registry.",
        epilog=TOP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
        "  ! $(python3 ~/.claude/skills/resume-session/scripts/session_registry.py resume-cmd proxmox-backup)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_resume.add_argument("name")
    p_resume.set_defaults(func=cmd_resume_cmd)

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
        "Used by the /save slash command so the user doesn't have to type the "
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
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
