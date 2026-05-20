"""Unit tests for coral.security.secret_redactor.

Covers each of the 10 default patterns plus the redact_secrets replacement
semantics and the detect/has helpers.
"""

from __future__ import annotations

import pytest

from coral.security import (
    DEFAULT_SECRET_PATTERNS,
    detect_secrets,
    has_secrets,
    redact_secrets,
)

# --- Per-pattern detection ----------------------------------------------------


@pytest.mark.parametrize(
    "name,sample",
    [
        ("openai_key", "API key: sk-proj-abcd1234EFGH5678ijkl9012mnop"),
        ("openai_key", "key=sk-abcd1234EFGH5678ijkl9012"),
        ("anthropic_key", "ANTHROPIC=sk-ant-api03-abcdefghijklmnopqrstuvwx_-"),
        ("zhipu_key", "GLM_KEY=" + "a" * 32 + "." + "B" * 16),
        ("github_token", "PAT: ghp_" + "a" * 36),
        ("github_token", "ghs_" + "0" * 40),
        ("slack_token", "SLACK=xoxb-1234567890-abcde"),
        (
            "jwt",
            "Bearer eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM.SflKxwRJSMeKKF",
        ),
        ("aws_access_key", "Access: AKIAIOSFODNN7EXAMPLE"),
        (
            "private_key_pem",
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIB...\n-----END RSA PRIVATE KEY-----",
        ),
        (
            "private_key_pem",
            "-----BEGIN PGP PRIVATE KEY BLOCK-----\nlQVYBF...\n",
        ),
        ("long_hex_secret", "sha=" + "a1b2c3d4" * 6),  # 48 hex chars
        ("credential_assignment", 'password = "supersecret"'),
        ("credential_assignment", "api_key: abcdef123456"),
        ("credential_assignment", "access-key='myaccessvalue'"),
    ],
)
def test_each_pattern_detects(name: str, sample: str) -> None:
    """Every default pattern must fire on a realistic positive sample."""
    hits = detect_secrets(sample)
    assert name in hits, f"expected pattern {name!r} to match {sample!r}; got {hits}"


# --- No false positives on innocuous content ---------------------------------


@pytest.mark.parametrize(
    "sample",
    [
        "Hello, world!",
        "def foo(bar):\n    return bar * 2",
        "# This is a markdown comment with no credentials in it whatsoever.",
        "Score: 0.875\nFeedback: pass",
        # Short hex (39 chars — just below the 40 threshold) should not fire.
        "color=" + "a" * 39,
        # `password` mentioned but no assignment.
        "We compared password complexity policies.",
        # PEM-like phrase without the literal header
        "begins with a private key",
    ],
)
def test_innocuous_content_clean(sample: str) -> None:
    assert detect_secrets(sample) == []
    assert not has_secrets(sample)


# --- Redaction semantics ------------------------------------------------------


def test_redact_replaces_with_marker() -> None:
    content = "Header\nAPI=sk-ant-" + "a" * 30 + "\nTrailer"
    result = redact_secrets(content)
    assert "sk-ant-" not in result.content
    assert "[REDACTED_anthropic_key]" in result.content
    assert "anthropic_key" in result.hits
    # Surrounding text preserved.
    assert result.content.startswith("Header")
    assert result.content.endswith("Trailer")


def test_redact_multiple_distinct_patterns() -> None:
    content = (
        "openai: sk-proj-" + "x" * 25 + "\naws: AKIAIOSFODNN7EXAMPLE\nfine: just a normal line"
    )
    result = redact_secrets(content)
    assert "[REDACTED_openai_key]" in result.content
    assert "[REDACTED_aws_access_key]" in result.content
    assert set(result.hits) >= {"openai_key", "aws_access_key"}
    assert "fine: just a normal line" in result.content


def test_redact_idempotent_on_clean_content() -> None:
    content = "nothing to see here"
    result = redact_secrets(content)
    assert result.content == content
    assert result.hits == []


def test_redact_replaces_multiple_instances_of_same_pattern() -> None:
    # Two distinct OpenAI keys on different lines.
    content = "k1=sk-proj-" + "a" * 25 + "\nk2=sk-proj-" + "b" * 25 + "\n"
    result = redact_secrets(content)
    # Each replaced individually.
    assert result.content.count("[REDACTED_openai_key]") == 2
    # Pattern recorded once (deduped).
    assert result.hits == ["openai_key"]


def test_has_secrets_returns_bool() -> None:
    assert has_secrets("token: ghp_" + "z" * 40)
    # An obvious non-credential — no colon/equals assignment, no key shape.
    assert not has_secrets("Hello, world!")


def test_short_github_token_not_detected_as_github_but_may_be_credential() -> None:
    """github_token regex requires 36+ chars; shorter strings shouldn't fire it.
    But `credential_assignment` is more aggressive and will catch a short value
    after `token:` — verify the two patterns behave as documented.
    """
    short = "token: ghp_short"
    hits = detect_secrets(short)
    assert "github_token" not in hits
    # The generic credential_assignment is intentionally aggressive — that's by
    # design, since `token: ...` is almost always a credential context.
    assert "credential_assignment" in hits


# --- Custom-patterns path ----------------------------------------------------


def test_custom_pattern_list_is_honoured() -> None:
    """detect/redact/has must operate on caller-provided patterns, not the
    module default, when a list is passed in."""
    only_aws = [p for p in DEFAULT_SECRET_PATTERNS if p.name == "aws_access_key"]
    content = "openai: sk-proj-" + "a" * 25 + "\naws: AKIAIOSFODNN7EXAMPLE"
    hits = detect_secrets(content, patterns=only_aws)
    assert hits == ["aws_access_key"]
    # The openai key survives because we narrowed the pattern list.
    redacted = redact_secrets(content, patterns=only_aws)
    assert "sk-proj-" in redacted.content
    assert "[REDACTED_aws_access_key]" in redacted.content


def test_pattern_dedupe_in_hits() -> None:
    """If the same pattern matches multiple times, it appears once in `hits`."""
    content = "k=sk-ant-" + "a" * 30 + "\nk2=sk-ant-" + "b" * 30
    hits = detect_secrets(content)
    assert hits.count("anthropic_key") == 1
