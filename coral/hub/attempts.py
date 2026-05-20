"""CRUD for .coral/public/attempts/*.json + leaderboard formatting."""

from __future__ import annotations

import copy
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from coral.types import Attempt


def _attempts_dir(coral_dir: str | Path) -> Path:
    d = Path(coral_dir) / "public" / "attempts"
    d.mkdir(parents=True, exist_ok=True)
    return d


# Process-local stat-signature cache: {absolute_path: (sig, parsed_Attempt)}.
#
# Motivation: `read_attempts` is called on every grader poll tick (~2 Hz),
# every `coral log` / `coral status`, every `submit_eval`'s pending check,
# and every monitor-loop stall-watchdog probe. Each call re-globs +
# re-`read_text` + re-`json.loads` every attempt file. With 2000 attempts
# the daemon spins ~8% CPU just on this (measured locally) — and the cost
# is linear in N for a workload that's mostly cold (only the latest 1-2
# attempts changed since the prior scan).
#
# A stat check is microseconds vs a full JSON parse at ~20 us per file.
# Caching parsed Attempts keyed by a multi-field stat signature collapses
# the steady-state cost to stat-only for cold files.
#
# Why a *multi-field* signature, not just mtime_ns:
#
#   - Atomic write uses `os.replace(tmp_path, path)` (see write_attempt).
#     This swaps in the temp file's INODE — the old inode is unlinked. An
#     inode-aware signature catches this even if mtime didn't change.
#   - HFS+ has 1-second mtime granularity (Apple legacy filesystem).
#     A pending-then-finalized write inside the same second can present
#     the same mtime to a stat() observer. Inode + size still differ.
#   - NFS may cache stat attributes; ctime tracks server-side metadata
#     updates that mtime can lag on.
#   - Overlay filesystems (Docker, containerd) sometimes lose mtime
#     precision when promoting files across layers.
#
# Signature: (st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns). Any one
# of these changing is sufficient to invalidate the cached entry.
#
# The cache is process-local. Each of {daemon, CLI, web dashboard} has
# its own; they don't share parsed entries.
_StatSig = tuple[int, int, int, int, int]
_ATTEMPT_CACHE: dict[str, tuple[_StatSig, Attempt]] = {}
_ATTEMPT_CACHE_MAX_ENTRIES = 50_000
"""Soft cap on cache size to bound memory in pathological long-runs.
50k attempts × ~2 KB parsed = ~100 MB worst case. Past the cap the
oldest cache entry is dropped on insert (FIFO; we rely on the dict
preserving insertion order)."""


def _stat_signature(path: Path) -> _StatSig | None:
    """Return the multi-field signature for `path`, or None on stat failure."""
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _clone_attempt(attempt: Attempt) -> Attempt:
    """Deep-copy an Attempt. Callers must never get the cached instance —
    Attempt is a dataclass with a mutable `metadata` dict; mutation by a
    caller would corrupt the cache silently.

    A previous version used `Attempt.from_dict(attempt.to_dict())` thinking
    that would suffice, but `to_dict()` returns the live `metadata`
    reference (not a copy), so `from_dict` recovered a shared inner dict.
    `copy.deepcopy` is the only correct call here — it recurses into
    nested dicts/lists, which `metadata` may contain (e.g. the regression
    machinery stores `score_breakdown: dict[str, float]` and
    `regressed_fixtures: list[str]` under metadata).
    """
    return copy.deepcopy(attempt)


