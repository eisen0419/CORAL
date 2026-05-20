"""Eval submission: git-add, git-commit, write pending attempt, optionally wait.

The grading itself happens asynchronously in the grader daemon
(coral/grader/daemon.py). `submit_eval` only stages+commits, writes a
pending attempt record, and optionally polls for the final score.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from coral.config import CoralConfig
from coral.hooks.pre_commit import (
    SecretsBlockedError,
    enforce_no_secrets,
    unstage_all,
)
from coral.hub.attempts import (
    agent_in_grader_queue,
    count_agent_pending,
    increment_eval_count,
    read_attempt,
    read_eval_count,
    write_attempt,
)
from coral.hub.checkpoint import checkpoint
from coral.types import BUDGET_CLASS_TUNE, Attempt

# Legacy alias — external tests/hooks may still import the underscore-prefixed
# name. Prefer `coral.hub.attempts.increment_eval_count` directly.
_increment_eval_count = increment_eval_count

logger = logging.getLogger(__name__)

# How often submit_eval(wait=True) polls the attempt file for score updates.
_POLL_INTERVAL_SEC = 0.2


def _git_stage_all(workdir: str) -> None:
    """`git add -A`. Raises if the index is empty afterward (nothing to commit)."""
    result = subprocess.run(
        ["git", "add", "-A"],
        capture_output=True,
        text=True,
        cwd=workdir,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git add failed: {result.stderr}")

    status = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        capture_output=True,
        cwd=workdir,
    )
    if status.returncode == 0:
        raise RuntimeError("Nothing to commit — no changes detected.")


def _git_commit_staged(message: str, workdir: str) -> str:
    """Commit the current index. Returns the new commit hash."""
    result = subprocess.run(
        ["git", "commit", "-m", message],
        capture_output=True,
        text=True,
        cwd=workdir,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git commit failed: {result.stderr}")

    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        cwd=workdir,
    )
    return result.stdout.strip()


def _git_add_and_commit(message: str, workdir: str) -> str:
    """Backward-compat wrapper. New code should call `_git_stage_all` +
    secret scan + `_git_commit_staged` directly. This shim does not run the
    secret scan — it's preserved only for older tests that call it directly.
    """
    _git_stage_all(workdir)
    return _git_commit_staged(message, workdir)


def _get_parent_hash(commit_hash: str, cwd: str) -> str | None:
    """Get the parent commit hash."""
    result = subprocess.run(
        ["git", "log", "--format=%P", "-n", "1", commit_hash],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().split()[0]
    return None


def _find_coral_dir(workdir: Path) -> Path | None:
    """Find the shared .coral directory from the .coral_dir breadcrumb file."""
    coral_dir_file = workdir / ".coral_dir"
    if coral_dir_file.exists():
        try:
            return Path(coral_dir_file.read_text().strip()).resolve()
        except (OSError, ValueError):
            pass
    return None


def _poll_until_graded(
    coral_dir: Path,
    commit_hash: str,
    timeout: float,
) -> Attempt:
    """Poll the attempt file until status != 'pending' or timeout elapses.

    Raises TimeoutError if no grader finalizes the attempt within `timeout` seconds.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        attempt = read_attempt(coral_dir, commit_hash)
        if attempt is not None and attempt.status != "pending":
            return attempt
        time.sleep(_POLL_INTERVAL_SEC)
    raise TimeoutError(
        f"Grader did not finalize attempt {commit_hash[:12]} within {timeout:.0f}s "
        f"(is the grader daemon running?)"
    )


