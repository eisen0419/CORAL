"""Pre-commit hook: scan the staging area for leaked credentials.

Invoked by `coral eval` after `git add -A` but before `git commit`. If any of
the patterns in `coral.security.DEFAULT_SECRET_PATTERNS` match the staged
content, the default policy raises `SecretsBlockedError` and the caller is
expected to roll back the staging area so the agent's working tree is left
clean for a follow-up fix.

Two policies, selected at the call site:

* ``fail-closed`` (default) — raise. Nothing gets committed.
* ``warn`` — return the hit list. Caller decides whether to tag the attempt
  metadata or just log.

Operator dial: the CLI exposes ``--allow-secrets`` to flip to warn mode for the
rare case where a false positive blocks a legitimate commit (e.g. test fixtures
that legitimately contain `BEGIN PRIVATE KEY` markers for an HSM mock).
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

from coral.security import detect_secrets

logger = logging.getLogger(__name__)


class SecretsBlockedError(RuntimeError):
    """Raised when pre-commit policy is fail-closed and secrets were found."""

    def __init__(self, files: dict[str, list[str]]) -> None:
        self.files = files
        super().__init__(self._format_message(files))

    @staticmethod
    def _format_message(files: dict[str, list[str]]) -> str:
        lines = ["Secret-like content detected in staged changes:"]
        for path, hits in files.items():
            lines.append(f"  {path}: {', '.join(hits)}")
        lines.append("")
        lines.append("Aborted. To resolve, one of:")
        lines.append("  - Remove the credential and replace with a placeholder.")
        lines.append("  - Move it to an env var / 1Password / secret manager.")
        lines.append("  - If this is a false positive, re-run with `--allow-secrets`.")
        return "\n".join(lines)


@dataclass
class ScanResult:
    """Outcome of a staged-content scan."""

    files: dict[str, list[str]]
    """Map of file path -> list of pattern names that matched. Empty if clean."""

    @property
    def clean(self) -> bool:
        return not self.files

    @property
    def all_hits(self) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for hits in self.files.values():
            for h in hits:
                if h not in seen:
                    seen.add(h)
                    out.append(h)
        return out


def _git(args: list[str], cwd: str) -> str:
    """Run a git command and return stdout (raise on non-zero)."""
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _list_staged_paths(workdir: str) -> list[str]:
    """Return paths of files staged for commit (added or modified)."""
    # -z null-terminates filenames so newlines in paths don't confuse us.
    raw = _git(["diff", "--cached", "--name-only", "-z"], workdir)
    return [p for p in raw.split("\x00") if p]


def _read_staged_blob(path: str, workdir: str) -> str | None:
    """Read the staged-side content of `path` from the index.

    Returns None for binary files or unreadable blobs (so the caller can skip
    scanning them without surfacing a hard error).
    """
    # Use `git show :path` to read the index version (not working tree),
    # so we scan exactly what's about to be committed.
    result = subprocess.run(
        ["git", "show", f":{path}"],
        capture_output=True,
        cwd=workdir,
    )
    if result.returncode != 0:
        return None
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        # Binary blob — nothing to scan with text regexes.
        return None


def scan_staged_for_secrets(workdir: str = ".") -> ScanResult:
    """Scan everything in the git index for secret-like content.

    Returns a `ScanResult`; callers decide what to do with hits.
    """
    files: dict[str, list[str]] = {}
    for path in _list_staged_paths(workdir):
        blob = _read_staged_blob(path, workdir)
        if blob is None:
            continue
        hits = detect_secrets(blob)
        if hits:
            files[path] = hits
    return ScanResult(files=files)


def enforce_no_secrets(workdir: str = ".", strict: bool = True) -> ScanResult:
    """Scan; raise if `strict` and any hits; otherwise return the result.

    Logs at WARNING level on any hit (even in warn mode) so operators can see
    secret bleeds in logs even when the policy is permissive.
    """
    result = scan_staged_for_secrets(workdir)
    if result.clean:
        return result

    logger.warning(
        "Secret-pattern hits in staged changes: %s",
        {path: hits for path, hits in result.files.items()},
    )
    if strict:
        raise SecretsBlockedError(result.files)
    return result


def unstage_all(workdir: str = ".") -> None:
    """Reset the index, leaving the working tree untouched.

    Called after `SecretsBlockedError` so the agent's edits survive (only the
    `git add -A` is undone). The agent can then fix the leak and re-run
    `coral eval`.
    """
    subprocess.run(
        ["git", "reset", "--mixed", "HEAD"],
        capture_output=True,
        cwd=workdir,
        check=False,
    )