def _cached_read(path: Path) -> Attempt | None:
    """Return a parsed Attempt, using the stat-signature cache when possible.

    Returns None if the file is missing or malformed; in either case the
    cache entry (if any) is dropped so a later write can repopulate. The
    returned Attempt is always a fresh clone — callers may safely mutate
    its `metadata` or other fields without affecting subsequent reads.
    """
    sig = _stat_signature(path)
    if sig is None:
        _ATTEMPT_CACHE.pop(str(path), None)
        return None
    key = str(path)
    cached = _ATTEMPT_CACHE.get(key)
    if cached is not None and cached[0] == sig:
        return _clone_attempt(cached[1])
    try:
        attempt = Attempt.from_dict(json.loads(path.read_text()))
    except (json.JSONDecodeError, KeyError, OSError):
        _ATTEMPT_CACHE.pop(key, None)
        return None
    # FIFO eviction once the cap is hit. Insertion order is preserved by
    # dict since CPython 3.7; assigning to an existing key does NOT move
    # it, so we drop the oldest by popping the first key only when we're
    # about to add a *new* key.
    if key not in _ATTEMPT_CACHE and len(_ATTEMPT_CACHE) >= _ATTEMPT_CACHE_MAX_ENTRIES:
        oldest = next(iter(_ATTEMPT_CACHE))
        _ATTEMPT_CACHE.pop(oldest, None)
    _ATTEMPT_CACHE[key] = (sig, attempt)
    # Return a clone — see _clone_attempt docstring.
    return _clone_attempt(attempt)


def _invalidate_cache_entry(path: Path) -> None:
    """Drop a single cache entry. Called from write_attempt on the
    successful-write path so that even before the next stat()/read,
    we won't return a stale parsed value if for some reason mtime
    happens to repeat (extremely unlikely on real filesystems but
    cheap to defend against)."""
    _ATTEMPT_CACHE.pop(str(path), None)


def clear_attempt_cache() -> None:
    """Test helper: drop the entire cache. Production code shouldn't need
    this — atomic-write mtime bumps + path-keyed entries are sufficient."""
    _ATTEMPT_CACHE.clear()


def write_attempt(coral_dir: str | Path, attempt: Attempt) -> Path:
    """Write an attempt record to JSON atomically (tmp + rename).

    Readers (monitor loop, grader daemon, `coral wait`) may poll these files
    concurrently with writes. Using tmp + rename guarantees readers see either
    the old complete file or the new complete file, never a partial write.
    """
    path = _attempts_dir(coral_dir) / f"{attempt.commit_hash}.json"
    payload = json.dumps(attempt.to_dict(), indent=2)
    # Write to a temp file in the same directory (same filesystem -> atomic rename).
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{attempt.commit_hash}.",
        suffix=".json.tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        # Clean up temp file on any failure so we don't leak .tmp files.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    # Defensive: drop our own cache entry. The mtime check would catch
    # the change on next read anyway, but clearing here means a same-
    # process read after write won't take the stat hit.
    _invalidate_cache_entry(path)
    return path


def read_attempt(coral_dir: str | Path, commit_hash: str) -> Attempt | None:
    """Read a single attempt by commit hash. Returns None if missing or malformed.

    Cache-backed via `_cached_read` — repeated reads of unchanged files
    skip the JSON parse. If the file went missing since the last cached
    read, the cache entry is dropped (otherwise a deleted attempt could
    masquerade as still-present in process-local memory).
    """
    path = _attempts_dir(coral_dir) / f"{commit_hash}.json"
    if not path.exists():
        _invalidate_cache_entry(path)
        return None
    return _cached_read(path)


def increment_eval_count(coral_dir: str | Path) -> int:
    """Increment the global eval counter at .coral/public/eval_count and return the new value."""
    counter_file = Path(coral_dir) / "public" / "eval_count"
    count = 0
    if counter_file.exists():
        try:
            count = int(counter_file.read_text().strip())
        except ValueError:
            pass
    count += 1
    counter_file.write_text(str(count))
    return count


def read_eval_count(coral_dir: str | Path) -> int:
    """Read the global eval counter (0 if missing)."""
    counter_file = Path(coral_dir) / "public" / "eval_count"
    if not counter_file.exists():
        return 0
    try:
        return int(counter_file.read_text().strip())
    except ValueError:
        return 0


