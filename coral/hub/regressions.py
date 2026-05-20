"""Regression memory: per-fixture baseline scores that no commit may break.

CORAL's grader returns a `ScoreBundle` with multiple named scores (one per
"fixture" — could be a sub-task, a held-out evaluation case, a metric on a
benchmark suite). The aggregated score collapses these into one number that
goes onto the leaderboard, but the per-name dimension is exactly what
"regression memory" wants: any commit that makes one named fixture worse
than the team-established baseline is flagged, even if the aggregate looks
better.

The pattern is borrowed from Trinkle's "Learning Beyond Gradients" paper
(heuristic learning) — "old capabilities become test cases" — and is the
task-scale analogue of Crucible's `golden-cases` at the user scale.

Files under `.coral/public/regressions/`:

- `baseline.json` — current per-fixture lower bounds. Atomic writes.
- `history.jsonl` — append-only audit log of baseline updates.

Status semantics:

- An attempt that beats or matches every fixture's baseline (within tolerance)
  passes the regression check.
- An attempt that drops below baseline on any fixture FAILS — its
  `attempt.status` is forced to `"regression"` regardless of what
  `_compute_status` decided, and `metadata.regressed_fixtures` lists the
  offenders.
- When no baseline exists yet, the first PASS-status attempt auto-promotes
  itself, seeding the baseline from its own `bundle.scores`.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REGRESSION_STATUS = "regression"
"""`attempt.status` value when a baseline-fixture comparison fails."""

REGRESSION_CHECK_PASS = "pass"
REGRESSION_CHECK_FAIL = "fail"
REGRESSION_CHECK_NONE = "none"  # no baseline yet
REGRESSION_CHECK_SKIPPED = "skipped"  # grader didn't return per-name scores


@dataclass
class FixtureBaseline:
    """A single fixture's baseline entry."""

    score: float
    tolerance: float = 0.0
    minimize: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "tolerance": self.tolerance,
            "minimize": self.minimize,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FixtureBaseline:
        return cls(
            score=float(data["score"]),
            tolerance=float(data.get("tolerance", 0.0)),
            minimize=bool(data.get("minimize", False)),
        )

    def regressed(self, observed: float) -> bool:
        """True iff `observed` is worse than this baseline beyond tolerance."""
        if self.minimize:
            return observed > self.score + self.tolerance
        return observed < self.score - self.tolerance


@dataclass
class Baseline:
    """The full per-fixture baseline for a run."""

    baseline_commit: str
    set_at: str
    set_by: str
    fixtures: dict[str, FixtureBaseline] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_commit": self.baseline_commit,
            "set_at": self.set_at,
            "set_by": self.set_by,
            "fixtures": {name: fb.to_dict() for name, fb in self.fixtures.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Baseline:
        return cls(
            baseline_commit=data["baseline_commit"],
            set_at=data["set_at"],
            set_by=data.get("set_by", "unknown"),
            fixtures={
                name: FixtureBaseline.from_dict(fb) for name, fb in data.get("fixtures", {}).items()
            },
        )


@dataclass
class RegressionCheck:
    """Outcome of comparing a ScoreBundle against the current baseline."""

    status: str  # one of REGRESSION_CHECK_*
    regressed_fixtures: list[str] = field(default_factory=list)
    missing_fixtures: list[str] = field(default_factory=list)
    """Baseline names that the current bundle didn't include — surfaced as a
    soft warning rather than a hard fail (the grader may have legitimately
    pruned a fixture)."""

    @property
    def passed(self) -> bool:
        return self.status == REGRESSION_CHECK_PASS

    @property
    def failed(self) -> bool:
        return self.status == REGRESSION_CHECK_FAIL


# --------------------------------------------------------------------------- #
# Paths                                                                       #
# --------------------------------------------------------------------------- #


def _regressions_dir(coral_dir: str | Path) -> Path:
    p = Path(coral_dir) / "public" / "regressions"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _baseline_path(coral_dir: str | Path) -> Path:
    return _regressions_dir(coral_dir) / "baseline.json"


def _history_path(coral_dir: str | Path) -> Path:
    return _regressions_dir(coral_dir) / "history.jsonl"


# --------------------------------------------------------------------------- #
# I/O                                                                         #
# --------------------------------------------------------------------------- #


def read_baseline(coral_dir: str | Path) -> Baseline | None:
    """Return the current baseline, or None if not set yet."""
    path = _baseline_path(coral_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return Baseline.from_dict(data)


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Atomic write — same pattern as hub.attempts.write_attempt."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
        encoding="utf-8",
    ) as tmp:
        json.dump(data, tmp, indent=2, sort_keys=True)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def write_baseline(coral_dir: str | Path, baseline: Baseline) -> None:
    _atomic_write_json(_baseline_path(coral_dir), baseline.to_dict())


def _append_history(coral_dir: str | Path, entry: dict[str, Any]) -> None:
    """Append one event line to history.jsonl (best-effort, no atomicity)."""
    path = _history_path(coral_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True))
        f.write("\n")


