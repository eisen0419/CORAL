"""Integration tests for the pre-commit secret hook.

Verifies:
- A staged file containing a credential blocks the commit (default fail-closed).
- The staging area is rolled back so the agent's working tree survives.
- `allow_secrets=True` lets the commit through but tags `attempt.metadata.secret_hits`.
- Clean diffs are unaffected.
- Binary blobs in the index don't crash the scanner.
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
from coral.hooks.pre_commit import (
    SecretsBlockedError,
    enforce_no_secrets,
    scan_staged_for_secrets,
)

# Tests use the deprecated eval/grader.py loading path; silence the warning.
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _setup_repo_with_config(base_dir: Path) -> Path:
    """Same fixture as tests/test_hooks.py, kept local so the file is self-contained."""
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
    coral_dir.mkdir()
    (coral_dir / "public" / "attempts").mkdir(parents=True)
    eval_dir = coral_dir / "private" / "eval"
    eval_dir.mkdir(parents=True)
    (repo / ".coral_dir").write_text(str(coral_dir.resolve()))

    (eval_dir / "grader.py").write_text(
        "from coral.grader.task_grader import TaskGrader\n"
        "class Grader(TaskGrader):\n"
        "    def evaluate(self):\n"
        "        return 0.5\n"
    )

    config = {
        "task": {"name": "test_task", "description": "A test"},
        "grader": {},
        "agents": {"count": 1},
        "sharing": {"attempts": True, "notes": True, "skills": True},
        "workspace": {"base_dir": str(repo), "repo_path": str(repo)},
    }
    with open(coral_dir / "config.yaml", "w") as f:
        yaml.dump(config, f)
    return repo


def _head_hash(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


# --- Direct scan against the index -------------------------------------------


def test_scan_clean_when_no_secrets(tmp_path: Path) -> None:
    repo = _setup_repo_with_config(tmp_path)
    (repo / "hello.py").write_text("print('hello world')\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    result = scan_staged_for_secrets(str(repo))
    assert result.clean
    assert result.files == {}


def test_scan_detects_anthropic_key_in_staged_file(tmp_path: Path) -> None:
    repo = _setup_repo_with_config(tmp_path)
    (repo / "leak.py").write_text("ANTHROPIC = 'sk-ant-" + "a" * 30 + "'\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    result = scan_staged_for_secrets(str(repo))
    assert not result.clean
    assert "leak.py" in result.files
    assert "anthropic_key" in result.files["leak.py"]


def test_scan_skips_binary_blobs(tmp_path: Path) -> None:
    """Binary files in the index must not crash the scanner."""
    repo = _setup_repo_with_config(tmp_path)
    (repo / "blob.bin").write_bytes(bytes(range(256)))
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    result = scan_staged_for_secrets(str(repo))
    assert result.clean  # binary was simply skipped


# --- enforce_no_secrets policy ------------------------------------------------


def test_enforce_raises_in_strict_mode(tmp_path: Path) -> None:
    repo = _setup_repo_with_config(tmp_path)
    (repo / "leak.py").write_text("OPENAI = 'sk-proj-" + "x" * 25 + "'\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    with pytest.raises(SecretsBlockedError) as exc:
        enforce_no_secrets(str(repo), strict=True)
    assert "leak.py" in str(exc.value)
    assert "openai_key" in str(exc.value)


def test_enforce_warn_mode_returns_hits(tmp_path: Path) -> None:
    repo = _setup_repo_with_config(tmp_path)
    (repo / "leak.py").write_text("AWS = 'AKIAIOSFODNN7EXAMPLE'\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    result = enforce_no_secrets(str(repo), strict=False)
    assert not result.clean
    assert "aws_access_key" in result.all_hits


# --- submit_eval integration --------------------------------------------------


def test_submit_eval_blocks_commit_on_secret(tmp_path: Path) -> None:
    """Default (fail-closed): a credential in staged content aborts the commit."""
    repo = _setup_repo_with_config(tmp_path)
    sys.path.insert(0, str(repo))
    try:
        baseline_head = _head_hash(repo)
        (repo / "leak.py").write_text("GH = 'ghp_" + "z" * 40 + "'\n")
        with pytest.raises(SecretsBlockedError):
            submit_eval(
                message="leak attempt",
                agent_id="agent-test",
                workdir=str(repo),
                wait=False,
            )
        # No commit happened.
        assert _head_hash(repo) == baseline_head
        # Staging was rolled back (index now matches HEAD).
        diff = subprocess.run(
            ["git", "-C", str(repo), "diff", "--cached", "--name-only"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert diff.stdout.strip() == ""
        # But the working-tree edit survives — the agent doesn't lose work.
        assert (repo / "leak.py").exists()
    finally:
        sys.path.pop(0)


def test_submit_eval_allow_secrets_commits_with_metadata_tag(tmp_path: Path) -> None:
    """`allow_secrets=True` flips to warn mode: commit lands, metadata tagged."""
    repo = _setup_repo_with_config(tmp_path)
    sys.path.insert(0, str(repo))
    try:
        (repo / "fixture.pem").write_text(
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIB...\n-----END RSA PRIVATE KEY-----\n"
        )
        attempt = submit_eval(
            message="add HSM mock fixture",
            agent_id="agent-test",
            workdir=str(repo),
            wait=False,
            allow_secrets=True,
        )
        assert attempt.status == "pending"
        # Persisted JSON carries secret_hits.
        attempt_file = repo / ".coral" / "public" / "attempts" / f"{attempt.commit_hash}.json"
        data = json.loads(attempt_file.read_text())
        assert "private_key_pem" in data["metadata"]["secret_hits"]
    finally:
        sys.path.pop(0)


def test_submit_eval_clean_change_unaffected(tmp_path: Path) -> None:
    """A change with no credentials should pass straight through and grade."""
    repo = _setup_repo_with_config(tmp_path)
    sys.path.insert(0, str(repo))
    try:
        (repo / "hello.py").write_text("print('hello, secret-free world')\n")
        attempt = submit_eval(
            message="no secrets here",
            agent_id="agent-test",
            workdir=str(repo),
            wait=False,
        )
        assert attempt.status == "pending"
        # Sanity: grader still runs.
        process_pending_once(repo / ".coral")
        attempt_file = repo / ".coral" / "public" / "attempts" / f"{attempt.commit_hash}.json"
        data = json.loads(attempt_file.read_text())
        assert data["score"] == 0.5
        assert "secret_hits" not in data["metadata"]
    finally:
        sys.path.pop(0)