def read_attempts(coral_dir: str | Path) -> list[Attempt]:
    """Read all attempt records.

    mtime-cached: files unchanged since the last call skip the JSON parse.
    The dominant cost is now `os.stat` per file (microseconds), which
    cuts daemon-poll-loop CPU dramatically on long runs.
    """
    d = _attempts_dir(coral_dir)
    attempts: list[Attempt] = []
    for f in sorted(d.glob("*.json")):
        a = _cached_read(f)
        if a is not None:
            attempts.append(a)
    return attempts


def _is_archived(a: Attempt) -> bool:
    return bool((a.metadata or {}).get("archived"))


def get_leaderboard(
    coral_dir: str | Path,
    top_n: int = 20,
    direction: str = "maximize",
    include_archived: bool = False,
) -> list[Attempt]:
    """Get top N attempts sorted by score. Direction controls sort order.

    Archived attempts (see ``coral.hub.archive``) are filtered out by default;
    pass ``include_archived=True`` to bring them back.
    """
    attempts = read_attempts(coral_dir)
    scored = [a for a in attempts if a.score is not None]
    if not include_archived:
        scored = [a for a in scored if not _is_archived(a)]
    descending = direction != "minimize"
    scored.sort(key=lambda a: a.score or 0.0, reverse=descending)
    return scored[:top_n]


def get_agent_attempts(coral_dir: str | Path, agent_id: str) -> list[Attempt]:
    """Get all attempts from a specific agent."""
    return [a for a in read_attempts(coral_dir) if a.agent_id == agent_id]


def agent_in_grader_queue(
    coral_dir: str | Path, agent_id: str, attempts: list[Attempt] | None = None
) -> Attempt | None:
    """Return the agent's newest pending attempt if any is in the grader queue.

    A pending attempt is one with `status == "pending"` and `score is None` —
    matching the daemon's own `_find_pending` filter. When multiple pending
    attempts exist for the same agent (e.g. the agent crashed and resubmitted
    while a prior attempt was still queued), the newest by ISO timestamp is
    returned so the stall-watchdog exemption uses the most relevant evidence.
    Callers (e.g. the manager monitor loop) should pass a pre-fetched
    `attempts` list once per tick to avoid rescanning the JSON directory for
    every agent.
    """
    if attempts is None:
        attempts = read_attempts(coral_dir)
    candidates = [
        a for a in attempts if a.agent_id == agent_id and a.status == "pending" and a.score is None
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda a: a.timestamp, reverse=True)
    return candidates[0]


def count_agent_pending(
    coral_dir: str | Path, agent_id: str, attempts: list[Attempt] | None = None
) -> int:
    """Return the number of pending attempts owned by `agent_id`.

    Uses the same filter as `agent_in_grader_queue` (status=="pending" and
    score is None). Pass a pre-fetched `attempts` list to avoid a duplicate
    directory scan when the caller already has one.
    """
    if attempts is None:
        attempts = read_attempts(coral_dir)
    return sum(
        1
        for a in attempts
        if a.agent_id == agent_id and a.status == "pending" and a.score is None
    )


def get_recent(
    coral_dir: str | Path, n: int = 10, include_archived: bool = False
) -> list[Attempt]:
    """Get N most recent attempts (by timestamp). Hides archived by default."""
    attempts = read_attempts(coral_dir)
    if not include_archived:
        attempts = [a for a in attempts if not _is_archived(a)]
    attempts.sort(key=lambda a: a.timestamp, reverse=True)
    return attempts[:n]


def per_agent_class_counts(coral_dir: str | Path) -> dict[str, dict[str, int]]:
    """Tally finalized attempts per agent, split by budget_class.

    Returns ``{agent_id: {"real": n, "grader_error": n, "tune": n}}``.
    Pending attempts (not yet graded) are skipped; they don't have a final
    classification. Used by `coral status` to surface per-agent grader-error rate.
    """
    counts: dict[str, dict[str, int]] = {}
    for a in read_attempts(coral_dir):
        if a.status == "pending":
            continue
        bucket = counts.setdefault(a.agent_id, {})
        bucket[a.budget_class] = bucket.get(a.budget_class, 0) + 1
    return counts


