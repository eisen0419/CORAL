"""Security helpers: secret detection, redaction, commit-time guards.

Adapted from the anamnesis (memory) project's SecretRedactor pattern.
See docs/security/secrets-pattern.md for the operator-side discipline.
"""

from coral.security.secret_redactor import (
    DEFAULT_SECRET_PATTERNS,
    SECRET_REDACT_PROMPT_RIDER,
    RedactResult,
    SecretPattern,
    detect_secrets,
    has_secrets,
    redact_secrets,
)

__all__ = [
    "DEFAULT_SECRET_PATTERNS",
    "SECRET_REDACT_PROMPT_RIDER",
    "RedactResult",
    "SecretPattern",
    "detect_secrets",
    "has_secrets",
    "redact_secrets",
]
