"""Tests for coral.hub.archive and the leaderboard/search filters."""

from __future__ import annotations

import json
from pathlib import Path

from coral.hub.archive import (
    ArchiveCriteria,
    archive_attempt,
    archive_many,
    is_archived,
    select_for_archive,
    unarchive_attempt,
)
from coral.hub.attempts import (
    get_leaderboard,
    get_recent,
    search_attempts,
    write_attempt,
)
from coral.types import Attempt


def _make_attempt(
    commit: str,
    score: float | None = 0.5,
    status: str = "improved",
    title: str = "test",
    agent: str = "agent-1",
    timestamp: str = "2026-05-01T00:00:00+00:00",
) -> Attempt:
    return Attempt(
        commit_hash=commit,
        agent_id=agent,
        title=title,
        score=score,
        status=status,
        parent_hash=None,
        timestamp=timestamp,
        feedback="",
    )


def _setup_attempts_dir(tmp_path: Path) -> Path:
    coral_dir = tmp_path / ".coral"
    (coral_dir / "public" / "attempts").mkdir(parents=True)
    return coral_dir


# --- archive_attempt / unarchive_attempt --------------------------------------


def test_archive_attempt_flips_flag(tmp_path: Path) -> None:
    coral = _setup_attempts_dir(tmp_path)
    write_attempt(str(coral), _make_attempt("a" * 40, score=0.4))
    assert archive_attempt(coral, "a" * 40, reason="low score") is True
    raw = json.loads((coral / "public" / "attempts" / ("a" * 40 + ".json")).read_text())
    assert raw["metadata"]["archived"]["reason"] == "low score"
    assert "at" in raw["metadata"]["archived"]


def test_archive_attempt_idempotent(tmp_path: Path) -> None:
    coral = _setup_attempts_dir(tmp_path)
    write_attempt(str(coral), _make_attempt("a" * 40))
    assert archive_attempt(coral, "a" * 40) is True
    # Second call doesn't flip again — already archived.
    assert archive_attempt(coral, "a" * 40) is False


def test_archive_attempt_unknown_hash(tmp_path: Path) -> None:
    coral = _setup_attempts_dir(tmp_path)
    assert archive_attempt(coral, "missing" * 6) is False


def test_unarchive_attempt(tmp_path: Path) -> None:
    coral = _setup_attempts_dir(tmp_path)
    write_attempt(str(coral), _make_attempt("a" * 40))
    archive_attempt(coral, "a" * 40, reason="r")
    assert unarchive_attempt(coral, "a" * 40) is True
    raw = json.loads((coral / "public" / "attempts" / ("a" * 40 + ".json")).read_text())
    assert "archived" not in raw.get("metadata", {})
    # Idempotent — second call is no-op.
    assert unarchive_attempt(coral, "a" * 40) is False


# --- ArchiveCriteria ----------------------------------------------------------


def test_criteria_score_below(tmp_path: Path) -> None:
    coral = _setup_attempts_dir(tmp_path)
    write_attempt(str(coral), _make_attempt("a" * 40, score=0.2))
    write_attempt(str(coral), _make_attempt("b" * 40, score=0.6))
    matched = select_for_archive(coral, ArchiveCriteria(score_below=0.5))
    assert {a.commit_hash for a in matched} == {"a" * 40}


def test_criteria_before_date(tmp_path: Path) -> None:
    coral = _setup_attempts_dir(tmp_path)
    write_attempt(str(coral), _make_attempt("a" * 40, timestamp="2026-04-01T00:00:00+00:00"))
    write_attempt(str(coral), _make_attempt("b" * 40, timestamp="2026-05-15T00:00:00+00:00"))
    matched = select_for_archive(coral, ArchiveCriteria(before_date="2026-05-01T00:00:00+00:00"))
    assert {a.commit_hash for a in matched} == {"a" * 40}


def test_criteria_status_set(tmp_path: Path) -> None:
    coral = _setup_attempts_dir(tmp_path)
    write_attempt(str(coral), _make_attempt("a" * 40, status="crashed"))
    write_attempt(str(coral), _make_attempt("b" * 40, status="timeout"))
    write_attempt(str(coral), _make_attempt("c" * 40, status="improved"))
    matched = select_for_archive(coral, ArchiveCriteria(status_in=("crashed", "timeout")))
    assert {a.commit_hash for a in matched} == {"a" * 40, "b" * 40}


