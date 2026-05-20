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

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from coral.hub.attempts import read_attempt, read_attempts, write_attempt
from coral.types import Attempt

# Reserved metadata key. Stored as a dict so we can hold reason + when
# without polluting the top-level metadata namespace.
ARCHIVE_KEY = "archived"


@dataclass
class ArchiveCriteria:
    """Predicate set for selecting attempts to archive."""

    score_below: float | None = None
    """Archive attempts with `score is not None and score < this`."""
    before_date: str | None = None
    """ISO-8601 cutoff; archive attempts with `timestamp < this`."""
    status_in: tuple[str, ...] | None = None
    """Archive attempts whose `status` is in this set (e.g. ("crashed", "timeout"))."""

    def matches(self, attempt: Attempt) -> bool:
        if is_archived(attempt):
            return False  # already archived
        if self.score_below is not None:
            if attempt.score is None or attempt.score >= self.score_below:
                return False
        if self.before_date is not None and attempt.timestamp >= self.before_date:
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
    """Flip `metadata.archived` on an attempt. Returns True if it actually
    flipped (False if already archived or attempt not found).
    """
    attempt = read_attempt(Path(coral_dir), commit_hash)
    if attempt is None:
        return False
    if is_archived(attempt):
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
    """Reverse of `archive_attempt`. Returns True if it actually flipped."""
    attempt = read_attempt(Path(coral_dir), commit_hash)
    if attempt is None:
        return False
    if not is_archived(attempt):
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
