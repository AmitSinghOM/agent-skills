---
name: code-quality-gate
description: "Use when preparing a pull request and you want a quality read on the diff, wiring a pre-commit hook or CI step that must fail only on NEW issues, triaging analyzer JSON/SARIF findings, or the user mentions code quality, complexity, changed-line gating, baselines, or SARIF. Runs the privacy-first offline analyzer cqa-analyzer (Python, Go, TypeScript/JS, Java, Kotlin, C#, C/C++, and an experimental Rust pilot) and turns findings into a deterministic pass/fail gate scoped to the lines you changed."
license: MIT
compatibility: Requires python3, git, and cqa-analyzer (pip install cqa-analyzer). No network access needed; the analyzer runs with --offline.
metadata:
  author: AmitSinghOM
  version: "1.1.1"
---

# Code Quality Gate

## Overview

Wraps [`cqa-analyzer`](https://pypi.org/project/cqa-analyzer/) — an offline
static analyzer where no source leaves the machine — and converts its findings
into one decision: **did this diff introduce new issues on the lines it
touched?** Built for the pre-PR moment and for CI, where you need a
deterministic yes/no plus a short, actionable finding list, not a wall of JSON.

The analyzer deliberately never invokes git, so it cannot know what changed.
This skill supplies the missing piece: `scripts/changed_lines_manifest.py`
turns a git diff into the changed-lines manifest the analyzer accepts, and
`scripts/run-gate.sh` drives the whole flow with the correct flags.

## Usage

Use this skill when:

- Preparing a PR and you want a quality read on the diff before reviewers see it.
- Wiring a `pre-commit` hook or a CI step that must **fail the build** only on new issues.
- The analyzer emitted JSON/SARIF and you need it triaged into "must-fix vs. accept".
- The user mentions code quality, cyclomatic complexity, changed-line gating, baselines, or SARIF.

Do **not** use it for runtime profiling or security-vulnerability scanning — it is a
static quality gate, not a SAST or APM tool.

## Core Concepts

- **Changed-line gate.** Findings are intersected with the lines the diff touched.
  A pre-existing issue on an untouched line does not fail the gate; a new one does.
  This is what makes it safe to drop into CI on a legacy repository.
- **Baseline + new-findings-only.** On a legacy repo, record today's findings once
  (`--write-baseline`), then gate only findings absent from that baseline. The
  analyzer hashes fingerprints, so the baseline contains no source text.
- **Offline and deterministic.** Same input → same output, no network. The wrapper
  always passes `--offline`, which makes the analyzer deny socket operations while
  it runs. A green local run predicts a green CI run.
- **Two machine formats.** `json` for caches and dashboards, `sarif` (2.1.0) for
  code-host annotations on the changed lines. `text` for humans.
- **Configuration is pinned outside the tree being gated.** A pull request could
  otherwise edit `.code-quality.toml` to disable a rule or exclude a path and
  pass the gate. `--config <file>` (use a file the PR cannot touch),
  `--no-project-config` (defaults only), or `--expect-config-fingerprint <sha256>`
  (analyzer exits 6 before scanning if the effective config differs) close that
  hole. Read the fingerprint once from `configuration_fingerprint` in a trusted
  JSON run and commit it to CI config.
- **Analysis health.** Every run reports what it could and could not analyze
  (`analysis_health` in JSON). Zero findings with parse failures means the scan was
  blind, not clean — `--strict` turns that into a non-zero exit.

## Quick Reference

```bash
pipx install cqa-analyzer            # or: pip install cqa-analyzer

# One-time on a legacy repo: record the accepted floor (no gate)
scripts/run-gate.sh --write-baseline .code-quality-baseline.json

# Gate a branch: only NEW warning+ findings on CHANGED lines can fail (exit 4)
scripts/run-gate.sh --base origin/main --baseline .code-quality-baseline.json --format sarif > results.sarif

# Pre-commit: gate the lines you are about to commit
scripts/run-gate.sh --staged --baseline .code-quality-baseline.json

# Clean repo (no legacy debt): gate every changed-line finding, be strict about scan health
scripts/run-gate.sh --base origin/main --strict

# CI-hardened: the PR cannot weaken the gate by editing the repo's own config
scripts/run-gate.sh --base origin/main --baseline .code-quality-baseline.json \
  --expect-config-fingerprint "$CQA_CONFIG_FINGERPRINT" --strict --format sarif > results.sarif
```

| Wrapper flag | Analyzer flags it drives | Meaning |
|---|---|---|
| `--base <ref>` | `--changed-lines-manifest` (generated from `git diff <ref>...HEAD`) | Gate only lines changed since the merge-base |
| `--staged` | `--changed-lines-manifest` (from `git diff --cached`) | Gate staged lines (pre-commit) |
| `--baseline <file>` | `--baseline <file> --new-findings-only` | Ignore findings already in the accepted floor |
| `--write-baseline <file>` | `--write-baseline <file>` | Record the floor; no gate |
| `--format json\|sarif\|text` | `--output-format` | Machine cache / code-host annotations / human |
| `--fail-on warning\|error` | `--fail-on` | Severity that fails the gate (default `warning`) |
| `--strict` | `--strict` | Non-zero exit if any requested analysis could not run |
| `--config <file>` | `--config` | Use this config; ignore the repo's `.code-quality.toml` |
| `--no-project-config` | `--no-project-config` | Analyzer defaults only |
| `--expect-config-fingerprint <sha256>` | `--expect-config-fingerprint` | Exit 6 before scanning if the effective config differs |
| `--report-only` | (drops `--fail-on`) | Always exit 0. Prints a loud warning. |
| `CQA_CMD=/abs/path` | — | Pin the analyzer binary; must be absolute (PATH-hijack guard) |

Exit codes: `0` clean · `4` gate failed · `6` config fingerprint mismatch (both
the analyzer's own) · `2` usage error · `3` environment error (missing analyzer,
missing baseline) from the wrapper. `--report-only` remaps only `4`; a `6` still fails.

## Pre-PR Gate Checklist

```
Gate Progress:
- [ ] cqa-analyzer installed (or CQA_CMD set to an absolute path)
- [ ] Baseline exists (legacy repo) — or repo is clean and no baseline is needed
- [ ] In CI: config pinned (--expect-config-fingerprint or --config outside the tree)
- [ ] Ran: scripts/run-gate.sh --base origin/main [--baseline ...]
- [ ] analysis_health shows 0 parse failures (else the result is not trustworthy)
- [ ] Reviewed must-fix findings on changed lines
- [ ] SARIF uploaded if annotating the PR
```

## Integration Patterns

- **Pre-commit:** a `.pre-commit-hooks.yaml` entry calling
  `scripts/run-gate.sh --staged --baseline .code-quality-baseline.json` blocks
  the commit before it reaches a PR.
- **GitHub Actions:** run with `--format sarif > results.sarif` and upload with
  `github/codeql-action/upload-sarif` so findings annotate the changed lines.
  Fetch enough history for the merge-base (`fetch-depth: 0`) and pin the config
  with `--expect-config-fingerprint` from a repository variable, so a PR cannot
  turn the gate off by editing `.code-quality.toml`.
- **Agent-driven PR prep:** the agent runs the gate with `--format json`, reads
  the selected findings, and reports the must-fix list to the user before
  opening the PR.

See `references/output-contracts.md` for the JSON/SARIF field shapes and
`references/changed-lines-manifest.md` for the manifest schema the generator emits.

## Common Mistakes

- With `--report-only` you **MUST** lead with the warning the wrapper prints:
  **REPORT ONLY: this run exits 0 and will not block the build even when findings
  exist.** Do not bury it in exit-code details.
- You **MUST NOT** gate a legacy repo without a baseline, because the first CI run
  drowns in pre-existing findings and the team disables the gate. Write a
  baseline once, then gate with `--baseline`.
- You **MUST** check `analysis_health` before trusting an empty result; zero
  findings with N parse failures means the scan was blind, not clean.
- You **MUST NOT** feed JSON to a code host expecting inline annotations; hosts
  annotate from SARIF. JSON is for caches and dashboards.
- You **MUST NOT** run an unpinned gate in CI on a repository where pull requests
  can edit `.code-quality.toml`; the gate is then advisory, not a gate.
- You **MUST NOT** claim a scan ran if the analyzer was not found. The wrapper
  exits 3 with an install hint; relay that, never fabricate findings.
