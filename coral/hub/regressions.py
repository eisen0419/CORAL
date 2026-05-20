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

import contextlib
import fcntl
import json
import os
import tempfile
from collections.abc import Iterator
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


def _baseline_lock_path(coral_dir: str | Path) -> Path:
    return _regressions_dir(coral_dir) / "baseline.json.lock"


def _history_path(coral_dir: str | Path) -> Path:
    return _regressions_dir(coral_dir) / "history.jsonl"


@contextlib.contextmanager
def _baseline_lock(coral_dir: str | Path) -> Iterator[None]:
    """Cross-process exclusive lock around baseline read-modify-write.

    The grader daemon runs N workers in a thread pool (and the operator
    may also run `coral regression promote` concurrently). Without a
    lock, two workers can both:
      1. read the same stale baseline,
      2. compute a "pass" verdict against it,
      3. write divergent new baselines — last-writer-wins drops the
         other's improvement and can erase fixtures.

    The lock file is a sibling (`baseline.json.lock`) so the actual
    baseline file is never opened in a mode that conflicts with the
    atomic tmp+rename used by `write_baseline`.

    POSIX `fcntl.flock`. CORAL targets macOS / Linux; Windows isn't
    supported as a runtime anyway.
    """
    lock_path = _baseline_lock_path(coral_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


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


def _bundle_fixtures(bundle: Any, default_minimize: bool) -> dict[str, tuple[float, bool]]:
    """Extract `{name: (value, minimize)}` from a ScoreBundle.

    Per-fixture direction is read from each `Score.metadata["minimize"]`
    when present (lets a grader say "this fixture is lower-better, that
    one is higher-better"); otherwise the caller's `default_minimize`
    applies (task-level direction from `grader.direction`).
    """
    scores = getattr(bundle, "scores", None) or {}
    out: dict[str, tuple[float, bool]] = {}
    for name, score in scores.items():
        v = getattr(score, "value", None)
        if v is None:
            continue
        try:
            value = float(v)
        except (TypeError, ValueError):
            continue
        meta = getattr(score, "metadata", None) or {}
        if "minimize" in meta:
            minimize = bool(meta["minimize"])
        else:
            minimize = default_minimize
        out[name] = (value, minimize)
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


def _promote_inside_lock(
    coral_dir: str | Path,
    bundle: Any,
    commit_hash: str,
    by: str,
    *,
    event: str,
    tolerance: float,
    minimize: bool,
    retire_missing: bool,
    existing: Baseline | None,
) -> Baseline:
    """Caller must hold `_baseline_lock`. Performs the merge + write.

    Split from the public `promote` so the daemon's check-and-promote
    path can re-read the baseline inside the same critical section
    (without acquiring the lock twice).
    """
    fixture_data = _bundle_fixtures(bundle, default_minimize=minimize)

    fixtures: dict[str, FixtureBaseline] = {}
    retired: list[str] = []
    preserved: list[str] = []

    if existing is not None:
        for name, fb in existing.fixtures.items():
            if name in fixture_data:
                continue  # will be overwritten below
            if retire_missing:
                retired.append(name)
            else:
                fixtures[name] = fb
                preserved.append(name)

    for name, (value, per_fix_minimize) in fixture_data.items():
        fixtures[name] = FixtureBaseline(
            score=value, tolerance=tolerance, minimize=per_fix_minimize
        )

    baseline = Baseline(
        baseline_commit=commit_hash,
        set_at=datetime.now(UTC).isoformat(),
        set_by=by,
        fixtures=fixtures,
    )
    write_baseline(coral_dir, baseline)

    history: dict[str, Any] = {
        "at": baseline.set_at,
        "event": event,
        "commit": commit_hash,
        "by": by,
        "fixtures": {name: fb.to_dict() for name, fb in fixtures.items()},
    }
    if preserved:
        history["preserved_fixtures"] = sorted(preserved)
    if retired:
        history["retired_fixtures"] = sorted(retired)
    _append_history(coral_dir, history)
    return baseline


def promote(
    coral_dir: str | Path,
    bundle: Any,
    commit_hash: str,
    by: str,
    event: str = "promote",
    tolerance: float = 0.0,
    minimize: bool = False,
    retire_missing: bool = False,
) -> Baseline:
    """Merge `bundle.scores` into the baseline. Returns the written Baseline.

    **Merge semantics, not overwrite** — previously-tracked fixtures that
    are missing from this bundle are preserved by default. This prevents
    a grader that pruned (or simply forgot) fixture `b` from permanently
    erasing `b`'s regression protection. Pass `retire_missing=True` only
    when the grader has intentionally stopped emitting a fixture.

    Per-fixture direction (lower-better vs higher-better) is read from
    each `Score.metadata["minimize"]` when present; otherwise the
    `minimize` argument applies as the task-level default.

    `event` is "set" (first time seed), "promote" (auto on improved
    attempt), or "manual" (CLI `coral regression promote`). Carried
    into history.jsonl for audit; `preserved_fixtures` / `retired_fixtures`
    appear on the history entry when applicable.

    Acquires `_baseline_lock` and re-reads the baseline so concurrent
    callers can't last-writer-wins each other.
    """
    with _baseline_lock(coral_dir):
        existing = read_baseline(coral_dir)
        return _promote_inside_lock(
            coral_dir,
            bundle,
            commit_hash,
            by,
            event=event,
            tolerance=tolerance,
            minimize=minimize,
            retire_missing=retire_missing,
            existing=existing,
        )


def check_and_maybe_promote(
    coral_dir: str | Path,
    bundle: Any,
    commit_hash: str,
    by: str,
    *,
    aggregate_improved: bool,
    tolerance: float = 0.0,
    minimize: bool = False,
) -> RegressionCheck:
    """Atomic check + (optional) seed/promote inside `_baseline_lock`.

    The daemon's `_grade_one` must call this instead of calling
    `check_against_baseline` and `promote` separately — otherwise two
    workers can both pass against the same stale baseline, race on
    `write_baseline`, and the loser's improvements are silently
    dropped.

    Side effects (inside the lock):
      - If no baseline exists AND `aggregate_improved` is True AND
        the bundle has named scores: seed a new baseline (event="set").
      - If a baseline exists, all fixtures hold, AND `aggregate_improved`
        is True: merge the observed scores in as the new baseline
        (event="promote").
      - Otherwise: no write.

    Returns the `RegressionCheck` reflecting the on-disk baseline at
    the moment of decision (which, because of the lock, is the same
    one the promote decision was made against).
    """
    with _baseline_lock(coral_dir):
        baseline = read_baseline(coral_dir)
        observed = _bundle_scores(bundle)

        if baseline is None:
            if observed and aggregate_improved:
                _promote_inside_lock(
                    coral_dir,
                    bundle,
                    commit_hash,
                    by,
                    event="set",
                    tolerance=tolerance,
                    minimize=minimize,
                    retire_missing=False,
                    existing=None,
                )
            return RegressionCheck(status=REGRESSION_CHECK_NONE)

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

        if aggregate_improved:
            _promote_inside_lock(
                coral_dir,
                bundle,
                commit_hash,
                by,
                event="promote",
                tolerance=tolerance,
                minimize=minimize,
                retire_missing=False,
                existing=baseline,
            )

        return RegressionCheck(
            status=REGRESSION_CHECK_PASS,
            missing_fixtures=sorted(missing),
        )


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

    Atomic via `_baseline_lock` — safe to call concurrently with promote
    or check_and_maybe_promote (only the first caller seeds; subsequent
    callers see the existing baseline and return None).
    """
    if not _bundle_scores(bundle):
        return None
    with _baseline_lock(coral_dir):
        if read_baseline(coral_dir) is not None:
            return None
        return _promote_inside_lock(
            coral_dir,
            bundle,
            commit_hash,
            by,
            event="set",
            tolerance=0.0,
            minimize=minimize,
            retire_missing=False,
            existing=None,
        )