def test_empty_criteria_matches_nothing(tmp_path: Path) -> None:
    """A safety net: caller forgot to set any predicate."""
    coral = _setup_attempts_dir(tmp_path)
    write_attempt(str(coral), _make_attempt("a" * 40))
    matched = select_for_archive(coral, ArchiveCriteria())
    assert matched == []


def test_criteria_skips_already_archived(tmp_path: Path) -> None:
    coral = _setup_attempts_dir(tmp_path)
    write_attempt(str(coral), _make_attempt("a" * 40, score=0.2))
    archive_attempt(coral, "a" * 40)
    matched = select_for_archive(coral, ArchiveCriteria(score_below=0.5))
    assert matched == []


# --- archive_many -------------------------------------------------------------


def test_archive_many_returns_count(tmp_path: Path) -> None:
    coral = _setup_attempts_dir(tmp_path)
    write_attempt(str(coral), _make_attempt("a" * 40, score=0.2))
    write_attempt(str(coral), _make_attempt("b" * 40, score=0.3))
    matched = select_for_archive(coral, ArchiveCriteria(score_below=0.5))
    n = archive_many(coral, matched, reason="batch")
    assert n == 2


# --- leaderboard / search / get_recent filter ---------------------------------


def test_leaderboard_hides_archived_by_default(tmp_path: Path) -> None:
    coral = _setup_attempts_dir(tmp_path)
    write_attempt(str(coral), _make_attempt("a" * 40, score=0.9))
    write_attempt(str(coral), _make_attempt("b" * 40, score=0.5))
    archive_attempt(coral, "a" * 40)
    top = get_leaderboard(coral)
    assert [a.commit_hash for a in top] == ["b" * 40]


def test_leaderboard_include_archived_brings_back(tmp_path: Path) -> None:
    coral = _setup_attempts_dir(tmp_path)
    write_attempt(str(coral), _make_attempt("a" * 40, score=0.9))
    write_attempt(str(coral), _make_attempt("b" * 40, score=0.5))
    archive_attempt(coral, "a" * 40)
    top = get_leaderboard(coral, include_archived=True)
    assert [a.commit_hash for a in top] == ["a" * 40, "b" * 40]


def test_search_hides_archived_by_default(tmp_path: Path) -> None:
    coral = _setup_attempts_dir(tmp_path)
    write_attempt(str(coral), _make_attempt("a" * 40, title="gradient clip"))
    write_attempt(str(coral), _make_attempt("b" * 40, title="gradient norm"))
    archive_attempt(coral, "a" * 40)
    hits = search_attempts(coral, "gradient")
    assert [a.commit_hash for a in hits] == ["b" * 40]


def test_search_include_archived(tmp_path: Path) -> None:
    coral = _setup_attempts_dir(tmp_path)
    write_attempt(str(coral), _make_attempt("a" * 40, title="gradient clip"))
    archive_attempt(coral, "a" * 40)
    hits = search_attempts(coral, "gradient", include_archived=True)
    assert len(hits) == 1


def test_get_recent_hides_archived(tmp_path: Path) -> None:
    coral = _setup_attempts_dir(tmp_path)
    write_attempt(str(coral), _make_attempt("a" * 40, timestamp="2026-05-10T00:00:00+00:00"))
    write_attempt(str(coral), _make_attempt("b" * 40, timestamp="2026-05-11T00:00:00+00:00"))
    archive_attempt(coral, "b" * 40)
    recent = get_recent(coral, n=10)
    assert [a.commit_hash for a in recent] == ["a" * 40]


# --- is_archived predicate ----------------------------------------------------


def test_is_archived_true_after_archive(tmp_path: Path) -> None:
    coral = _setup_attempts_dir(tmp_path)
    write_attempt(str(coral), _make_attempt("a" * 40))
    archive_attempt(coral, "a" * 40)
    from coral.hub.attempts import read_attempt

    a = read_attempt(coral, "a" * 40)
    assert a is not None
    assert is_archived(a) is True


def test_is_archived_false_otherwise() -> None:
    a = _make_attempt("a" * 40)
    assert is_archived(a) is False
