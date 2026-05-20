# Secrets Pattern for CORAL Operators

> Operator-side discipline for keeping API keys and other credentials out of
> CORAL runs. Complements the in-repo `pre-commit` hook (see
> [coral/security/secret_redactor.py](../../coral/security/secret_redactor.py))
> that scans agent commits for ~10 credential shapes and rejects matches.

CORAL agents spawn LLM CLIs (Claude Code, Codex, Cursor Agent, Kiro, OpenCode)
and an optional LiteLLM gateway. Every one of these needs API keys. The pattern
below — adapted from the anamnesis (memory) project — keeps real key values
out of:

1. Shell history.
2. `.env` files, `direnv`, `export` lines in `.zshrc` / `.bashrc`.
3. Process listings (`ps`, `top`).
4. Claude / Codex transcript logs (under `~/.claude/projects/*.jsonl`).
5. CORAL's own attempt JSONs and `coral notes`.
6. Anywhere git-tracked in this repo.

## The wrapper

Install once:

```bash
# 1Password CLI
brew install 1password-cli
# Enable in 1Password desktop:
#   Settings → Developer → "Integrate with 1Password CLI" + "Use Touch ID to unlock"

# A thin shell wrapper. Place at ~/bin/with-secrets and chmod +x.
cat > ~/bin/with-secrets <<'EOF'
#!/usr/bin/env bash
# with-secrets <profile>[,<profile>...] -- <command> [args...]
# Injects op:// references from ~/.config/op/profiles/<profile>.env into the
# child process environment via `op run`. Real values never touch this shell.
set -euo pipefail
if [[ $# -lt 3 || "$2" != "--" ]]; then
  echo "usage: with-secrets <profile>[,<profile>...] -- <command> [args...]" >&2
  exit 64
fi
profiles="$1"; shift 2
env_files=()
IFS=',' read -ra parts <<< "$profiles"
for p in "${parts[@]}"; do
  env_files+=("--env-file=$HOME/.config/op/profiles/${p}.env")
done
exec op run "${env_files[@]}" -- "$@"
EOF
chmod +x ~/bin/with-secrets
```

## Profiles

Each profile is a tiny env file with only `op://` references — no real
values, safe to commit to a private dotfiles repo.

```bash
mkdir -p ~/.config/op/profiles

# Profile: LLM provider keys for the LiteLLM gateway / agent runtimes.
cat > ~/.config/op/profiles/llm.env <<'EOF'
ANTHROPIC_API_KEY=op://Personal/CORAL API Keys/anthropic
OPENAI_API_KEY=op://Personal/CORAL API Keys/openai
DEEPSEEK_API_KEY=op://Personal/CORAL API Keys/deepseek
EOF
```

The corresponding 1Password item ("CORAL API Keys") holds the real secrets;
each `op://` reference points to one field on that item.

## Using it

```bash
# Launch a CORAL run with keys available to the gateway + every agent runtime
with-secrets llm -- coral start -c task.yaml

# Run the gateway standalone
with-secrets llm -- python -m coral.gateway.server

# Pop one variable for an ad-hoc command (avoid this if you can — but if you
# must, never pipe op read | <command>; it leaks to stdout)
with-secrets llm -- bash -c 'curl -H "Authorization: Bearer $ANTHROPIC_API_KEY" ...'
```

`op run` injects keys into the child process's env only; they vanish when
that process exits. They never appear in `env`, `ps`, or the parent shell.

## Hard rules

- **Never** `export` API keys in your shell rc or session.
- **Never** put real keys in `.env`, docs, chat, commits, or shell history.
- **Never** pipe `op read "op://..."` to anywhere that surfaces stdout. A
  single-line API key cannot be truncated by `head`/`tail` and **will** land
  in Claude/Codex transcript logs verbatim. Only `op run -- <cmd>` is safe.
  If you absolutely must debug the op connection, use
  `op read "op://..." 2>&1 | wc -c` (length only) or
  `op read "op://..." >/dev/null 2>&1; echo $?` (exit code only).
- **Never** check `.config/op/profiles/*.env` into a public repo (private
  dotfiles repo is fine — the files contain only `op://` references).

## Leak recovery

If you suspect a key leaked into a transcript or log:

1. **Revoke immediately** at the provider console (Anthropic / OpenAI / etc).
2. **Generate a new key**; update the corresponding field in 1Password.
3. **Restart any shell using the cached value** (`op run` re-reads on next
   invocation, but in-flight processes still hold the old key).
4. **Do not try to scrub the transcript** — those are append-only logs in
   `~/.claude/projects/`. The leaked key is now public-equivalent; rotation
   is the only safe fix.

## How this composes with the in-repo pre-commit hook

| Layer | Defends against | Mechanism |
|---|---|---|
| `with-secrets` wrapper | Operator-side leaks (shell history, `ps`, transcripts) | `op run` injects env only into the child process |
| `coral/security/secret_redactor.py` | Agent-side leaks (key pasted into a fixture, scratch note, or test) | Pre-commit regex scan; default fail-closed |
| `coral eval --allow-secrets` | False positives on legitimate fixture sigils | Warn-only; tags `attempt.metadata.secret_hits` |

The wrapper stops a key from being **handed to** an agent in the first
place. The pre-commit hook stops an agent from **committing** a key it
somehow obtained anyway (web search, debugging logs, copy-paste from a
teammate's transcript). Defense in depth — neither layer is sufficient
alone.

## Where this came from

The pattern is borrowed from the anamnesis (memory) project, where a real
incident (`op read "op://..." | head -3` leaking a complete DeepSeek key
into a Claude transcript because `head` couldn't truncate the single-line
key) motivated the protocol. See the CORAL inbox scan report at
`inbox/2026-05-19-memory.md` (#2) for the original finding.
