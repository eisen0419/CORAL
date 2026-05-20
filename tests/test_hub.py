"""Tests for hub (attempts, notes, skills)."""

import tempfile
from pathlib import Path

from coral.hub.attempts import (
    format_leaderboard,
    get_agent_attempts,
    get_leaderboard,
    per_agent_class_counts,
    read_attempts,
    search_attempts,
    write_attempt,
)
from coral.hub.notes import format_notes_list, get_recent_notes, list_notes, read_note, search_notes
from coral.hub.skills import get_skill_tree, list_skills, read_skill
from coral.types import Attempt


def _make_attempt(
    commit: str, agent: str = "agent-1", score: float = 0.5, title: str = "test"
) -> Attempt:
    return Attempt(
        commit_hash=commit,
        agent_id=agent,
        title=title,
        score=score,
        status="improved",
        parent_hash=None,
        timestamp="2026-03-11T10:00:00Z",
    )


def test_attempts_crud():
    with tempfile.TemporaryDirectory() as d:
        a1 = _make_attempt("aaa111", score=0.8, title="approach A")
        a2 = _make_attempt("bbb222", agent="agent-2", score=0.6, title="approach B")

        write_attempt(d, a1)
        write_attempt(d, a2)

        all_attempts = read_attempts(d)
        assert len(all_attempts) == 2


def test_leaderboard():
    with tempfile.TemporaryDirectory() as d:
        write_attempt(d, _make_attempt("a", score=0.3))
        write_attempt(d, _make_attempt("b", score=0.9))
        write_attempt(d, _make_attempt("c", score=0.6))

        top = get_leaderboard(d, top_n=2)
        assert len(top) == 2
        assert top[0].score == 0.9
        assert top[1].score == 0.6


def test_agent_filter():
    with tempfile.TemporaryDirectory() as d:
        write_attempt(d, _make_attempt("a", agent="agent-1"))
        write_attempt(d, _make_attempt("b", agent="agent-2"))
        write_attempt(d, _make_attempt("c", agent="agent-1"))

        agent1 = get_agent_attempts(d, "agent-1")
        assert len(agent1) == 2


def test_search():
    with tempfile.TemporaryDirectory() as d:
        write_attempt(d, _make_attempt("a", title="learning rate tuning"))
        write_attempt(d, _make_attempt("b", title="attention heads"))
        write_attempt(d, _make_attempt("c", title="learning rate schedule"))

        results = search_attempts(d, "learning rate")
        assert len(results) == 2


def test_format_leaderboard():
    attempts = [_make_attempt("a", score=0.9), _make_attempt("b", score=0.5)]
    md = format_leaderboard(attempts)
    assert "Rank" in md
    assert "0.9000" in md


def test_format_leaderboard_shows_class_column():
    """The Class column distinguishes real / tune / error attempts at a glance."""
    real = _make_attempt("aaa", score=0.9, title="real-row")
    tune = _make_attempt("bbb", score=0.5, title="tune-row")
    tune.metadata["budget_class"] = "tune"
    err = _make_attempt("ccc", score=0.3, title="error-row")
    err.metadata["budget_class"] = "grader_error"

    md = format_leaderboard([real, tune, err])
    assert "Class" in md
    # Per-row class labels appear in the table body.
    real_line = next(line for line in md.splitlines() if "real-row" in line)
    tune_line = next(line for line in md.splitlines() if "tune-row" in line)
    err_line = next(line for line in md.splitlines() if "error-row" in line)
    assert " real " in real_line
    assert " tune " in tune_line
    # grader_error is rendered as compact "error" to keep the column narrow.
    assert " error " in err_line
    assert "grader_error" not in err_line


def test_per_agent_class_counts_splits_by_budget_class():
    """Budget class counts are tallied per agent (issue #73)."""
    with tempfile.TemporaryDirectory() as d:
        # agent-1: 2 real, 1 grader_error, 1 tune
        a = _make_attempt("aaa", agent="agent-1")
        b = _make_attempt("bbb", agent="agent-1")
        c = _make_attempt("ccc", agent="agent-1")
        c.metadata["budget_class"] = "grader_error"
        c.status = "timeout"
        d_att = _make_attempt("ddd", agent="agent-1")
        d_att.metadata["budget_class"] = "tune"

        # agent-2: 1 real
        e = _make_attempt("eee", agent="agent-2")

        for att in (a, b, c, d_att, e):
            write_attempt(d, att)

        counts = per_agent_class_counts(d)
        assert counts["agent-1"] == {"real": 2, "grader_error": 1, "tune": 1}
        assert counts["agent-2"] == {"real": 1}


