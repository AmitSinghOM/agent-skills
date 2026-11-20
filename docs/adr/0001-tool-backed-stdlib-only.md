# ADR 0001 — Skills are tool-backed and standard-library only

Status: Accepted · 2026-09-12

## Context

Skill repositories mostly ship prompt text. Prompt wording degrades with every
model generation: what steered one model over-steers or under-steers the next.
A repository meant to last seven years cannot rest its value on wording.

## Decision

A skill earns a slot only if it is backed by (a) a runnable script that produces
a deterministic artefact or exit code, or (b) a behaviour contract
(`evals/scenarios.json`) whose expected behaviours are observable. Scripts import
only Python's standard library. External tools (the analyzer) are resolved at run
time and fail loudly when absent.

## Consequences

- No lockfile, no dependency rot, no supply-chain surface beyond GitHub Actions.
- Some conveniences are forgone (no YAML library: the validator parses the
  frontmatter subset itself).
- `rag-assistant` was rejected under this rule: a pattern document duplicating a
  metrics script that already lived in another project.

## Rejected

- Depending on a framework SDK (LangChain-style) for scenario running: ties the
  repository's lifetime to the framework's.
- Shipping prompt-only skills for breadth: breadth without evidence lowers the
  credibility of the tool-backed ones.
