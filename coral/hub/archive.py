"""Attempt archiving: hide low-value attempts from the leaderboard.

Implements the "compress history" half of Trinkle's "Learning Beyond
Gradients" maintenance loop. The paper's warning — "an HS that only
grows becomes unmaintainable" — applies directly to CORAL's
`attempts/` directory: a long run accumulates hundreds of low-score
attempts that drown out signal in `coral log` and `coral notes`.

This module provides a non-destructive archiving primitive:

- `archive_attempt(coral_dir, commit_hash, reason)` flips
  `attempt.metadata.archived = True` and stamps the reason +
  timestamp.
- The attempt JSON is preserved (audit + reproducibility); only its
  visibility changes.
- `coral log` and friends filter out archived attempts by default;
  `--include-archived` brings them back.

Selection helpers (`select_for_archive`) make policy explicit and
testable: archive by score floor, by date cutoff, or by status set
(e.g. all `crashed` + `timeout` attempts from before a known-good
commit).
"""

from __future__ import annotations

import contextlib
import fcntl
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from coral.hub.attempts import read_attempt, read_attempts, write_attempt
from coral.types import Attempt

# Reserved metadata key. Stored as a dict so we can hold reason + when
# without polluting the top-level metadata namespace.
ARCHIVE_KEY = "archived"


