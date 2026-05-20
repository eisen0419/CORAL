"""CLI: `coral regression {status,promote,reset}`.

Operator/agent tools for inspecting and managing the per-fixture baseline
that the grader daemon enforces. See `coral.hub.regressions` for the
underlying storage + comparison.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from coral.cli._helpers import find_coral_dir
from coral.hub.attempts import read_attempt
from coral.hub.regressions import (
    Baseline,
    FixtureBaseline,
    _append_history,
    clear_baseline,
    read_baseline,
    write_baseline,
)


def _resolve_coral_dir(args: argparse.Namespace) -> Path:
    """Same dual lookup pattern as the eval/wait commands."""
    workdir = Path(getattr(args, "workdir", None) or ".").resolve()
    breadcrumb = workdir / ".coral_dir"
    if breadcrumb.exists():
        try:
            return Path(breadcrumb.read_text().strip()).resolve()
        except OSError:
            pass
    return find_coral_dir(getattr(args, "task", None), getattr(args, "run", None))


def cmd_regression_status(args: argparse.Namespace) -> None:
    """Print the current baseline (or note that none is set)."""
    coral_dir = _resolve_coral_dir(args)
    baseline = read_baseline(coral_dir)

    if baseline is None:
        print("No baseline set for this run.")
        print("A baseline is auto-seeded by the first attempt with status='improved'")
        print("and named scores, or you can set one manually:")
        print("  coral regression promote <hash>")
        return

    print(f"Baseline commit: {baseline.baseline_commit[:12]}")
    print(f"Set at:          {baseline.set_at}")
    print(f"Set by:          {baseline.set_by}")
    print(f"Fixtures ({len(baseline.fixtures)}):")
    if not baseline.fixtures:
        print("  (none)")
        return
    width = max(len(name) for name in baseline.fixtures) + 2
    for name, fb in sorted(baseline.fixtures.items()):
        direction = "↓" if fb.minimize else "↑"
        tol = f"  (±{fb.tolerance})" if fb.tolerance else ""
        print(f"  {name.ljust(width)} {direction} {fb.score}{tol}")


def cmd_regression_promote(args: argparse.Namespace) -> None:
    """Force the named attempt to become the new baseline.

    Reads the attempt's score bundle from `.coral/public/attempts/<hash>.json`
    and writes a baseline.json with each named score as a fixture floor.
    """
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

    attempt = read_attempt(coral_dir, target)
    if attempt is None:
        print(f"Error: attempt {target[:12]} not found.", file=sys.stderr)
        sys.exit(1)

    # We need the per-fixture scores, which live in the on-disk attempt JSON's
    # `metadata.score_breakdown` if the grader populated it — most graders
    # today only set `aggregated`. Fall back to reading the file directly so
    # we can pick up `bundle.scores` if it was persisted.
    raw = json.loads((attempts_dir / f"{target}.json").read_text())
    score_breakdown = raw.get("metadata", {}).get("score_breakdown")
    if not score_breakdown:
        # Fall back to seeding from aggregate only — single fixture named "score".
        if attempt.score is None:
            print(
                f"Error: attempt {target[:12]} has no score; cannot promote.",
                file=sys.stderr,
            )
            sys.exit(1)
        score_breakdown = {"score": float(attempt.score)}

    fixtures = {
        name: FixtureBaseline(score=float(value), tolerance=0.0, minimize=False)
        for name, value in score_breakdown.items()
    }
    baseline = Baseline(
        baseline_commit=target,
        set_at=datetime.now(UTC).isoformat(),
        set_by="manual",
        fixtures=fixtures,
    )
    write_baseline(coral_dir, baseline)
    _append_history(
        coral_dir,
        {
            "at": baseline.set_at,
            "event": "manual",
            "commit": target,
            "by": "manual",
            "fixtures": {name: fb.to_dict() for name, fb in fixtures.items()},
        },
    )
    print(f"Promoted {target[:12]} as new baseline ({len(fixtures)} fixture(s)).")


def cmd_regression_reset(args: argparse.Namespace) -> None:
    """Delete the baseline. New attempts won't be checked until a baseline
    is re-established (auto or via `promote`)."""
    coral_dir = _resolve_coral_dir(args)
    if not args.yes:
        print("This will delete .coral/public/regressions/baseline.json.")
        print("Re-run with --yes to confirm.")
        return
    removed = clear_baseline(coral_dir)
    if removed:
        print("Baseline cleared.")
    else:
        print("No baseline was set.")


def cmd_regression(args: argparse.Namespace) -> None:
    """Dispatch on the regression sub-subcommand."""
    sub = getattr(args, "regression_command", None)
    if sub == "status" or sub is None:
        cmd_regression_status(args)
    elif sub == "promote":
        cmd_regression_promote(args)
    elif sub == "reset":
        cmd_regression_reset(args)
    else:
        print(f"Unknown regression command: {sub}", file=sys.stderr)
        sys.exit(1)
