"""Failure bundles: structured diagnostics for failed / regressed attempts.

Companion to `coral.hub.regressions`. Where regression memory tells the
agent *what they broke* (which fixture fell below baseline), failure
bundles tell them *why* — stderr, traces, per-fixture breakdowns,
timeout flags. Together they form the "read the failure → edit policy"
loop from Trinkle's "Learning Beyond Gradients" paper.

Layout under `.coral/public/failures/<commit_hash>/`:

- `meta.json`        — kind, agent_id, timestamp, summary, exit_code.
- `stderr.log`       — full stderr (truncated to MAX_STDERR_BYTES).
- `metric_breakdown.json` — per-fixture score map + which fixtures
                            regressed (for kind="regression").
- `trace.log`        — optional, grader-written via TaskGrader.write_failure_log.

The bundle directory is referenced from `attempt.metadata.failure_bundle`
as the path `"failures/<commit_hash>"` (relative to the runtime's
shared-state symlink, so agents read it as `.claude/failures/...`).
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Cap on stderr / trace text we persist. Agent reads need to be cheap, and
# graders that flood stderr with millions of lines would otherwise blow up
# the .coral/public/ directory.
MAX_STDERR_BYTES = 32 * 1024  # 32 KB
_TRUNC_NOTICE = "\n\n[...truncated; original exceeded {n} bytes]\n"

# Kind enum — `meta.json["kind"]` must be one of these.
KIND_CRASHED = "crashed"
KIND_TIMEOUT = "timeout"
KIND_REGRESSION = "regression"
KIND_FIXTURE_FAIL = "fixture_fail"  # Grader returned score=None for some fixtures

_VALID_KINDS = (KIND_CRASHED, KIND_TIMEOUT, KIND_REGRESSION, KIND_FIXTURE_FAIL)


def _failures_root(coral_dir: str | Path) -> Path:
    p = Path(coral_dir) / "public" / "failures"
    p.mkdir(parents=True, exist_ok=True)
    return p


def failure_bundle_dir(coral_dir: str | Path, commit_hash: str) -> Path:
    """Path to a single attempt's failure bundle directory.

    Lazily creates the directory — callers should only invoke this when
    they're about to write a diagnostic file. An empty bundle dir would
    falsely suggest a failure happened.
    """
    d = _failures_root(coral_dir) / commit_hash
    d.mkdir(parents=True, exist_ok=True)
    return d


def bundle_relpath(commit_hash: str) -> str:
    """The path string to store in `attempt.metadata.failure_bundle`.

    Relative to the runtime's shared-state symlink (so agents read it as
    `.claude/failures/<hash>` / `.codex/failures/<hash>` / etc.).
    """
    return f"failures/{commit_hash}"


def _atomic_write_text(path: Path, content: str) -> None:
    """tmp+rename text write — same pattern as hub.attempts.write_attempt."""
    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
        encoding="utf-8",
    ) as tmp:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def _truncate(text: str, limit: int = MAX_STDERR_BYTES) -> str:
    """Truncate `text` to `limit` bytes (UTF-8) with a footer notice."""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return text
    head = encoded[:limit].decode("utf-8", errors="replace")
    return head + _TRUNC_NOTICE.format(n=len(encoded))


def write_failure_bundle(
    coral_dir: str | Path,
    commit_hash: str,
    *,
    kind: str,
    agent_id: str,
    summary: str,
    stderr: str | None = None,
    metric_breakdown: dict[str, float] | None = None,
    regressed_fixtures: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Write a complete failure bundle. Returns the bundle directory.

    Caller is responsible for setting ``attempt.metadata["failure_bundle"]``
    to `bundle_relpath(commit_hash)`. This separation lets the daemon
    decide what goes into the bundle and the metadata path independently.
    """
    if kind not in _VALID_KINDS:
        raise ValueError(f"unknown failure kind {kind!r}; must be one of {_VALID_KINDS}")

    d = failure_bundle_dir(coral_dir, commit_hash)

    meta: dict[str, Any] = {
        "commit_hash": commit_hash,
        "agent_id": agent_id,
        "kind": kind,
        "summary": summary,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    if regressed_fixtures:
        meta["regressed_fixtures"] = list(regressed_fixtures)
    if extra:
        meta.update(extra)
    _atomic_write_text(d / "meta.json", json.dumps(meta, indent=2, sort_keys=True))

    if stderr:
        _atomic_write_text(d / "stderr.log", _truncate(stderr))

    if metric_breakdown is not None:
        # Sort keys so diffs across runs are stable.
        _atomic_write_text(
            d / "metric_breakdown.json",
            json.dumps(metric_breakdown, indent=2, sort_keys=True),
        )

    return d


def read_meta(coral_dir: str | Path, commit_hash: str) -> dict[str, Any] | None:
    """Return the meta dict for a bundle, or None if absent."""
    path = _failures_root(coral_dir) / commit_hash / "meta.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def list_bundle_files(coral_dir: str | Path, commit_hash: str) -> list[Path]:
    """Return all files in a bundle, sorted. Empty list if no bundle exists."""
    d = _failures_root(coral_dir) / commit_hash
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_file())


def read_stderr_tail(coral_dir: str | Path, commit_hash: str, max_bytes: int = 4096) -> str:
    """Tail of stderr.log for a bundle. Empty string if no stderr present.

    Used by `coral show` to print a preview without dumping the full log.
    """
    path = _failures_root(coral_dir) / commit_hash / "stderr.log"
    if not path.exists():
        return ""
    try:
        encoded = path.read_bytes()
    except OSError:
        return ""
    if len(encoded) <= max_bytes:
        return encoded.decode("utf-8", errors="replace")
    return "[...] " + encoded[-max_bytes:].decode("utf-8", errors="replace")