def clear_baseline(coral_dir: str | Path) -> bool:
    """Delete the baseline. Returns True if there was one to remove."""
    path = _baseline_path(coral_dir)
    if not path.exists():
        return False
    path.unlink()
    _append_history(
        coral_dir,
        {
            "at": datetime.now(UTC).isoformat(),
            "event": "reset",
            "commit": None,
            "by": "manual",
        },
    )
    return True


# --------------------------------------------------------------------------- #
# Comparison + auto-promote                                                   #
# --------------------------------------------------------------------------- #


def _bundle_scores(bundle: Any) -> dict[str, float]:
    """Extract `{name: float}` from a ScoreBundle, skipping None values."""
    scores = getattr(bundle, "scores", None) or {}
    out: dict[str, float] = {}
    for name, score in scores.items():
        v = getattr(score, "value", None)
        if v is None:
            continue
        try:
            out[name] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def check_against_baseline(
    coral_dir: str | Path,
    bundle: Any,
) -> RegressionCheck:
    """Compare `bundle.scores` to the persisted baseline.

    Pure read; does not mutate the baseline.
    """
    baseline = read_baseline(coral_dir)
    if baseline is None:
        return RegressionCheck(status=REGRESSION_CHECK_NONE)

    observed = _bundle_scores(bundle)
    if not observed:
        return RegressionCheck(status=REGRESSION_CHECK_SKIPPED)

    regressed: list[str] = []
    missing: list[str] = []
    for name, fb in baseline.fixtures.items():
        if name not in observed:
            missing.append(name)
            continue
        if fb.regressed(observed[name]):
            regressed.append(name)

    if regressed:
        return RegressionCheck(
            status=REGRESSION_CHECK_FAIL,
            regressed_fixtures=sorted(regressed),
            missing_fixtures=sorted(missing),
        )
    return RegressionCheck(
        status=REGRESSION_CHECK_PASS,
        missing_fixtures=sorted(missing),
    )


def promote(
    coral_dir: str | Path,
    bundle: Any,
    commit_hash: str,
    by: str,
    event: str = "promote",
    tolerance: float = 0.0,
    minimize: bool = False,
) -> Baseline:
    """Write `bundle.scores` as the new baseline. Returns the written Baseline.

    `event` is "set" (first time), "promote" (auto on improved attempt),
    or "manual" (CLI `coral regression promote`). Carried into history.jsonl
    for audit.
    """
    observed = _bundle_scores(bundle)
    fixtures = {
        name: FixtureBaseline(score=value, tolerance=tolerance, minimize=minimize)
        for name, value in observed.items()
    }
    baseline = Baseline(
        baseline_commit=commit_hash,
        set_at=datetime.now(UTC).isoformat(),
        set_by=by,
        fixtures=fixtures,
    )
    write_baseline(coral_dir, baseline)
    _append_history(
        coral_dir,
        {
            "at": baseline.set_at,
            "event": event,
            "commit": commit_hash,
            "by": by,
            "fixtures": {name: fb.to_dict() for name, fb in fixtures.items()},
        },
    )
    return baseline


def maybe_seed_baseline(
    coral_dir: str | Path,
    bundle: Any,
    commit_hash: str,
    by: str,
    minimize: bool = False,
) -> Baseline | None:
    """If no baseline exists and `bundle` has named scores, seed one.

    Returns the seeded baseline or None if nothing was written (baseline
    already exists, or the bundle had no usable named scores).
    """
    if read_baseline(coral_dir) is not None:
        return None
    if not _bundle_scores(bundle):
        return None
    return promote(coral_dir, bundle, commit_hash, by, event="set", minimize=minimize)