def search_attempts(
    coral_dir: str | Path, query: str, include_archived: bool = False
) -> list[Attempt]:
    """Full-text search over attempt titles, feedback, and status. Hides
    archived attempts by default."""
    query_lower = query.lower()
    results = []
    for attempt in read_attempts(coral_dir):
        if not include_archived and _is_archived(attempt):
            continue
        text = f"{attempt.title} {attempt.feedback} {attempt.status}".lower()
        if query_lower in text:
            results.append(attempt)
    return results


def _format_time(timestamp: str) -> str:
    """Format ISO timestamp to short human-readable form."""
    try:
        dt = datetime.fromisoformat(timestamp)
        return dt.strftime("%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return timestamp[:19] if timestamp else "—"


def format_leaderboard(attempts: list[Attempt]) -> str:
    """Format attempts as a markdown leaderboard table."""
    if not attempts:
        return "No attempts yet."

    lines = [
        "| Rank | Score            | Agent   | Class  | Title                                    | Time        | Commit   |",
        "|------|------------------|---------|--------|------------------------------------------|-------------|----------|",
    ]
    for i, a in enumerate(attempts, 1):
        score_str = f"{a.score:.10f}" if a.score is not None else "—"
        commit_short = a.commit_hash[:8]
        title = a.title[:40].ljust(40) if a.title else "—".ljust(40)
        time_str = _format_time(a.timestamp)
        # Display "error" instead of full "grader_error" to keep the column narrow.
        class_str = "error" if a.budget_class == "grader_error" else a.budget_class
        lines.append(
            f"| {i:<4} | {score_str:>16} | {a.agent_id:<7} | {class_str:<6} | {title} | {time_str:<11} | {commit_short} |"
        )

    return "\n".join(lines)


def format_status_summary(coral_dir: str | Path, direction: str = "maximize") -> str:
    """Format a summary of the current run state."""
    attempts = read_attempts(coral_dir)
    if not attempts:
        return "No attempts yet."

    total = len(attempts)
    scored = [a for a in attempts if a.score is not None]
    crashed = [a for a in attempts if a.status == "crashed"]

    if direction == "minimize":
        best = min(scored, key=lambda a: a.score or 0.0) if scored else None
        worst = max(scored, key=lambda a: a.score or 0.0) if scored else None
    else:
        best = max(scored, key=lambda a: a.score or 0.0) if scored else None
        worst = min(scored, key=lambda a: a.score or 0.0) if scored else None

    # Per-agent stats
    agents: dict[str, list[Attempt]] = {}
    for a in attempts:
        agents.setdefault(a.agent_id, []).append(a)

    lines = [
        f"Total attempts: {total}  |  Scored: {len(scored)}  |  Crashed: {len(crashed)}",
    ]

    if best:
        lines.append(
            f"Best:  {best.score:.10f}  ({best.title[:50]})  @ {_format_time(best.timestamp)}"
        )
    if worst and best and worst.commit_hash != best.commit_hash:
        lines.append(f"Worst: {worst.score:.10f}  ({worst.title[:50]})")

    if scored:
        first_time = min(a.timestamp for a in attempts)
        last_time = max(a.timestamp for a in attempts)
        lines.append(
            f"First attempt: {_format_time(first_time)}  |  Latest: {_format_time(last_time)}"
        )

    # Per-agent breakdown
    lines.append("")
    lines.append("Per-agent:")
    for aid in sorted(agents.keys()):
        agent_attempts = agents[aid]
        agent_scored = [a for a in agent_attempts if a.score is not None]
        if agent_scored:
            if direction == "minimize":
                agent_best = min(agent_scored, key=lambda a: a.score or 0.0)
            else:
                agent_best = max(agent_scored, key=lambda a: a.score or 0.0)
        else:
            agent_best = None
        best_str = f"best={agent_best.score:.10f}" if agent_best else "no scored attempts"
        lines.append(f"  {aid}: {len(agent_attempts)} attempts, {best_str}")

    return "\n".join(lines)
