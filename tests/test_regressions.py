"""Tests for coral.hub.regressions and daemon integration.

Unit tests cover the I/O + comparison core (fast, no git/daemon).
Integration tests drive a real grader through `process_pending_once`
and verify baseline auto-seed / promote / regression-block / status flip.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from coral.grader.daemon import process_pending_once
from coral.hooks.post_commit import submit_eval
from coral.hub.attempts import read_attempt
from coral.hub.regressions import (
    REGRESSION_CHECK_FAIL,
    REGRESSION_CHECK_NONE,
    REGRESSION_CHECK_PASS,
    REGRESSION_CHECK_SKIPPED,
    REGRESSION_STATUS,
    Baseline,
    FixtureBaseline,
    check_against_baseline,
    clear_baseline,
    maybe_seed_baseline,
    promote,
    read_baseline,
    write_baseline,
)
from coral.types import Score, ScoreBundle

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# --- FixtureBaseline.regressed ------------------------------------------------


def test_fixture_baseline_higher_better_strict() -> None:
    fb = FixtureBaseline(score=0.8, tolerance=0.0, minimize=False)
    assert not fb.regressed(0.85)
    assert not fb.regressed(0.8)
    assert fb.regressed(0.79)


def test_fixture_baseline_higher_better_with_tolerance() -> None:
    fb = FixtureBaseline(score=0.8, tolerance=0.05, minimize=False)
    assert not fb.regressed(0.78)  # within tolerance
    assert not fb.regressed(0.75)  # exactly at the floor
    assert fb.regressed(0.74)


def test_fixture_baseline_lower_better() -> None:
    fb = FixtureBaseline(score=0.1, tolerance=0.0, minimize=True)
    assert not fb.regressed(0.05)
    assert not fb.regressed(0.1)
    assert fb.regressed(0.11)


# --- Baseline I/O roundtrip ---------------------------------------------------


def test_read_baseline_returns_none_when_unset(tmp_path: Path) -> None:
    (tmp_path / "public").mkdir()
    assert read_baseline(tmp_path) is None


def test_write_and_read_baseline_roundtrip(tmp_path: Path) -> None:
    b = Baseline(
        baseline_commit="abc123",
        set_at="2026-05-19T00:00:00+00:00",
        set_by="agent-1",
        fixtures={
            "fix-a": FixtureBaseline(score=0.8, tolerance=0.02, minimize=False),
            "fix-b": FixtureBaseline(score=0.9),
        },
    )
    write_baseline(tmp_path, b)
    loaded = read_baseline(tmp_path)
    assert loaded is not None
    assert loaded.baseline_commit == "abc123"
    assert loaded.set_by == "agent-1"
    assert len(loaded.fixtures) == 2
    assert loaded.fixtures["fix-a"].score == 0.8
    assert loaded.fixtures["fix-a"].tolerance == 0.02
    assert loaded.fixtures["fix-b"].minimize is False


def test_clear_baseline_appends_history(tmp_path: Path) -> None:
    b = Baseline("h1", "2026-05-19T00:00:00+00:00", "agent-1", {})
    write_baseline(tmp_path, b)
    assert clear_baseline(tmp_path) is True
    assert read_baseline(tmp_path) is None
    # Second clear is no-op.
    assert clear_baseline(tmp_path) is False
    # History file has exactly one entry.
    history = (
        (tmp_path / "public" / "regressions" / "history.jsonl").read_text().strip().splitlines()
    )
    assert len(history) == 1
    assert json.loads(history[0])["event"] == "reset"


# --- check_against_baseline ---------------------------------------------------


def _bundle(scores_dict: dict[str, float], aggregated: float | None = None) -> ScoreBundle:
    if aggregated is None and scores_dict:
        aggregated = sum(scores_dict.values()) / len(scores_dict)
    return ScoreBundle(
        scores={n: Score(value=v, name=n) for n, v in scores_dict.items()},
        aggregated=aggregated,
    )


def test_check_none_when_no_baseline(tmp_path: Path) -> None:
    result = check_against_baseline(tmp_path, _bundle({"a": 0.5}))
    assert result.status == REGRESSION_CHECK_NONE


def test_check_pass_when_all_fixtures_meet_baseline(tmp_path: Path) -> None:
    write_baseline(
        tmp_path,
        Baseline(
            "h1",
            "t",
            "agent",
            fixtures={"a": FixtureBaseline(0.8), "b": FixtureBaseline(0.9)},
        ),
    )
    result = check_against_baseline(tmp_path, _bundle({"a": 0.85, "b": 0.95}))
    assert result.status == REGRESSION_CHECK_PASS
    assert result.regressed_fixtures == []


def test_check_fail_lists_offenders(tmp_path: Path) -> None:
    write_baseline(
        tmp_path,
        Baseline(
            "h1",
            "t",
            "agent",
            fixtures={
                "a": FixtureBaseline(0.8),
                "b": FixtureBaseline(0.9),
                "c": FixtureBaseline(0.7),
            },
        ),
    )
    # b and c both drop below baseline.
    result = check_against_baseline(tmp_path, _bundle({"a": 0.85, "b": 0.5, "c": 0.6}))
    assert result.status == REGRESSION_CHECK_FAIL
    assert result.regressed_fixtures == ["b", "c"]


def test_check_skipped_when_bundle_has_no_named_scores(tmp_path: Path) -> None:
    write_baseline(
        tmp_path,
        Baseline("h1", "t", "agent", fixtures={"a": FixtureBaseline(0.8)}),
    )
    # Empty scores dict.
    result = check_against_baseline(tmp_path, _bundle({}))
    assert result.status == REGRESSION_CHECK_SKIPPED


def test_check_records_missing_fixtures(tmp_path: Path) -> None:
    write_baseline(
        tmp_path,
        Baseline(
            "h1",
            "t",
            "agent",
            fixtures={"a": FixtureBaseline(0.8), "b": FixtureBaseline(0.9)},
        ),
    )
    # Bundle dropped fixture "b" entirely.
    result = check_against_baseline(tmp_path, _bundle({"a": 0.85}))
    assert result.status == REGRESSION_CHECK_PASS  # missing isn't a hard fail
    assert result.missing_fixtures == ["b"]


# --- promote + maybe_seed_baseline -------------------------------------------


def test_maybe_seed_baseline_writes_when_unset(tmp_path: Path) -> None:
    bundle = _bundle({"a": 0.8, "b": 0.9})
    seeded = maybe_seed_baseline(tmp_path, bundle, commit_hash="hash1", by="agent-1")
    assert seeded is not None
    on_disk = read_baseline(tmp_path)
    assert on_disk is not None
    assert on_disk.baseline_commit == "hash1"
    assert sorted(on_disk.fixtures.keys()) == ["a", "b"]


def test_maybe_seed_baseline_noop_when_already_set(tmp_path: Path) -> None:
    write_baseline(
        tmp_path,
        Baseline("first", "t", "agent", {"a": FixtureBaseline(0.5)}),
    )
    seeded = maybe_seed_baseline(tmp_path, _bundle({"a": 0.9}), "later", "agent")
    assert seeded is None
    # Existing baseline untouched.
    on_disk = read_baseline(tmp_path)
    assert on_disk is not None
    assert on_disk.baseline_commit == "first"
    assert on_disk.fixtures["a"].score == 0.5


def test_promote_overwrites_baseline_and_appends_history(tmp_path: Path) -> None:
    write_baseline(tmp_path, Baseline("old", "t", "agent", {"a": FixtureBaseline(0.5)}))
    promote(
        tmp_path, _bundle({"a": 0.9, "b": 0.7}), commit_hash="new", by="agent-2", event="manual"
    )
    on_disk = read_baseline(tmp_path)
    assert on_disk is not None
    assert on_disk.baseline_commit == "new"
    assert on_disk.set_by == "agent-2"
    assert on_disk.fixtures["a"].score == 0.9
    assert on_disk.fixtures["b"].score == 0.7
    history = (
        (tmp_path / "public" / "regressions" / "history.jsonl").read_text().strip().splitlines()
    )
    assert len(history) == 1
    entry = json.loads(history[0])
    assert entry["event"] == "manual"
    assert entry["commit"] == "new"


# --- Integration: daemon picks up the baseline + flips status ----------------


def _setup_repo_with_multifixture_grader(base_dir: Path, scores_to_emit: dict[str, float]) -> Path:
    """Build a test run-dir whose grader returns a multi-fixture ScoreBundle.

    `scores_to_emit` controls per-fixture values; tests can edit
    `.coral/private/eval/next_scores.json` between calls to drive sequenced
    behavior (baseline seed → improved → regression).
    """
    repo = base_dir / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.com"], capture_output=True
    )
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], capture_output=True)

    (repo / "hello.py").write_text("print('v0')\n")
    (repo / ".gitignore").write_text(".coral/\n.coral_dir\n.claude/\n.coral_agent_id\nCLAUDE.md\n")
    subprocess.run(["git", "-C", str(repo), "add", "hello.py", ".gitignore"], capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "Initial"], capture_output=True, check=True
    )

    coral_dir = repo / ".coral"
    (coral_dir / "public" / "attempts").mkdir(parents=True)
    eval_dir = coral_dir / "private" / "eval"
    eval_dir.mkdir(parents=True)
    (repo / ".coral_dir").write_text(str(coral_dir.resolve()))

    # Multi-fixture grader: reads scores from next_scores.json each invocation.
    (eval_dir / "next_scores.json").write_text(json.dumps(scores_to_emit))
    (eval_dir / "grader.py").write_text(
        """import json
