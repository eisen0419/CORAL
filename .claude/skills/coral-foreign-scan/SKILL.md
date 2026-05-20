---
name: coral-foreign-scan
description: Scan a foreign project (any non-CORAL repo) for code, patterns, or lessons that would be valuable to upstream into CORAL. Classifies findings into CORAL's 5 extension points (new task / runtime / bundled skill / hook-or-CLI / framework fix), scores each by ROI = value / (cost x risk), and writes a ranked report to ~/projects/CORAL/inbox/. Use when the user (in some other project's session) says "scan this for CORAL" or invokes this skill explicitly.
---

# coral-foreign-scan

You are running **in a foreign project's session**, not inside the CORAL repo. Your job: scan the current working directory and produce a ranked report telling the CORAL maintainer what's worth pulling upstream.

## Pre-flight (do these before scanning)

1. `pwd` — confirm you're NOT in `~/projects/CORAL`. If you are, abort and tell the user "this skill is for foreign projects only".
2. `cat ~/projects/CORAL/CLAUDE.md` — load CORAL's architecture so you know what counts as a valid extension point.
3. `ls ~/projects/CORAL/examples/ ~/projects/CORAL/coral/agent/builtin/ ~/projects/CORAL/coral/template/skills/ ~/projects/CORAL/coral/hooks/` — see what already exists upstream (so you don't suggest duplicates).
4. Read this project's `README.md`, `CLAUDE.md`/`AGENTS.md` (if any), `package.json`/`pyproject.toml`/`Cargo.toml`. Understand what it actually does before classifying.

## The 5 extension points (canonical taxonomy)

Every finding MUST be classified into exactly one of these. Anything else is out of scope.

| # | Type | CORAL landing dir | What to look for in this project |
|---|------|-------------------|----------------------------------|
| **T** | new **task** | `examples/<task>/` | Anything with a clear automatable success metric — benchmarks, eval harnesses, optimization problems with a numeric score, fixtures + grading logic |
| **R** | new **runtime** | `coral/agent/builtin/<x>.py` | Code that wraps a new agent CLI (something like Aider, Continue, Cline, etc.) — process management, prompt injection, exit detection |
| **S** | bundled **skill** / subagent | `coral/template/skills/` or `agents/` | Reusable Claude/Codex skills, prompt templates, subagent definitions that are domain-agnostic and would help any CORAL agent |
| **H** | **hook** or new CLI command | `coral/hooks/` or `coral/cli/` | Lifecycle hooks (post-commit, pre-eval, on-restart), CLI sub-commands, dashboard widgets |
| **F** | **framework fix** / perf | `coral/{grader,agent,workspace,hub,...}` | Bug patterns, race conditions, performance tricks, observability improvements that apply to CORAL's own internals |

## Scoring rubric

For each candidate finding, score on three axes (use **low / medium / high**, no half-grades):

**Value** — if this lands in CORAL, who benefits and how much?
- high: unblocks a whole task class, fixes a known pain point, or adds a previously-impossible capability
- medium: improves DX/perf measurably, or adds a new task in a domain CORAL doesn't cover
- low: nice-to-have, marginal improvement, or only helps niche users

**Cost** — work to upstream cleanly into CORAL's style
- low: <1 day, single file, drop-in
- medium: 1-3 days, touches 2-5 files, needs tests
- high: >3 days, cross-cutting refactor, new abstractions, breaking changes

**Risk** — what could break in CORAL after merging
- low: additive, no API change, well-isolated
- medium: touches shared code paths, might affect existing tasks
- high: changes public API, alters grader/agent contracts, or behaves differently per runtime

**ROI ranking**: convert each axis to a number (low=1, medium=2, high=3) and compute
```
ROI = value_score / (cost_score * risk_score)
```
Sort findings descending by ROI. Ties broken by lower cost first, then lower risk.

## Anti-patterns (do NOT include in the report)

- "Refactor CORAL to use my favorite pattern" — out of scope, you're scanning the foreign repo, not editing CORAL
- Duplicates of what already exists upstream (you already `ls`-ed examples/ etc. in pre-flight)
- Findings that need code you don't have access to or can't read
- Vague "general best practice" suggestions with no concrete file/symbol reference
- More than 15 findings total — if you have >15, you're not being selective enough; cull to top 15

## Output

Write to: `~/projects/CORAL/inbox/<YYYY-MM-DD>-<project-slug>.md`

`<project-slug>` is the basename of `pwd`, kebab-cased, lowercase.
If the file exists, append a `-2`, `-3`, ... suffix.

### Report template (use exactly this structure)

```markdown
# CORAL Foreign Scan: <project name>

- **Scanned from**: <absolute path to project>
- **Date**: <YYYY-MM-DD>
- **Scanner**: claude-code session in <project name>
- **CORAL HEAD checked against**: <output of `git -C ~/projects/CORAL rev-parse --short HEAD`>

## Summary

<3-5 bullets: what this project is, why some of its code might be relevant to CORAL, what overall theme emerged from the scan>

## Findings (ranked by ROI)

### #1 — <one-line title> [Type: T/R/S/H/F]

- **What**: <2-3 sentences describing the concrete code/pattern/lesson, with file paths and line refs>
- **Where to land**: <exact target dir/file in CORAL>
- **Value**: high/medium/low — <one sentence reason>
- **Cost**: low/medium/high — <one sentence reason, with rough time estimate>
- **Risk**: low/medium/high — <one sentence reason>
- **ROI**: <number, e.g. 1.50>
- **Dependencies**: <any deps it brings, or "none">
- **Open questions**: <things the CORAL maintainer should decide before accepting, or "none">

### #2 — ...

(repeat for each finding, max 15)

## Cuts (findings considered but rejected)

<short list of things you looked at but decided weren't worth a slot, with one-line reason per cut. Helps the maintainer trust the scan was thorough.>

## Handoff note to CORAL session

<2-3 sentences telling the CORAL-side Claude what to do first. Example: "Start with #1 and #3 — both are low-cost low-risk additive. #2 is high-value but needs a design discussion first.">
```

## Hard rules

- **Do not modify any code** in this project or in CORAL. This is a read-only scan + report.
- **Do not commit** anything. Just write the inbox file.
- **Anchor every finding to a real file/line** in this project. No vague claims.
- **Cap at 15 findings**. Ruthlessly cut low-ROI items.
- **One pass, no iterating**. If you can't fit it in one report, the project isn't ready to upstream.
- **If you find zero high-ROI items**, say so explicitly in the Summary and write a short report — don't pad.

## Final step

After writing the report, print **only** these three lines to the user:

```
Report written: ~/projects/CORAL/inbox/<filename>
Findings: <N>  |  Top ROI: <number>  |  Type breakdown: T=<n> R=<n> S=<n> H=<n> F=<n>
Switch to CORAL session and tell it: "read ~/projects/CORAL/inbox/<filename>"
```

Nothing else. The user wants the report path, not a chat summary.