def test_per_agent_class_counts_skips_pending():
    """Pending attempts have no final classification — exclude from tallies."""
    with tempfile.TemporaryDirectory() as d:
        scored = _make_attempt("aaa", agent="agent-1")
        pending = _make_attempt("bbb", agent="agent-1")
        pending.status = "pending"
        pending.score = None

        write_attempt(d, scored)
        write_attempt(d, pending)

        counts = per_agent_class_counts(d)
        assert counts["agent-1"] == {"real": 1}


def test_notes():
    with tempfile.TemporaryDirectory() as d:
        # Write notes in public/notes/notes.md
        (Path(d) / "public" / "notes").mkdir(parents=True)
        notes_file = Path(d) / "public" / "notes" / "notes.md"
        notes_file.write_text(
            "## [2026-03-11] ReLU works better\n"
            "Details about ReLU activation...\n"
            "\n"
            "## [2026-03-11] Learning rate 0.001 is optimal\n"
            "Tried various learning rates...\n"
        )

        entries = list_notes(d)
        assert len(entries) == 2
        assert entries[0]["title"] == "ReLU works better"
        assert entries[1]["title"] == "Learning rate 0.001 is optimal"

        # Read specific entry
        content = read_note(d, 1)
        assert content is not None
        assert "ReLU" in content
        assert "Details" in content

        # Search
        results = search_notes(d, "learning rate")
        assert len(results) == 1
        assert results[0]["title"] == "Learning rate 0.001 is optimal"

        # Recent
        recent = get_recent_notes(d, n=1)
        assert len(recent) == 1
        assert recent[0]["title"] == "Learning rate 0.001 is optimal"

        # Format
        formatted = format_notes_list(entries)
        assert "ReLU" in formatted
        assert "Learning rate" in formatted


def test_notes_empty():
    with tempfile.TemporaryDirectory() as d:
        entries = list_notes(d)
        assert entries == []
        assert format_notes_list(entries) == "No notes yet."


def test_skills():
    with tempfile.TemporaryDirectory() as d:
        skill_dir = Path(d) / "public" / "skills" / "my_tool"
        skill_dir.mkdir(parents=True)
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()

        (skill_dir / "SKILL.md").write_text(
            "---\nname: my_tool\ndescription: A useful tool\ncreator: agent-1\n---\n# My Tool\nUsage..."
        )
        (scripts_dir / "run.py").write_text("print('hello')")

        skills = list_skills(d)
        assert len(skills) == 1
        assert skills[0]["name"] == "my_tool"

        info = read_skill(str(skill_dir))
        assert "run.py" in str(info["files"])
        assert "Usage" in info["body"]

        tree = get_skill_tree(str(skill_dir))
        assert "SKILL.md" in tree


# --- mtime cache for read_attempts / read_attempt ----------------------------


def test_mtime_cache_returns_equal_when_file_unchanged(tmp_path: Path) -> None:
    """Repeated reads of an unchanged file return equal (not identical) Attempts.

    Codex r1 P2 fix: the cache returns a defensive clone, not the stored
    instance. Callers may mutate the result without poisoning the cache.
    """
    from coral.hub.attempts import (
        clear_attempt_cache,
        read_attempt,
        write_attempt,
    )
    from coral.types import Attempt

    clear_attempt_cache()
    coral = tmp_path / ".coral"
    (coral / "public" / "attempts").mkdir(parents=True)
    attempt = Attempt(
        commit_hash="a" * 40,
        agent_id="agent-1",
        title="test",
        score=0.7,
        status="improved",
        parent_hash=None,
        timestamp="2026-05-20T00:00:00+00:00",
        metadata={"k": "v"},
    )
    write_attempt(str(coral), attempt)

    first = read_attempt(coral, "a" * 40)
    second = read_attempt(coral, "a" * 40)
    assert first is not None and second is not None
    # Different instances (clone), but equal values.
    assert first is not second
    assert first.to_dict() == second.to_dict()