@contextlib.contextmanager
def _attempt_lock(coral_dir: str | Path, commit_hash: str) -> Iterator[None]:
    """Per-attempt exclusive lock for read-modify-write of attempts/<hash>.json.

    Archive runs concurrently with the grader daemon, which finalizes
    pending attempts via `write_attempt`. Without serialization, this
    can happen:

      1. archive_attempt reads attempts/<hash>.json (status=pending,
         score=None) at T=0.
      2. daemon finalizes the same attempt (status=improved, score=0.8)
         at T=1, writing the JSON atomically.
      3. archive_attempt writes back its earlier read with
         metadata.archived added at T=2 — clobbering the finalized
         state, restoring `pending`, score `None`.

    The lock file is a sibling (`attempts/.<hash>.lock`) so the JSON
    file itself is never opened with a conflicting mode. POSIX
    `fcntl.flock`; CORAL targets macOS / Linux.

    Note: the daemon's `write_attempt` does NOT currently acquire this
    lock. That's intentional — the daemon is the sole writer for the
    pending→finalized transition, and `archive_attempt` refuses to
    touch pending attempts (see below). The lock primarily serializes
    archive operations against each other and provides re-read
    semantics so the archive write is based on the latest finalized
    state, not a stale snapshot.
    """
    lock_dir = Path(coral_dir) / "public" / "attempts"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f".{commit_hash}.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp into a tz-aware UTC `datetime`.

    Lexicographic string comparison of ISO timestamps is unreliable
    when offsets differ (`2026-05-19T23:00:00-08:00` lexically less
    than `2026-05-20T01:00:00+00:00` even though they describe the
    same instant) or when one side is naive. We parse + normalize
    so `matches()` compares actual moments in time.
    """
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        # Naive timestamps are assumed UTC (consistent with CORAL's
        # daemon, which stamps `datetime.now(UTC).isoformat()` and
        # therefore always emits tz-aware).
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


@dataclass
class ArchiveCriteria:
    """Predicate set for selecting attempts to archive."""

    score_below: float | None = None
    """Archive attempts with `score is not None and score < this`."""
    before_date: str | None = None
    """ISO-8601 cutoff; archive attempts whose `timestamp` is earlier.
    Parsed as a tz-aware datetime — see `_parse_iso` for normalization."""
    status_in: tuple[str, ...] | None = None
    """Archive attempts whose `status` is in this set (e.g. ("crashed", "timeout"))."""

    def matches(self, attempt: Attempt) -> bool:
        if is_archived(attempt):
            return False  # already archived
        # Pending attempts are never archivable — see archive_attempt's
        # rationale (would race the daemon's finalize).
        if attempt.status == "pending":
            return False
        if self.score_below is not None:
            if attempt.score is None or attempt.score >= self.score_below:
                return False
        if self.before_date is not None:
            cutoff = _parse_iso(self.before_date)
            attempt_at = _parse_iso(attempt.timestamp)
            if cutoff is None or attempt_at is None:
                # Unparseable timestamp on either side → skip the attempt
                # rather than risk lexical-compare false matches.
                return False
            if attempt_at >= cutoff:
                return False
        if self.status_in is not None and attempt.status not in self.status_in:
            return False
        # An empty criteria set matches nothing — refuse the foot-gun.
        if self.score_below is None and self.before_date is None and self.status_in is None:
            return False
        return True


def is_archived(attempt: Attempt) -> bool:
    """True iff an attempt has been archived via this module."""
    return bool((attempt.metadata or {}).get(ARCHIVE_KEY))


def archive_attempt(
    coral_dir: str | Path,
    commit_hash: str,
    reason: str = "",
) -> bool:
    """Flip `metadata.archived` on a finalized attempt.

    Returns True if it actually flipped, False if:
      - the attempt doesn't exist,
      - it's already archived,
      - it's still `pending` (daemon hasn't finalized — archiving would
        risk clobbering the finalized state when the daemon writes it).

    Acquires a per-attempt lock and re-reads inside the critical
    section so the write is based on the latest finalized state, not
    a stale snapshot taken before the daemon's `write_attempt`.
    """
    # Cheap pre-check outside the lock (avoid acquiring for missing/archived).
    pre = read_attempt(Path(coral_dir), commit_hash)
    if pre is None or is_archived(pre):
        return False
    if pre.status == "pending":
        return False

    with _attempt_lock(coral_dir, commit_hash):
        # Re-read inside the lock so we don't clobber a finalize that
        # happened between the pre-check and the lock acquisition.
        attempt = read_attempt(Path(coral_dir), commit_hash)
        if attempt is None or is_archived(attempt):
            return False
        if attempt.status == "pending":
            return False
        metadata = dict(attempt.metadata or {})
        metadata[ARCHIVE_KEY] = {
            "reason": reason,
            "at": datetime.now(UTC).isoformat(),
        }
        updated = Attempt(
            commit_hash=attempt.commit_hash,
            agent_id=attempt.agent_id,
            title=attempt.title,
            score=attempt.score,
            status=attempt.status,
            parent_hash=attempt.parent_hash,
            timestamp=attempt.timestamp,
            feedback=attempt.feedback,
            shared_state_hash=attempt.shared_state_hash,
            parent_shared_state_hash=attempt.parent_shared_state_hash,
            metadata=metadata,
        )
        write_attempt(str(coral_dir), updated)
        return True


def unarchive_attempt(coral_dir: str | Path, commit_hash: str) -> bool:
    """Reverse of `archive_attempt`. Returns True if it actually flipped.

    Same locking + re-read discipline as `archive_attempt`. Also refuses
    to touch attempts whose current status is `pending` — if an
    operator-edited or otherwise legacy archived-while-pending record
    exists, unarchiving from a stale snapshot here would race a
    daemon finalize that lacks the attempt lock (same failure mode
    that `archive_attempt`'s pending guard defends against).
    """
    pre = read_attempt(Path(coral_dir), commit_hash)
    if pre is None or not is_archived(pre):
        return False
    if pre.status == "pending":
        return False

    with _attempt_lock(coral_dir, commit_hash):
        attempt = read_attempt(Path(coral_dir), commit_hash)
        if attempt is None or not is_archived(attempt):
            return False
        if attempt.status == "pending":
            return False
        metadata = dict(attempt.metadata or {})
        metadata.pop(ARCHIVE_KEY, None)
        updated = Attempt(
            commit_hash=attempt.commit_hash,
            agent_id=attempt.agent_id,
            title=attempt.title,
            score=attempt.score,
            status=attempt.status,
            parent_hash=attempt.parent_hash,
            timestamp=attempt.timestamp,
            feedback=attempt.feedback,
            shared_state_hash=attempt.shared_state_hash,
            parent_shared_state_hash=attempt.parent_shared_state_hash,
            metadata=metadata,
        )
        write_attempt(str(coral_dir), updated)
        return True


def select_for_archive(
    coral_dir: str | Path,
    criteria: ArchiveCriteria,
) -> list[Attempt]:
    """Return every attempt that matches `criteria` (and isn't yet archived)."""
    return [a for a in read_attempts(coral_dir) if criteria.matches(a)]


def archive_many(
    coral_dir: str | Path,
    attempts: list[Attempt],
    reason: str = "",
) -> int:
    """Archive every attempt in `attempts`. Returns count successfully flipped."""
    n = 0
    for a in attempts:
        if archive_attempt(coral_dir, a.commit_hash, reason=reason):
            n += 1
    return n
