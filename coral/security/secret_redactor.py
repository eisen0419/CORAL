"""Regex-based credential detection and redaction.

Ported from anamnesis (src/security/SecretRedactor.ts) — same 10 patterns,
same semantics. Pure stdlib (re only). Stateless: detect/has/redact each
do their own compilation so callers never see the lastIndex / global-flag
foot-gun the JS version had to defend against.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SecretPattern:
    """A single secret-detection rule."""

    name: str
    """Unique identifier — surfaces in audit logs and `[REDACTED_<name>]` markers."""
    regex: re.Pattern[str]
    """Compiled pattern. Compile with `re.IGNORECASE` where appropriate, never with re.MULTILINE."""


# 10 patterns covering the credential shapes most commonly leaked by LLM
# agents into commits. Mirrors anamnesis DEFAULT_SECRET_PATTERNS.
DEFAULT_SECRET_PATTERNS: list[SecretPattern] = [
    # Anthropic: sk-ant-...  (listed before the generic OpenAI pattern so
    # `redact_secrets` tags `sk-ant-*` keys as anthropic_key, not openai_key —
    # the OpenAI regex would otherwise swallow `ant-` because `-` is in its
    # character class).
    SecretPattern("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    # OpenAI: sk-... or sk-proj-... (>=20 chars in the body). The negative
    # lookahead `(?!ant-)` keeps this from doubly-matching Anthropic keys when
    # the patterns are evaluated independently (e.g. in `detect_secrets`).
    SecretPattern("openai_key", re.compile(r"sk-(?!ant-)(?:proj-)?[A-Za-z0-9_-]{20,}")),
    # Zhipu GLM: 32 hex . 16 alnum
    SecretPattern("zhipu_key", re.compile(r"[a-f0-9]{32}\.[A-Za-z0-9]{16}\b")),
    # GitHub PAT: ghp_/gho_/ghu_/ghs_/ghr_ + 36+ alnum
    SecretPattern("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    # Slack tokens: xoxb-/xoxp-/xoxa-/xoxs-
    SecretPattern("slack_token", re.compile(r"xox[bpas]-[A-Za-z0-9-]{10,}")),
    # JWT: three base64-url segments; middle one must start with eyJ
    SecretPattern(
        "jwt",
        re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ),
    # AWS access key id
    SecretPattern("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    # PEM / OpenSSH / PGP private key headers (RSA/EC/DSA/OPENSSH/generic + PGP "BLOCK" variant)
    SecretPattern(
        "private_key_pem",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY( BLOCK)?-----", re.IGNORECASE),
    ),
    # Long hex (40+ chars) — catches SHA1/SHA256-like secrets
    SecretPattern("long_hex_secret", re.compile(r"\b[a-f0-9]{40,}\b", re.IGNORECASE)),
    # Credential assignment lines:  password = 'xxxxxxxx' / token: "yyyyyyyy" / api_key=zzzzzzzz
    # Quoted: >=6 inside quotes;  unquoted: >=6 non-whitespace, non-quote chars.
    SecretPattern(
        "credential_assignment",
        re.compile(
            r"\b(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)"
            r"\s*[:=]\s*"
            r"(?:'[^']{6,}'|\"[^\"]{6,}\"|[^\s'\"]{6,})",
            re.IGNORECASE,
        ),
    ),
]


@dataclass
class RedactResult:
    """Output of `redact_secrets`."""

    content: str
    """Input with each hit replaced by `[REDACTED_<pattern_name>]`."""
    hits: list[str]
    """Pattern names that matched at least once (deduped, in pattern order)."""


def detect_secrets(
    content: str,
    patterns: list[SecretPattern] | None = None,
) -> list[str]:
    """Return the names of every pattern that matches `content`.

    Order of returned hits matches `patterns` order (which by default mirrors
    `DEFAULT_SECRET_PATTERNS`). Each pattern appears at most once.
    """
    pats = patterns if patterns is not None else DEFAULT_SECRET_PATTERNS
    hits: list[str] = []
    for p in pats:
        if p.regex.search(content):
            hits.append(p.name)
    return hits


def redact_secrets(
    content: str,
    patterns: list[SecretPattern] | None = None,
) -> RedactResult:
    """Replace every match of every pattern with `[REDACTED_<pattern_name>]`."""
    pats = patterns if patterns is not None else DEFAULT_SECRET_PATTERNS
    out = content
    hits: list[str] = []
    for p in pats:
        new_out, n = p.regex.subn(f"[REDACTED_{p.name}]", out)
        if n > 0:
            hits.append(p.name)
            out = new_out
    return RedactResult(content=out, hits=hits)


def has_secrets(
    content: str,
    patterns: list[SecretPattern] | None = None,
) -> bool:
    """True iff at least one pattern matches `content`."""
    pats = patterns if patterns is not None else DEFAULT_SECRET_PATTERNS
    return any(p.regex.search(content) for p in pats)


SECRET_REDACT_PROMPT_RIDER = """\
SECURITY: Never commit raw credentials. If you encounter an API key, password,
token, JWT, private key (PEM/OpenSSH/PGP), AWS access key, or similar secret —
in seed code, environment files, test fixtures, logs, or your own scratch
notes — do not commit it. Use environment variables, a secret manager (1Password
op://, AWS Secrets Manager), or commit a placeholder like `<REDACTED>` or `***`.
CORAL's pre-commit hook will reject commits that contain detectable credentials.
"""
