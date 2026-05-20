"""CLI: `coral archive {list,run,undo}`.

Hide low-value attempts from the leaderboard without deleting them.
See `coral.hub.archive` for the underlying primitive.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from coral.cli._helpers import find_coral_dir
from coral.hub.archive import (
    ArchiveCriteria,
    archive_many,
    is_archived,
    select_for_archive,
    unarchive_attempt,
)
from coral.hub.attempts import read_attempts


def _resolve_coral_dir(args: argparse.Namespace) -> Path:
    workdir = Path(getattr(args, "workdir", None) or ".").resolve()
    breadcrumb = workdir / ".coral_dir"
    if breadcrumb.exists():
        try:
            return Path(breadcrumb.read_text().strip()).resolve()
        except OSError:
            pass
    return find_coral_dir(getattr(args, "task", None), getattr(args, "run", None))


def _parse_before(value: str | None) -> str | None:
    """Accept either `30d` (relative, days) or an ISO-8601 timestamp."""
    if value is None:
        return None
    value = value.strip()
    if value.endswith("d") and value[:-1].isdigit():
        days = int(value[:-1])
        return (datetime.now(UTC) - timedelta(days=days)).isoformat()
    # Assume ISO; let downstream comparisons fail noisily if not parseable.
    return value


def _parse_status_set(value: str | None) -> tuple[str, ...] | None:
    if not value:
        return None
    return tuple(s.strip() for s in value.split(",") if s.strip())


def cmd_archive_list(args: argparse.Namespace) -> None:
    """Show every attempt that is currently archived."""
    coral_dir = _resolve_coral_dir(args)
    rows = [a for a in read_attempts(coral_dir) if is_archived(a)]
    if not rows:
        print("No archived attempts.")
        return
    rows.sort(key=lambda a: a.timestamp, reverse=True)
    print(f"Archived ({len(rows)}):")
    for a in rows:
        meta = (a.metadata or {}).get("archived") or {}
        reason = meta.get("reason") if isinstance(meta, dict) else ""
        score = f"{a.score:.6f}" if a.score is not None else "—"
        print(
            f"  {a.commit_hash[:10]}  score={score:>10}  status={a.status:<10} "
            f"agent={a.agent_id:<12}  ({reason or 'no reason'})"
        )


def cmd_archive_run(args: argparse.Namespace) -> None:
    """Archive every attempt matching the criteria."""
    coral_dir = _resolve_coral_dir(args)
    criteria = ArchiveCriteria(
        score_below=args.score_below,
        before_date=_parse_before(args.before),
        status_in=_parse_status_set(args.status),
    )

    matched = select_for_archive(coral_dir, criteria)
    if not matched:
        print("No attempts match the criteria.")
        return

    print(f"Matched {len(matched)} attempts:")
    matched.sort(key=lambda a: (a.score or 0.0, a.timestamp))
    preview = matched if len(matched) <= 20 else matched[:10] + matched[-10:]
    if len(matched) > 20:
        print(f"  (showing first 10 and last 10 of {len(matched)})")
    for a in preview:
        score = f"{a.score:.6f}" if a.score is not None else "—"
        print(f"  {a.commit_hash[:10]}  score={score:>10}  status={a.status}  agent={a.agent_id}")

    if not args.apply:
        print("\nThis was a dry run. Re-run with --apply to archive.")
        return

    reason = args.reason or _criteria_summary(criteria)
    n = archive_many(coral_dir, matched, reason=reason)
    print(f"\nArchived {n} attempt(s). Run `coral archive list` to inspect.")


def cmd_archive_undo(args: argparse.Namespace) -> None:
    """Reverse a previous archive operation on a single attempt."""
    coral_dir = _resolve_coral_dir(args)
    target = args.hash
    # Partial-hash resolution.
    attempts_dir = coral_dir / "public" / "attempts"
    if attempts_dir.exists() and len(target) < 40:
        matches = list(attempts_dir.glob(f"{target}*.json"))
        if len(matches) == 1:
            target = matches[0].stem
        elif len(matches) > 1:
            print(f"Ambiguous hash prefix '{target}'. Matches:", file=sys.stderr)
            for m in matches:
                print(f"  {m.stem}", file=sys.stderr)
            sys.exit(1)
        else:
            print(f"Error: No attempt matches '{target}'.", file=sys.stderr)
            sys.exit(1)
    if unarchive_attempt(coral_dir, target):
        print(f"Un-archived {target[:12]}.")
    else:
        # Distinguish "not archived" from "doesn't exist".
        from coral.hub.attempts import read_attempt

        if read_attempt(coral_dir, target) is None:
            print(f"Error: attempt {target[:12]} not found.", file=sys.stderr)
            sys.exit(1)
        print(f"Attempt {target[:12]} was not archived; nothing to do.")


def _criteria_summary(criteria: ArchiveCriteria) -> str:
    parts: list[str] = []
    if criteria.score_below is not None:
        parts.append(f"score<{criteria.score_below}")
    if criteria.before_date is not None:
        parts.append(f"before={criteria.before_date}")
    if criteria.status_in is not None:
        parts.append(f"status∈{{{','.join(criteria.status_in)}}}")
    return "; ".join(parts) if parts else "(no criteria)"


def cmd_archive(args: argparse.Namespace) -> None:
    """Dispatch on the archive sub-subcommand."""
    sub = getattr(args, "archive_command", None)
    if sub == "list" or sub is None:
        cmd_archive_list(args)
    elif sub == "run":
        cmd_archive_run(args)
    elif sub == "undo":
        cmd_archive_undo(args)
    else:
        print(f"Unknown archive command: {sub}", file=sys.stderr)
        sys.exit(1)