from pathlib import Path
from coral.grader.task_grader import TaskGrader
from coral.types import Score, ScoreBundle


class Grader(TaskGrader):
    def evaluate(self):
        scores_file = Path(self.private_dir) / "eval" / "next_scores.json"
        data = json.loads(scores_file.read_text())
        scores = {n: Score(value=float(v), name=n) for n, v in data.items()}
        aggregated = sum(s.value for s in scores.values()) / len(scores)
        return ScoreBundle(scores=scores, aggregated=aggregated)
"""
    )

    config = {
        "task": {"name": "test_task", "description": "Multi-fixture test"},
        "grader": {},
        "agents": {"count": 1},
        "sharing": {"attempts": True, "notes": True, "skills": True},
        "workspace": {"base_dir": str(repo), "repo_path": str(repo)},
    }
    with open(coral_dir / "config.yaml", "w") as f:
        yaml.dump(config, f)
    return repo


def _set_next_scores(repo: Path, scores: dict[str, float]) -> None:
    (repo / ".coral" / "private" / "eval" / "next_scores.json").write_text(json.dumps(scores))


def _submit_and_grade(message: str, agent_id: str, repo: Path) -> str:
    """Run submit_eval + drain. Returns the commit hash so the test can read back."""
    pending = submit_eval(message=message, agent_id=agent_id, workdir=str(repo), wait=False)
    process_pending_once(repo / ".coral")
    return pending.commit_hash


def test_first_attempt_seeds_baseline(tmp_path: Path) -> None:
    repo = _setup_repo_with_multifixture_grader(tmp_path, {"a": 0.8, "b": 0.9})
    sys.path.insert(0, str(repo))
    try:
        (repo / "hello.py").write_text("print('first')\n")
        commit = _submit_and_grade("first", "agent-1", repo)
        attempt = read_attempt(repo / ".coral", commit)
        assert attempt is not None
        assert attempt.status == "improved"
        # Baseline was seeded from this attempt.
        baseline = read_baseline(repo / ".coral")
        assert baseline is not None
        assert baseline.baseline_commit == commit
        assert sorted(baseline.fixtures.keys()) == ["a", "b"]
        # Metadata captures the check outcome (none — no baseline at scan time).
        assert attempt.metadata["regression_check"] == REGRESSION_CHECK_NONE
        # Score breakdown was persisted into the attempt.
        assert attempt.metadata["score_breakdown"] == {"a": 0.8, "b": 0.9}
    finally:
        sys.path.pop(0)


def test_improved_attempt_promotes_baseline(tmp_path: Path) -> None:
    repo = _setup_repo_with_multifixture_grader(tmp_path, {"a": 0.8, "b": 0.9})
    sys.path.insert(0, str(repo))
    try:
        (repo / "hello.py").write_text("print('first')\n")
        _submit_and_grade("first", "agent-1", repo)

        # Higher scores on both fixtures → status "improved" → baseline promoted.
        _set_next_scores(repo, {"a": 0.85, "b": 0.95})
        (repo / "hello.py").write_text("print('better')\n")
        commit2 = _submit_and_grade("better", "agent-1", repo)
        attempt2 = read_attempt(repo / ".coral", commit2)
        assert attempt2 is not None
        assert attempt2.status == "improved"
        assert attempt2.metadata["regression_check"] == REGRESSION_CHECK_PASS

        baseline = read_baseline(repo / ".coral")
        assert baseline is not None
        assert baseline.baseline_commit == commit2
        assert baseline.fixtures["a"].score == 0.85
        assert baseline.fixtures["b"].score == 0.95
    finally:
        sys.path.pop(0)


def test_regression_attempt_blocks_with_status_and_metadata(tmp_path: Path) -> None:
    repo = _setup_repo_with_multifixture_grader(tmp_path, {"a": 0.8, "b": 0.9})
    sys.path.insert(0, str(repo))
    try:
        (repo / "hello.py").write_text("print('first')\n")
        _submit_and_grade("first", "agent-1", repo)

        # Fixture "b" drops from 0.9 to 0.6 — regression even if aggregate didn't tank.
        _set_next_scores(repo, {"a": 0.85, "b": 0.6})
        (repo / "hello.py").write_text("print('broke b')\n")
        commit2 = _submit_and_grade("broke b", "agent-1", repo)
        attempt2 = read_attempt(repo / ".coral", commit2)
        assert attempt2 is not None
        # Status was forced to "regression" — overrides whatever _compute_status said.
        assert attempt2.status == REGRESSION_STATUS
        assert attempt2.metadata["regression_check"] == REGRESSION_CHECK_FAIL
        assert attempt2.metadata["regressed_fixtures"] == ["b"]
        # The original baseline survives (regression doesn't promote).
        baseline = read_baseline(repo / ".coral")
        assert baseline is not None
        assert baseline.fixtures["b"].score == 0.9
    finally:
        sys.path.pop(0)


def test_no_promote_when_aggregate_same(tmp_path: Path) -> None:
    """Equal-score attempts produce status 'baseline', not 'improved', so the
    baseline file should not be touched."""
    repo = _setup_repo_with_multifixture_grader(tmp_path, {"a": 0.8, "b": 0.9})
    sys.path.insert(0, str(repo))
    try:
        (repo / "hello.py").write_text("print('first')\n")
        first = _submit_and_grade("first", "agent-1", repo)
        baseline_before = read_baseline(repo / ".coral")

        # Identical scores → status "baseline".
        (repo / "hello.py").write_text("print('same')\n")
        commit2 = _submit_and_grade("same", "agent-1", repo)
        attempt2 = read_attempt(repo / ".coral", commit2)
        assert attempt2 is not None
        assert attempt2.status == "baseline"
        # Baseline file unchanged.
        baseline_after = read_baseline(repo / ".coral")
        assert baseline_after is not None
        assert baseline_after.baseline_commit == first
        assert baseline_before is not None
        assert baseline_after.baseline_commit == baseline_before.baseline_commit
    finally:
        sys.path.pop(0)
