# CORAL

An orchestration system for **autonomous coding agents** — agents follow a CORAL.md guide, run experiments, share knowledge, and loop forever.

## Project Overview

Core pattern: **Spawn agents → agents read CORAL.md → commit changes → grader daemon scores them → repeat**

Key concepts:
- **Agents are the optimizers** — Claude Code (or Codex / Cursor / Kiro / OpenCode) subprocesses, each in its own git worktree.
- **Shared state via `.coral/`** — split into `public/` (visible to agents through a runtime-specific symlink like `.claude/`, `.codex/`, `.opencode/`) and `private/` (grader code, grader venv, hidden inputs).
- **Async eval loop** — `coral eval -m "..."` stages+commits and writes a *pending* attempt; a long-running grader daemon picks it up, grades it inside a detached worktree, and writes the final score back. Default behavior blocks until the score lands; `--no-wait` returns immediately.
- **CLI orchestration** — 18 commands (see Commands below), grouped under `coral start / status / eval / log / ...`.

## Directory Structure

| Directory | Purpose |
|-----------|---------|
| `coral/types.py` | Core types: `Task`, `Score`, `ScoreBundle`, `Attempt` |
| `coral/config.py` | OmegaConf-backed YAML configuration (`CoralConfig`, `GraderConfig`, `AgentConfig`, `GatewayConfig`, `WarmStartConfig`, `HeartbeatActionConfig`, ...) |
| `coral/agent/` | Agent lifecycle: `manager.py` (multi-agent supervisor), `runtime.py` (abstract), `state.py`, `heartbeat.py`, `exit_classifier.py`, `warmstart.py`, `process.py`, `registry.py` |
| `coral/agent/builtin/` | Concrete runtimes: `claude_code`, `codex`, `cursor_agent`, `kiro`, `opencode` |
| `coral/grader/` | Grader stack: `protocol.py`, `base.py`, `task_grader.py`, `loader.py`, `subprocess_grader.py`, `daemon.py` (long-running grader), `builtin/function_grader.py` |
| `coral/hub/` | Shared state: `attempts.py`, `notes.py`, `skills.py`, `checkpoint.py` (git-tracked snapshots of `.coral/public/`), `heartbeat.py`, `prompts/` (built-in heartbeat prompts) |
| `coral/hooks/` | `post_commit.py` — implements `submit_eval` (called by `coral eval`) |
| `coral/workspace/` | Run layout: `project.py` (run dir setup), `worktree.py` (per-agent git worktrees + symlinks), `repo.py` (clone/init), `grader_env.py` (`.coral/private/grader_venv/`) |
| `coral/template/` | `coral_md.py` + `coral.md.template` / `coral_single.md.template`; bundled `agents/` (deep-researcher, librarian) and `skills/` (deep-research, organize-files, skill-creator) seeded into every run |
| `coral/gateway/` | Optional LiteLLM gateway (`server.py`, `middleware.py`, `config.py`) for intercepting agent model traffic |
| `coral/web/` | Starlette web dashboard (`app.py`, `api.py`, `events.py`, `logs.py`, `static/`) |
| `coral/cli/` | CLI package: `start.py`, `query.py`, `eval.py`, `heartbeat.py`, `ui.py`, `author.py`, `validation.py`, `_helpers.py` |
| `coral/web/` | Starlette dashboard backend — `app.py` (Starlette routes), `api.py`, `events.py` (SSE), `logs.py`, `translate.py` (on-the-fly LLM i18n proxy), `static/` (built frontend assets) |
| `web/` | **Separate** React 19 + Vite + TypeScript + Tailwind dashboard frontend. `npm run build` outputs to `../coral/web/static/`. Don't confuse with `coral/web/`. |
| `examples/` | Task configs (circle_packing, swebench-verified, kernel_engineering, mnist, ...) — each is a `task.yaml` + `seed/` + `eval/grader.py` (or packaged grader via `entrypoint`) |
| `tests/` | Pytest suite (config, grader, hooks, hub, manager reliability, daemon, workspace, ...) |
| `docs/` | Next.js documentation site (powers docs.coralxyz.com) — independent npm project |
| `docker/{claude,codex,opencode}/` | Dockerfile + entrypoint.sh per runtime — used when running CORAL itself in containers (note: Harbor-based graders can't run inside Docker, see README) |
| `blog/` | Static blog assets + `index.html`, deployed via `.github/workflows/deploy-blog.yml` |
| `install.sh` | Global installer (`uv tool install --force git+...`), invoked by the curl one-liner in README |

## How It Works

```
coral start --config task.yaml
  → results/<task-slug>/<timestamp>/        ← run_dir
    ├── .coral/
    │   ├── public/        symlinked into each worktree as .claude/ (or .codex/.opencode/...)
    │   │   ├── attempts/  pending + final ScoreBundle JSONs (one per commit hash)
    │   │   ├── notes/     agent-written markdown
    │   │   ├── skills/    agent-built reusable tools (seeded with deep-research, ...)
    │   │   ├── agents/    subagent definitions (seeded with deep-researcher, librarian)
    │   │   ├── logs/, eval_logs/, heartbeat/, eval_count
    │   │   └── grader_daemon.pid, grader_daemon_heartbeat
    │   ├── private/
    │   │   ├── eval/      copied from <task>/eval/ (legacy grader path)
    │   │   ├── grader_venv/   isolated uv venv where the grader entrypoint runs
    │   │   └── ...        anything listed in grader.private (hidden from agents)
    │   ├── config.yaml, config_dir
    │   └── .git/          checkpoint repo for shared-state versioning
    ├── repo/              cloned source repo (each run is independent)
    └── agents/<agent_id>/ git worktree on branch coral/<agent_id>; .claude/ → .coral/public/

  → If grader.entrypoint is set: bootstraps .coral/private/grader_venv/
    via `uv venv` and runs grader.setup commands inside it.
  → Spawns the chosen runtime per agent (claude_code default).
  → Starts the grader daemon as a sibling process.

Each agent loop:
  → Reads CORAL.md (generated by coral/template/coral_md.py)
  → Edits files, then runs `coral eval -m "description"`
    - submit_eval (coral/hooks/post_commit.py) does git add -A + commit
    - writes a pending Attempt JSON to .coral/public/attempts/<hash>.json
    - by default, polls until the daemon finalizes the score (use --no-wait to return immediately + `coral wait <hash>` later)
  → Grader daemon (coral/grader/daemon.py):
    - watches .coral/public/attempts/ for new pending entries
    - dispatches through a thread pool of size grader.parallel.max_workers (default 1)
    - each worker grades inside `git worktree add --detach <commit>` so agent commits during grading don't perturb the grader's view
    - reuses one TaskGrader instance per worker (no per-eval cold start)
    - writes the final ScoreBundle back atomically (tmp + rename)
  → Heartbeat actions (reflect / consolidate / pivot / lint_wiki) fire on
    interval or plateau triggers and inject prompts into the agent.
```

## Tech Stack

- **Python 3.11+** (`<3.14`), Hatchling + `hatch-vcs` build, **uv** for environment management.
- **Core deps** (always installed): `pyyaml`, `omegaconf`, `httpx`, `uvicorn`, `litellm[proxy]==1.82.3`, `pip`.
- **Extras** declared in `pyproject.toml`:
  - `dev` — `pytest`, `pytest-asyncio`, `ruff`, `mypy`
  - `ui` — `starlette`, `uvicorn[standard]`, `pyyaml` (dashboard)
  - `all` = `dev` + `ui`
  - Heavy task-grader deps (`swebench`, `datasets`, `docker`, `harbor`, ...) are **not** top-level extras — they install per-task through `grader.setup` into `.coral/private/grader_venv/`.
- **Runtimes** are external CLIs invoked as subprocesses — Claude Code (`claude`), Codex (`codex`), Cursor Agent (`cursor-agent`), Kiro (`kiro`), OpenCode (`opencode`). The `agents.runtime` config key accepts `cursor` / `cursor-agent` as aliases for `cursor_agent` (see `coral/agent/registry.py`).
- **Frontend**: React 19 + Vite + TypeScript + Tailwind, in `web/`; built static bundle is served by `coral/web/`.
- **Lint/type config** (`pyproject.toml`): ruff `line-length=100`, lint rules `E F I N W UP`, ignore `E501`; mypy `strict = true`; pytest `asyncio_mode = "auto"`, `testpaths = ["tests"]`.

## Commands

```bash
# Install
uv sync                    # Basic
uv sync --extra dev        # With pytest, ruff
uv sync --all-extras       # Everything

# Authoring
coral init my-task                                # Scaffold task.yaml + eval/grader.py + seed/
coral validate my-task                            # Type-check task structure and dry-run grader against seed/

# Running agents
coral start -c task.yaml                          # Launch agents (auto-tmux)
coral start -c task.yaml agents.count=4 agents.model=opus       # Dotlist overrides
coral start -c task.yaml run.verbose=true run.ui=true           # Verbose + dashboard
coral start -c task.yaml run.session=local                      # No tmux session
coral resume                                      # Resume latest run (sessions restored)
coral resume -i "Try greedy approaches"           # Inject an instruction at resume
coral stop [--all]                                # Stop one or all active runs
coral status                                      # Agent health + leaderboard

# Inspecting results
coral log                                         # Top 20 by score
coral log -n 5 --recent                           # Sort by time
coral log --search "kernel" --agent agent-1       # Full-text + filter
coral show <hash> [--diff]                        # Attempt details (file summary or full diff)
coral notes [--search KW] [--read N] [--history]  # Browse / read / show checkpoint history
coral skills [--read NAME]                        # List or read a shared skill
coral runs [--all] [--task NAME]                  # Active runs (or all)

# Dashboard
coral ui [--port 8420]                            # Web dashboard

# Agent-side commands (run inside an agent worktree)
coral eval -m "what changed and why"              # Stage + commit + grade (blocking)
coral eval -m "..." --no-wait                     # Submit and return immediately
coral wait <hash> [--timeout 600]                 # Block until daemon finalizes a prior submission
coral diff                                        # Show uncommitted changes
coral revert                                      # Undo last commit
coral checkout <hash>                             # Reset working tree to a previous attempt
coral heartbeat [set|remove|reset]                # Inspect or rewrite per-agent heartbeat actions

# Tests + lint
uv run pytest tests/ -v
uv run pytest tests/test_grader.py::test_subprocess_grader -v   # Single test
uv run ruff check .
uv run ruff format .
uv run mypy coral/                                # Strict mode (configured in pyproject)

# Web dashboard frontend (separate npm project under web/)
cd web && npm install
npm run dev                                       # Vite dev server with HMR
npm run build                                     # Builds into ../coral/web/static/
npm run lint                                      # ESLint

# Global install (end-user path; not for dev work in this repo)
curl -fsSL https://raw.githubusercontent.com/Human-Agent-Society/CORAL/main/install.sh | sh
# or pin a version:
CORAL_VERSION=v0.5.0 curl -fsSL .../install.sh | sh
```

## Code Patterns

1. **`GraderInterface`** protocol (`@runtime_checkable`):
   ```python
   class GraderInterface(Protocol):
       async def grade(self, codebase_path: str, tasks: list[Task], **kwargs) -> ScoreBundle: ...
   ```

2. **`BaseGrader`** with helpers `_make_score()`, `_make_bundle()`, `grade_sync()`. **`TaskGrader`** (`coral/grader/task_grader.py`) is the recommended base for task-specific graders — implement `evaluate()` and use `self.codebase_path`, `self.private_dir`, `self.args`, `self.score(...)`, `self.fail(...)`.

3. **Wiring a grader**: prefer `grader.entrypoint = "module.path:ClassName"` plus `grader.setup: ["uv pip install -e ./grader"]`. The daemon resolves the entrypoint inside `.coral/private/grader_venv/` via `coral.grader.subprocess_grader.SubprocessGrader`. The legacy `eval/grader.py` auto-discovery still works (in-process) but emits `DeprecationWarning`. `FunctionGrader` exists for wrapping plain callables but is no longer wired through `task.yaml` — ship a thin `TaskGrader` subclass instead.

4. **Eval is async by default**: `coral eval` writes a pending `Attempt` to `.coral/public/attempts/<hash>.json` and the daemon writes the final `ScoreBundle` back. `grader.max_pending_per_agent` (default 1) caps in-flight submissions per agent; `grader.parallel.max_workers` (default 1) controls daemon concurrency — bump only when the grader is concurrency-safe.

5. **Hub modules** are pure I/O over `.coral/public/`:
   - `attempts.py` — JSON CRUD, leaderboard, search, eval counters
   - `notes.py` — markdown + YAML frontmatter
   - `skills.py` — `SKILL.md` discovery
   - `checkpoint.py` — `git init` + lock-protected commits inside `.coral/public/` so agents can browse the history of shared state
   - `heartbeat.py` — per-agent action storage

6. **Heartbeat actions** (`coral/agent/heartbeat.py`): each agent has a list of `HeartbeatAction`s with `trigger ∈ {"interval", "plateau"}`. Defaults: `reflect` every 1 eval, `consolidate` every 10 (global), `pivot` after 5 plateau evals, `lint_wiki` every 10 (global). Edit at runtime with `coral heartbeat set/remove/reset`.

7. **Multi-runtime**: `coral.agent.registry` maps `agents.runtime` to a runtime class. `claude_code` is the default. Each runtime knows its native shared-state directory (`.claude`, `.codex`, `.opencode`, ...) — `generate_coral_md(..., shared_dir=...)` renders the right paths into CORAL.md.

## Key Files

| File | Purpose |
|------|---------|
| `pyproject.toml` | Package config, dependencies, `coral` console entrypoint |
| `coral/types.py` | `Task`, `Score`, `ScoreBundle`, `Attempt` |
| `coral/config.py` | YAML config dataclasses + OmegaConf merge + dotlist overrides |
| `coral/grader/protocol.py` | `GraderInterface` protocol |
| `coral/grader/base.py` | `BaseGrader` base class |
| `coral/grader/task_grader.py` | `TaskGrader` — recommended base for task graders |
| `coral/grader/loader.py` | Resolve grader from `grader.entrypoint` (subprocess) or legacy `eval/grader.py` |
| `coral/grader/subprocess_grader.py` | Worker-subprocess grader runtime used by entrypoint path |
| `coral/grader/daemon.py` | Long-running grader daemon (one per run) |
| `coral/grader/builtin/function_grader.py` | Wrap functions as graders |
| `coral/workspace/project.py` | `setup_run_dir()` — builds `.coral/{public,private}/`, clones repo, seeds bundled skills/agents |
| `coral/workspace/worktree.py` | Per-agent git worktree creation + `.claude/` symlink + permissions |
| `coral/workspace/repo.py` | Source-repo clone/init helpers + `run_setup_commands` |
| `coral/workspace/grader_env.py` | `setup_grader_env()` — `uv venv .coral/private/grader_venv/` + grader.setup |
| `coral/hub/attempts.py` | Attempt CRUD + leaderboard + per-agent pending caps |
| `coral/hub/checkpoint.py` | Git-tracked checkpoints of `.coral/public/` |
| `coral/agent/manager.py` | Multi-agent lifecycle, supervises grader daemon, restart-burst circuit breaker |
| `coral/agent/runtime.py` | `AgentRuntime` abstract base |
| `coral/agent/builtin/*.py` | Concrete runtimes (claude_code, codex, cursor_agent, kiro, opencode) |
| `coral/agent/heartbeat.py` | `HeartbeatAction` / `HeartbeatRunner` (interval + plateau triggers) |
| `coral/hooks/post_commit.py` | `submit_eval()` — git add/commit + write pending attempt + optional poll |
| `coral/template/coral_md.py` | Renders the CORAL.md each agent reads |
| `coral/cli/__init__.py` | Top-level argparse + dispatch (18 commands) |
| `coral/web/translate.py` | Backend proxy for on-the-fly LLM translation of dashboard content — API keys never reach the browser |
| `web/src/` | Vite dashboard frontend (`App.tsx`, `pages/`, `components/`, `hooks/`, `lib/`, `locales/`) |
| `install.sh` | `uv tool install` shim — used by the README curl one-liner, not by in-repo dev |
| `.github/workflows/` | `release.yml` (PyPI/GitHub release), `deploy-blog.yml` (GH Pages) |

## Developer Workflows

Project-local skills live under `.claude/skills/`. Claude Code loads them on demand by description match — describe the task and the matching skill triggers automatically.

| Skill | Use when |
|---|---|
| [coral-debug](.claude/skills/coral-debug/SKILL.md) | Editing existing code under `coral/` or chasing a bug — reproduce loops, where-to-look pointers, run inspection, lint/test |
| [coral-new-task](.claude/skills/coral-new-task/SKILL.md) | Adding a new `examples/<task>/` — seed + task.yaml + grader package, validation loop, common pitfalls |
| [coral-extend](.claude/skills/coral-extend/SKILL.md) | Extending the framework itself — new runtime, CLI command, bundled skill, hook, or config field |