def test_mtime_cache_mutation_does_not_poison_cache(tmp_path: Path) -> None:
    """Codex r1 P2: mutating a returned Attempt must not affect future reads."""
    from coral.hub.attempts import clear_attempt_cache, read_attempt, write_attempt
    from coral.types import Attempt

    clear_attempt_cache()
    coral = tmp_path / ".coral"
    (coral / "public" / "attempts").mkdir(parents=True)
    write_attempt(
        str(coral),
        Attempt(
            commit_hash="e" * 40,
            agent_id="agent-1",
            title="orig",
            score=0.5,
            status="improved",
            parent_hash=None,
            timestamp="2026-05-20T00:00:00+00:00",
            metadata={"untouched": True},
        ),
    )
    first = read_attempt(coral, "e" * 40)
    assert first is not None
    # Mutate the returned object.
    first.metadata["polluted"] = "yes"
    first.title = "MUTATED"
    # Subsequent read must NOT see the mutation.
    second = read_attempt(coral, "e" * 40)
    assert second is not None
    assert "polluted" not in second.metadata
    assert second.title == "orig"


def test_mtime_cache_invalidates_on_write(tmp_path: Path) -> None:
    """After write_attempt, a subsequent read sees the new content."""
    import time

    from coral.hub.attempts import clear_attempt_cache, read_attempt, write_attempt
    from coral.types import Attempt

    clear_attempt_cache()
    coral = tmp_path / ".coral"
    (coral / "public" / "attempts").mkdir(parents=True)
    a1 = Attempt(
        commit_hash="b" * 40,
        agent_id="agent-1",
        title="v1",
        score=0.5,
        status="improved",
        parent_hash=None,
        timestamp="2026-05-20T00:00:00+00:00",
    )
    write_attempt(str(coral), a1)
    first = read_attempt(coral, "b" * 40)
    assert first is not None
    assert first.title == "v1"

    # Sleep just long enough to guarantee a different mtime even on
    # filesystems with coarse mtime granularity. macOS HFS+ has ~1s
    # granularity; APFS / Linux ext4 are ns.
    time.sleep(0.01)

    a2 = Attempt(
        commit_hash="b" * 40,
        agent_id="agent-1",
        title="v2",  # changed
        score=0.9,  # changed
        status="improved",
        parent_hash=None,
        timestamp="2026-05-20T00:00:00+00:00",
    )
    write_attempt(str(coral), a2)
    second = read_attempt(coral, "b" * 40)
    assert second is not None
    assert second.title == "v2"
    assert second.score == 0.9


def test_mtime_cache_handles_deleted_file(tmp_path: Path) -> None:
    """Deleting the file after a cached read returns None on next read."""
    import os

    from coral.hub.attempts import _ATTEMPT_CACHE, clear_attempt_cache, read_attempt, write_attempt
    from coral.types import Attempt

    clear_attempt_cache()
    coral = tmp_path / ".coral"
    (coral / "public" / "attempts").mkdir(parents=True)
    attempt = Attempt(
        commit_hash="c" * 40,
        agent_id="agent-1",
        title="test",
        score=0.7,
        status="improved",
        parent_hash=None,
        timestamp="2026-05-20T00:00:00+00:00",
    )
    write_attempt(str(coral), attempt)
    read_attempt(coral, "c" * 40)
    # Cache populated.
    path = coral / "public" / "attempts" / ("c" * 40 + ".json")
    assert str(path) in _ATTEMPT_CACHE

    os.remove(path)
    result = read_attempt(coral, "c" * 40)
    assert result is None
    # Cache entry was dropped on the failed stat.
    assert str(path) not in _ATTEMPT_CACHE


