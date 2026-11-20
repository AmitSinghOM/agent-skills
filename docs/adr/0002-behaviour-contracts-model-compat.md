# ADR 0002 — Behaviour contracts are the model-compatibility test

Status: Accepted · 2026-09-13

## Context

Models and agent runtimes will be replaced several times over the repository's
intended life. There is no way to test a skill against a model that does not
exist yet. There is a way to make the test *portable*: state the observable
behaviours a skill must produce, independent of which model produces them.

## Decision

Every skill ships `evals/scenarios.json`: query, expected observable behaviours,
and a mode (`static` needs no tools; `integration` needs the skill's tools).
`scripts/eval_runner.py` replays scenarios through any CLI that answers a prompt
and stores raw outputs; grading is a separate, recorded step
(`docs/MODEL_COMPATIBILITY.md`). A model never grades its own run.

When a new model fails a scenario, the response depends on the cause: wording
that no longer steers → fix wording, patch bump; the model ignoring the contract
→ record it as a model finding and leave the contract alone.

## Consequences

- Compatibility is a table of recorded runs, not a claim.
- The runner is deliberately ignorant of models; adding a runtime is a CLI
  string, never a code change.
- Grading costs human or grader-model time; that cost is the price of an
  honest table.

## Rejected

- Hard-coding a model ID in the runner (the earlier internal `model_eval.py`
  did): every model release would need a code change.
- Auto-grading with the same model that produced the answer: circular.
