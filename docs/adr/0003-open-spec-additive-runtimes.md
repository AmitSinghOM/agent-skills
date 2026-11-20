# ADR 0003 — Follow the open Agent Skills spec; treat runtime manifests as additive

Status: Accepted · 2026-09-13

## Context

Several agent runtimes load `SKILL.md`, each with its own extras
(`.claude-plugin/`, `.codex-plugin/`, …). Runtimes come and go faster than the
underlying format. Coupling the repository to one runtime's manifest would tie
its lifetime to that runtime's.

## Decision

The skill directory format follows the Agent Skills specification
(agentskills.io) exactly; `scripts/validate.py` enforces the spec's frontmatter
rules plus this repository's contract (evals, `metadata.version`). Runtime
manifests are additive files that point at `skills/*`; removing a runtime
removes a manifest, never a skill. A weekly workflow hashes the spec's source
file and opens an issue when it changes, so spec drift is noticed within a week
rather than when a runtime starts rejecting skills.

## Consequences

- One skill directory serves every compliant runtime by copy.
- Non-spec frontmatter keys are rejected by the validator; extras live under
  `metadata`.
- A spec change costs a deliberate review PR (validator + `docs/spec.sha256`),
  which is the intent.

## Rejected

- Maintaining per-runtime skill variants: multiplies wording drift.
- Pinning to a vendored copy of the spec: hides upstream change instead of
  surfacing it.