def test_mtime_cache_handles_malformed_json(tmp_path: Path) -> None:
    """A file that becomes invalid JSON returns None and drops the cache entry."""
    from coral.hub.attempts import _ATTEMPT_CACHE, clear_attempt_cache, read_attempt, write_attempt
    from coral.types import Attempt

    clear_attempt_cache()
    coral = tmp_path / ".coral"
    (coral / "public" / "attempts").mkdir(parents=True)
    write_attempt(
        str(coral),
        Attempt(
            commit_hash="d" * 40,
            agent_id="agent-1",
            title="ok",
            score=0.5,
            status="improved",
            parent_hash=None,
            timestamp="2026-05-20T00:00:00+00:00",
        ),
    )
    read_attempt(coral, "d" * 40)
    path = coral / "public" / "attempts" / ("d" * 40 + ".json")
    assert str(path) in _ATTEMPT_CACHE

    # Corrupt the file (this rewrite changes mtime; cache should miss + re-parse fail).
    import time

    time.sleep(0.01)
    path.write_text("not valid json {{{")
    result = read_attempt(coral, "d" * 40)
    assert result is None
    # Cache entry was cleared by the failed parse.
    assert str(path) not in _ATTEMPT_CACHE


def test_read_attempts_cached_path_returns_equal_results(tmp_path: Path) -> None:
    """Multiple read_attempts() calls on an unchanged dir return equal lists.

    Returns defensive clones (Codex r1 P2 fix), so identity != ; but the
    parsed values are equal across calls.
    """
    from coral.hub.attempts import clear_attempt_cache, read_attempts, write_attempt
    from coral.types import Attempt

    clear_attempt_cache()
    coral = tmp_path / ".coral"
    (coral / "public" / "attempts").mkdir(parents=True)
    for i in range(5):
        write_attempt(
            str(coral),
            Attempt(
                commit_hash=f"{i:040x}",
                agent_id=f"agent-{i % 2}",
                title=f"a{i}",
                score=0.5 + i * 0.05,
                status="improved",
                parent_hash=None,
                timestamp="2026-05-20T00:00:00+00:00",
            ),
        )
    first = read_attempts(coral)
    second = read_attempts(coral)
    assert len(first) == 5 and len(second) == 5
    # Equal values across calls (not identity — see _clone_attempt).
    for a, b in zip(first, second, strict=True):
        assert a.to_dict() == b.to_dict()


def test_stat_signature_catches_inode_swap(tmp_path: Path) -> None:
    """Codex r1 P1: atomic rename swaps inode → cache invalidates even if mtime is identical."""
    import json as _json
    import os
    import tempfile as _tempfile

    from coral.hub.attempts import clear_attempt_cache, read_attempt, write_attempt
    from coral.types import Attempt

    clear_attempt_cache()
    coral = tmp_path / ".coral"
    (coral / "public" / "attempts").mkdir(parents=True)
    a1 = Attempt(
        commit_hash="f" * 40,
        agent_id="agent-1",
        title="v1",
        score=0.5,
        status="improved",
        parent_hash=None,
        timestamp="2026-05-20T00:00:00+00:00",
    )
    write_attempt(str(coral), a1)
    first = read_attempt(coral, "f" * 40)
    assert first is not None and first.title == "v1"

    # Force a same-mtime rename to a NEW inode by manually crafting the
    # tmp+rename sequence and explicitly setting the new file's mtime
    # to match the old one.
    target = coral / "public" / "attempts" / ("f" * 40 + ".json")
    old_stat = target.stat()
    a2 = Attempt(
        commit_hash="f" * 40,
        agent_id="agent-1",
        title="v2-inode-swap",
        score=0.9,
        status="improved",
        parent_hash=None,
        timestamp="2026-05-20T00:00:00+00:00",
    )
    fd, tmp_str = _tempfile.mkstemp(prefix=".swap.", suffix=".json.tmp", dir=str(target.parent))
    with os.fdopen(fd, "w") as f:
        f.write(_json.dumps(a2.to_dict()))
    os.replace(tmp_str, target)
    # Force mtime back to the old value to simulate the coarse-mtime race.
    os.utime(target, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))

    # New inode → different signature → cache invalidates.
    second = read_attempt(coral, "f" * 40)
    assert second is not None
    assert second.title == "v2-inode-swap"
    assert second.score == 0.9
