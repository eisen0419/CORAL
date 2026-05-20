"""Tests for coral.hub.failures and the daemon's failure-bundle wiring.

Covers:
- Unit: write_failure_bundle / read_meta / list_bundle_files / stderr truncation.
- Integration via process_pending_once: a crashing grader and a regression
  both produce bundles with the expected `kind` + content.
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
from coral.hub.failures import (
    KIND_CRASHED,
    KIND_REGRESSION,
    MAX_STDERR_BYTES,
    bundle_relpath,
    failure_bundle_dir,
    list_bundle_files,
    read_meta,
    read_stderr_tail,
    write_failure_bundle,
)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# --- Unit ---------------------------------------------------------------------


def test_write_failure_bundle_writes_meta(tmp_path: Path) -> None:
    write_failure_bundle(
        tmp_path,
        "abc123",
        kind=KIND_CRASHED,
        agent_id="agent-1",
        summary="grader exploded",
    )
    meta = read_meta(tmp_path, "abc123")
    assert meta is not None
    assert meta["kind"] == KIND_CRASHED
    assert meta["agent_id"] == "agent-1"
    assert meta["summary"] == "grader exploded"
    assert "timestamp" in meta


def test_write_failure_bundle_with_stderr_and_breakdown(tmp_path: Path) -> None:
    write_failure_bundle(
        tmp_path,
        "abc123",
        kind=KIND_REGRESSION,
        agent_id="agent-1",
        summary="b dropped",
        stderr="line1\nline2\n",
        metric_breakdown={"a": 0.9, "b": 0.4},
        regressed_fixtures=["b"],
    )
    files = list_bundle_files(tmp_path, "abc123")
    names = [p.name for p in files]
    assert "meta.json" in names
    assert "stderr.log" in names
    assert "metric_breakdown.json" in names

    meta = read_meta(tmp_path, "abc123")
    assert meta is not None
    assert meta["regressed_fixtures"] == ["b"]

    breakdown_path = tmp_path / "public" / "failures" / "abc123" / "metric_breakdown.json"
    breakdown = json.loads(breakdown_path.read_text())
    assert breakdown == {"a": 0.9, "b": 0.4}


def test_write_failure_bundle_rejects_unknown_kind(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown failure kind"):
        write_failure_bundle(
            tmp_path,
            "abc",
            kind="whatever",
            agent_id="agent",
            summary="",
        )


def test_truncates_large_stderr(tmp_path: Path) -> None:
    huge = "x" * (MAX_STDERR_BYTES + 5000)
    write_failure_bundle(
        tmp_path,
        "big",
        kind=KIND_CRASHED,
        agent_id="agent",
        summary="size test",
        stderr=huge,
    )
    stderr_path = tmp_path / "public" / "failures" / "big" / "stderr.log"
    content = stderr_path.read_text()
    # Truncated to about MAX_STDERR_BYTES + the trailing notice.
    assert len(content.encode("utf-8")) < MAX_STDERR_BYTES + 200
    assert "truncated" in content


def test_read_meta_returns_none_when_absent(tmp_path: Path) -> None:
    assert read_meta(tmp_path, "nope") is None


def test_list_bundle_files_empty_when_absent(tmp_path: Path) -> None:
    assert list_bundle_files(tmp_path, "nope") == []


def test_read_stderr_tail_returns_last_n_bytes(tmp_path: Path) -> None:
    payload = "\n".join(f"line {i}" for i in range(200))
    write_failure_bundle(
        tmp_path,
        "h",
        kind=KIND_CRASHED,
        agent_id="agent",
        summary="",
        stderr=payload,
    )
    tail = read_stderr_tail(tmp_path, "h", max_bytes=100)
    # Should include the tail of the payload (the higher-numbered lines).
    assert "line 199" in tail
    assert tail.startswith("[...]")


def test_bundle_relpath_format() -> None:
    assert bundle_relpath("abc123") == "failures/abc123"


def test_failure_bundle_dir_creates_lazily(tmp_path: Path) -> None:
    d = failure_bundle_dir(tmp_path, "abc")
    assert d.is_dir()
    assert d.name == "abc"
    assert d.parent.name == "failures"


# --- Integration: daemon writes bundles for crashes + regressions ------------


def _setup_repo(base_dir: Path, grader_body: str) -> Path:
    """Create a CORAL run dir with a custom grader.py body for test."""
    repo = base_dir / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.com"], capture_output=True
    )
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], capture_output=True)

    (repo / "hello.py").write_text("print('hello')\n")
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
    (eval_dir / "grader.py").write_text(grader_body)

    config = {
        "task": {"name": "test_task", "description": "failure tests"},
        "grader": {},
        "agents": {"count": 1},
        "sharing": {"attempts": True, "notes": True, "skills": True},
        "workspace": {"base_dir": str(repo), "repo_path": str(repo)},
    }
    with open(coral_dir / "config.yaml", "w") as f:
        yaml.dump(config, f)
    return repo


def test_grader_crash_writes_bundle_with_stderr(tmp_path: Path) -> None:
    """A grader that raises produces a `crashed` bundle with traceback in stderr.log."""
    grader = (
        "from coral.grader.task_grader import TaskGrader\n"
        "class Grader(TaskGrader):\n"
        "    def evaluate(self):\n"
        "        raise RuntimeError('intentional test crash')\n"
    )
    repo = _setup_repo(tmp_path, grader)
    sys.path.insert(0, str(repo))
    try:
        (repo / "hello.py").write_text("print('crash test')\n")
        pending = submit_eval(message="crash", agent_id="agent-1", workdir=str(repo), wait=False)
        process_pending_once(repo / ".coral")

        attempt = read_attempt(repo / ".coral", pending.commit_hash)
        assert attempt is not None
        assert attempt.status == "crashed"
        # Bundle path stamped onto attempt.
        assert attempt.metadata.get("failure_bundle") == f"failures/{pending.commit_hash}"

        meta = read_meta(repo / ".coral", pending.commit_hash)
        assert meta is not None
        assert meta["kind"] == KIND_CRASHED
        assert "intentional test crash" in meta["summary"]

        tail = read_stderr_tail(repo / ".coral", pending.commit_hash)
        assert "intentional test crash" in tail
        # Traceback present.
        assert "Traceback" in tail or "RuntimeError" in tail
    finally:
        sys.path.pop(0)


def test_regression_writes_bundle_with_breakdown(tmp_path: Path) -> None:
    """A regression attempt produces a `regression` bundle with breakdown + offenders."""
    grader = (
        "import json\n"
        "from pathlib import Path\n"
        "from coral.grader.task_grader import TaskGrader\n"
        "from coral.types import Score, ScoreBundle\n"
        "\n"
        "class Grader(TaskGrader):\n"
        "    def evaluate(self):\n"
        "        scores_file = Path(self.private_dir) / 'eval' / 'next_scores.json'\n"
        "        data = json.loads(scores_file.read_text())\n"
        "        scores = {n: Score(value=float(v), name=n) for n, v in data.items()}\n"
        "        aggregated = sum(s.value for s in scores.values()) / len(scores)\n"
        "        return ScoreBundle(scores=scores, aggregated=aggregated)\n"
    )
    repo = _setup_repo(tmp_path, grader)
    sys.path.insert(0, str(repo))
    try:
        # First: seed baseline at (a=0.8, b=0.9).
        (repo / ".coral" / "private" / "eval" / "next_scores.json").write_text(
            json.dumps({"a": 0.8, "b": 0.9})
        )
        (repo / "hello.py").write_text("print('first')\n")
        submit_eval(message="first", agent_id="agent-1", workdir=str(repo), wait=False)
        process_pending_once(repo / ".coral")

        # Second: drop b → should produce regression + bundle.
        (repo / ".coral" / "private" / "eval" / "next_scores.json").write_text(
            json.dumps({"a": 0.85, "b": 0.5})
        )
        (repo / "hello.py").write_text("print('drop b')\n")
        second = submit_eval(message="drop", agent_id="agent-1", workdir=str(repo), wait=False)
        process_pending_once(repo / ".coral")

        attempt = read_attempt(repo / ".coral", second.commit_hash)
        assert attempt is not None
        assert attempt.status == "regression"
        assert attempt.metadata.get("failure_bundle") == f"failures/{second.commit_hash}"

        meta = read_meta(repo / ".coral", second.commit_hash)
        assert meta is not None
        assert meta["kind"] == KIND_REGRESSION
        assert meta["regressed_fixtures"] == ["b"]

        # Breakdown JSON has both fixtures and the failing value.
        breakdown_path = (
            repo / ".coral" / "public" / "failures" / second.commit_hash / "metric_breakdown.json"
        )
        breakdown = json.loads(breakdown_path.read_text())
        assert breakdown["a"] == 0.85
        assert breakdown["b"] == 0.5
    finally:
        sys.path.pop(0)


def test_clean_attempt_writes_no_bundle(tmp_path: Path) -> None:
    """An attempt that grades cleanly should not produce a failure bundle."""
    grader = (
        "from coral.grader.task_grader import TaskGrader\n"
        "class Grader(TaskGrader):\n"
        "    def evaluate(self):\n"
        "        return 0.75\n"
    )
    repo = _setup_repo(tmp_path, grader)
    sys.path.insert(0, str(repo))
    try:
        (repo / "hello.py").write_text("print('clean')\n")
        pending = submit_eval(message="clean", agent_id="agent-1", workdir=str(repo), wait=False)
        process_pending_once(repo / ".coral")

        attempt = read_attempt(repo / ".coral", pending.commit_hash)
        assert attempt is not None
        assert attempt.status == "improved"
        assert "failure_bundle" not in attempt.metadata
        assert list_bundle_files(repo / ".coral", pending.commit_hash) == []
    finally:
        sys.path.pop(0)


# --- TaskGrader.write_failure_log helper -------------------------------------


def test_task_grader_write_failure_log_drops_file(tmp_path: Path) -> None:
    """Even on a passing attempt, grader-authored log lands in failures/<hash>/."""
    grader = (
        "from coral.grader.task_grader import TaskGrader\n"
        "class Grader(TaskGrader):\n"
        "    def evaluate(self):\n"
        "        self.write_failure_log('diag.txt', 'observed: foo=bar')\n"
        "        return 0.5\n"
    )
    repo = _setup_repo(tmp_path, grader)
    sys.path.insert(0, str(repo))
    try:
        (repo / "hello.py").write_text("print('helper')\n")
        pending = submit_eval(message="helper", agent_id="agent-1", workdir=str(repo), wait=False)
        process_pending_once(repo / ".coral")

        # Bundle file exists, even though daemon didn't tag this attempt as failed.
        diag = repo / ".coral" / "public" / "failures" / pending.commit_hash / "diag.txt"
        assert diag.exists()
        assert "foo=bar" in diag.read_text()
    finally:
        sys.path.pop(0)
