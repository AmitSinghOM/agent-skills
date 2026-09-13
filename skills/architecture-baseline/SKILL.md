---
name: architecture-baseline
description: "Use when starting work on an existing codebase, before implementing a feature that adds modules or dependencies, when the user asks 'where should this go', 'does this fit the architecture', or 'has the structure drifted', or when onboarding an agent onto a repo. Captures the codebase's observed architecture from cqa-analyzer output — layers, which design/DSA patterns live in which layers, score — into ARCHITECTURE.md + a machine snapshot, then guides new development to follow that structure and checks each change for drift (patterns leaking into new layers, score regressions)."
license: MIT
compatibility: Requires python3 and cqa-analyzer (pip install cqa-analyzer) or an existing analyzer JSON report via --from-json. No network access.
metadata:
  author: AmitSinghOM
  version: "1.0.0"
---

# Architecture Baseline

## Overview

Most architecture docs describe intent and rot within a month. This skill
captures what the code **actually** does: it reads
[`cqa-analyzer`](https://pypi.org/project/cqa-analyzer/)'s pattern map — which
files carry `event_sourcing_cqrs`, `database_orm`, `rate_limiting`, `adapter_pattern`
and so on — groups those files into layers, and writes the result to
`ARCHITECTURE.md` (human) and `ARCHITECTURE.snapshot.json` (machine). Every
later change is then checked against that baseline: a pattern appearing in a
layer it never lived in, or a score drop, is drift.

## Usage

Use this skill when:

- Starting work on an existing codebase and you need to know its shape before touching it.
- About to implement a feature that adds modules, a new dependency, or a new integration.
- The user asks "where should this go?", "does this fit?", or "has the architecture drifted?"
- Onboarding an agent onto a repo so it stops putting persistence code in the domain layer.

Do **not** treat the snapshot as a design authority. It records *observed*
conventions; confirm them with the owner before enforcing, and re-snapshot
deliberately when the architecture is meant to change.

## Workflow

### 1. Snapshot (once, and after every deliberate architecture change)

```bash
pipx install cqa-analyzer                       # once
scripts/architecture_baseline.py snapshot       # writes ARCHITECTURE.snapshot.json + ARCHITECTURE.md
scripts/architecture_baseline.py snapshot --from-json report.json   # reuse an existing analyzer run
```

Commit both files. Hand-written context in `ARCHITECTURE.md` outside the
generated markers is preserved on re-snapshot — put the *why* there.

### 2. Before implementing: read, then place

Read `ARCHITECTURE.md`. From the "Where design patterns live" table, decide the
layer for the new code **before** writing it:

- Persistence, HTTP clients, queues, ORMs → whichever layer already carries
  `database_orm` / `adapter_pattern` / `api_design` (typically `adapters/`,
  `infrastructure/`, `entrypoints/`).
- Business rules and invariants → the layer that carries none of those
  (typically `domain/`, `core/`).
- Orchestration, retries, idempotency → where `retry_backoff`, `idempotency`,
  `event_sourcing_cqrs` already live.
- New dependency → check `dependencies` in the snapshot; if the same capability
  exists, reuse it.

State the placement decision to the user in one sentence, with the layer and
the observed convention that justifies it.

### 3. After implementing: check

```bash
scripts/architecture_baseline.py check                   # human report
scripts/architecture_baseline.py check --fail-on-drift   # exit 4 on violations (CI)
scripts/architecture_baseline.py check --json            # machine-readable
```

| Result | Meaning | What to do |
|---|---|---|
| `VIOLATION 'X' now appears in [layer]` | A pattern leaked into a layer it never lived in | Move the code, or get explicit agreement and re-snapshot |
| `VIOLATION score dropped a → b` | Architecture signal fell by ≥ `--max-score-drop` (default 0.5) | Inspect what was removed or diluted |
| `note new pattern 'X' in [...]` | Something new was introduced | Confirm it's intended; re-snapshot to adopt |
| `note pattern 'X' no longer detected` | A convention disappeared | Was that deliberate? |
| `note scoring policy a → b` | Analyzer scoring changed | Scores are not comparable; re-snapshot |

### 4. Re-snapshot only on purpose

When the drift is the *intended* design change, run `snapshot` again and commit
it with the change, in the same PR, with the reason in the hand-written part of
`ARCHITECTURE.md`. Never re-snapshot to make a red check green.

## Quick Reference

| Flag | Meaning |
|---|---|
| `--project DIR` | Project root (default `.`) |
| `--from-json FILE` | Use an existing analyzer JSON instead of running the analyzer |
| `--layer-depth N` | Path components that define a layer (default 2: `pkg/adapters`) |
| `--snapshot FILE` | Snapshot path (default `ARCHITECTURE.snapshot.json`) |
| `--md FILE` / `--no-md` | Human doc path / skip it (snapshot only) |
| `--fail-on-drift` | Exit 4 on violations (check only) |
| `--max-score-drop F` | Score drop that counts as a violation (default 0.5) |
| `CQA_CMD=/abs/path` | Pin the analyzer binary (must be absolute) |

Exit codes: `0` ok · `4` drift violations with `--fail-on-drift` · `1` environment
or input error (analyzer missing, no snapshot, bad JSON).

## Worked Example

```
$ scripts/architecture_baseline.py snapshot --from-json report.json
wrote ARCHITECTURE.snapshot.json and ARCHITECTURE.md

ARCHITECTURE.md → "database_orm: adapters (9), entrypoints (3), migrations (2)"
                  "domain carries: error_handling, event_sourcing_cqrs only"

User: "Add a Redis cache for account lookups."
Agent: "Placement: cloudscale/adapters/redis/ — all persistence and external I/O
        (database_orm, adapter_pattern) live under adapters/; domain/ carries no
        I/O patterns. I'll expose it through the existing AccountRegistry port."

$ scripts/architecture_baseline.py check --fail-on-drift
  VIOLATION  'database_orm' now appears in ['cloudscale/domain']; baseline layers were [...]
→ the cache client was imported directly from a domain service. Move behind the port.
```

## Common Mistakes

- You **MUST NOT** re-snapshot to silence a violation. The snapshot changes only
  with an intended architecture change, stated in the PR.
- You **MUST** confirm observed conventions with the owner before enforcing them
  as rules; "only in adapters/" is an observation until someone says it is a
  decision.
- You **MUST NOT** compare scores across scoring-policy versions; the check
  flags this — re-snapshot instead.
- You **SHOULD** keep `--layer-depth` stable per repo; changing it makes every
  layer name differ and the check meaningless.
- You **MUST NOT** claim a snapshot was taken if the analyzer was not found. The
  script exits 1 with an install hint; relay it, never fabricate an architecture.