def submit_eval(
    message: str,
    agent_id: str,
    workdir: str = ".",
    wait: bool = True,
    poll_timeout: float | None = None,
    tune: bool = False,
    allow_secrets: bool = False,
) -> Attempt:
    """Stage changes, commit with message, write a pending attempt record.

    If ``wait`` is True (default), also polls the attempt file until the
    grader daemon finalizes it (score populated, status != "pending") and
    returns the final Attempt. If False, returns immediately with a pending
    Attempt — the caller (or a future `coral wait` invocation) is responsible
    for observing the final result.

    If ``tune`` is True, the attempt is marked as a tune-mode submission
    (``budget_class="tune"`` on its metadata). The grader still runs and the
    score is recorded, but the manager will not count it toward the agent's
    plateau / heartbeat budget — see issue #73.

    If ``allow_secrets`` is False (default), a pre-commit secret scan runs
    after staging; any credential-shaped content blocks the commit
    (``SecretsBlockedError``) and the staging area is rolled back so the
    agent can fix the leak. Passing True flips to warn mode: hits are logged
    + recorded in ``attempt.metadata["secret_hits"]`` but the commit
    proceeds. Use sparingly — only when a fixture legitimately needs a
    sigil that looks like a credential (e.g. HSM mock with a real-shaped
    PEM header).

    This is the core of `coral eval -m "description"` on the agent side.
    The grader itself runs asynchronously in `coral.grader.daemon`.
    """
    workdir_path = Path(workdir).resolve()

    coral_dir = _find_coral_dir(workdir_path)
    if coral_dir is None:
        raise FileNotFoundError(f"No .coral directory found from {workdir_path}")

    config_path = coral_dir / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"No config.yaml found at {config_path}")
    config = CoralConfig.from_yaml(config_path)

    # Producer-side queue cap: refuse to commit when this agent already has
    # `max_pending_per_agent` ungraded submissions in flight. The grader is
    # serial; without this cap, a slow grader plus a fast agent piles up
    # arbitrarily many pending JSONs (issue #80). 0 = unlimited (legacy).
    pending_limit = config.grader.max_pending_per_agent
    if pending_limit > 0:
        pending_count = count_agent_pending(coral_dir, agent_id)
        if pending_count >= pending_limit:
            oldest = agent_in_grader_queue(coral_dir, agent_id)
            wait_hint = (
                f"Run `coral wait {oldest.commit_hash[:12]}` to block on it "
                f"before submitting again."
                if oldest is not None
                else "Wait for the prior eval to finish before submitting again."
            )
            raise RuntimeError(
                f"You already have {pending_count} pending attempt(s) "
                f"(limit: {pending_limit}). {wait_hint}"
            )

    # Git add (split from commit so we can scan the index for secrets first).
    _git_stage_all(str(workdir_path))

    # Pre-commit secret scan. Default policy is fail-closed; agents pass
    # `allow_secrets=True` (CLI: `--allow-secrets`) only when a fixture
    # legitimately contains a credential-shaped sigil.
    secret_hits: list[str] = []
    try:
        scan = enforce_no_secrets(str(workdir_path), strict=not allow_secrets)
        secret_hits = scan.all_hits
    except SecretsBlockedError:
        # Roll back staging so the agent's working tree is left exactly as
        # it was — they can fix the leak and re-run `coral eval` without
        # having to deal with a half-baked index.
        unstage_all(str(workdir_path))
        raise

    commit_hash = _git_commit_staged(message, str(workdir_path))
    parent_hash = _get_parent_hash(commit_hash, str(workdir_path))

    # Checkpoint shared state at submission time (captures agent's current notes/skills).
    shared_state_hash = checkpoint(str(coral_dir), agent_id, message)

    # Look up parent attempt's shared state hash for provenance chain.
    parent_shared_state_hash = None
    if parent_hash:
        parent_attempt_file = coral_dir / "public" / "attempts" / f"{parent_hash}.json"
        if parent_attempt_file.exists():
            try:
                parent_data = json.loads(parent_attempt_file.read_text())
                parent_shared_state_hash = parent_data.get("shared_state_hash")
            except (json.JSONDecodeError, OSError):
                pass

    # Write pending record. The grader daemon will observe this and fill in
    # score/status/feedback asynchronously.
    metadata: dict[str, object] = {}
    if tune:
        metadata["budget_class"] = BUDGET_CLASS_TUNE
    if secret_hits:
        # Warn-mode allowed the commit through; persist the hits so audit/dashboard
        # can flag the attempt even after the fact.
        metadata["secret_hits"] = secret_hits
    attempt = Attempt(
        commit_hash=commit_hash,
        agent_id=agent_id,
        title=message,
        score=None,
        status="pending",
        parent_hash=parent_hash,
        timestamp=datetime.now(UTC).isoformat(),
        feedback="",
        shared_state_hash=shared_state_hash,
        parent_shared_state_hash=parent_shared_state_hash,
        metadata=metadata,
    )
    write_attempt(str(coral_dir), attempt)

    if not wait:
        return attempt

    # Block until grader daemon finalizes. We give it plenty of slack above the
    # grader's own per-eval timeout so the daemon has room to finish + write back.
    if poll_timeout is None:
        grader_timeout = config.grader.timeout if config.grader.timeout > 0 else 0
        # 2x the grader budget + 60s slack, with a floor of 300s for fast graders.
        poll_timeout = max(grader_timeout * 2 + 60, 300) if grader_timeout else 3600

    final = _poll_until_graded(coral_dir, commit_hash, poll_timeout)

    # Attach eval_count for display by cmd_eval (best-effort; daemon bumps this).
    try:
        final._eval_count = read_eval_count(coral_dir)  # type: ignore[attr-defined]
    except Exception:
        pass

    return final


# Backward-compat alias: older callers / hooks may still import `run_eval`.
# Same semantics as submit_eval(wait=True).
def run_eval(message: str, agent_id: str, workdir: str = ".") -> Attempt:
    """Deprecated. Prefer `submit_eval`. Synchronous (waits for grader)."""
    return submit_eval(message=message, agent_id=agent_id, workdir=workdir, wait=True)
