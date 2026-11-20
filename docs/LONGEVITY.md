# Longevity charter

This repository is built to stay useful through model upgrades, agent-runtime
churn, and spec revisions for at least seven years (2026–2033). Nothing below
guarantees that. What it does is make the repository's value independent of any
one model or runtime, and make its health checkable by a machine rather than
asserted by a human.

## Principles

1. **Value lives in tools and contracts, not in prompt wording.** Every skill is
   backed by either a runnable script (stdlib-only, deterministic, tested) or a
   behaviour contract (`evals/scenarios.json`) that states observable outcomes.
   A model upgrade can change *how* a skill is worded; it cannot change whether
   `changed_lines_manifest.py` emits a valid manifest.
2. **Standard library only.** Scripts import nothing outside Python's standard
   library. The only external dependency is the analyzer a skill wraps, resolved
   at run time and failing loudly if absent. No lockfile can rot.
3. **Open standard, tracked.** Skills follow the Agent Skills specification at
   agentskills.io, and CI watches that spec for changes (see
   `.github/workflows/spec-watch.yml`). Runtime-specific manifests
   (`.claude-plugin/`) are additive; removing a runtime never removes a skill.
4. **Behaviour contracts are the model-compatibility test.** When a new model or
   runtime appears, `scripts/eval_runner.py` replays every scenario through it
   and records the outcome in `docs/MODEL_COMPATIBILITY.md`. A skill is
   "compatible" with a model only when the run is recorded.
5. **Claims are machine-verifiable.** `scripts/evidence.py` writes
   `EVIDENCE.json` (validator result, test count, versions, commit, date) on
   every release. Anyone — human or agent — can regenerate it and diff.
6. **Decisions are recorded, including rejected ones.** `docs/adr/` holds an
   Architecture Decision Record per structural choice. A future maintainer
   (possibly an agent) should never have to guess why.
7. **Fail loudly, never fabricate.** Every script exits non-zero with an
   actionable message when its precondition is missing. No skill may report
   results it did not obtain.

## Enforced vs policy

| Enforced by CI or code | Policy (human/agent judgement) |
|---|---|
| Spec-valid frontmatter (`scripts/validate.py`) | Which skills earn a slot (tool- or contract-backed only) |
| ≥ 3 scenarios per skill, ≥ 1 static | Scenario quality |
| Stdlib-only imports (`tests/test_repo_contracts.py`) | When to bump versions |
| Plugin manifests list exactly the shipped skills | Model-compatibility recording cadence |
| Wrapper argv hygiene, exit codes | Deprecation timing |
| Spec-page drift detection (weekly) | How to adapt to a spec change |

## Versioning

- Each skill carries `metadata.version` (semver). Behaviour change → minor;
  wording/doc → patch; breaking CLI or output change → major, with the old
  behaviour kept for one minor release under a flag when feasible.
- The plugin version (`.claude-plugin/plugin.json`) is the release version and
  moves on every release. Git tags `vX.Y.Z` match it; a GitHub release attaches
  `EVIDENCE.json`.

## Deprecation

A skill is deprecated, not deleted: its `description` gains the prefix
`DEPRECATED:` with the replacement named, it stays for two minor releases, then
moves to `skills/_retired/` with its last `EVIDENCE.json`. Retired skills are
excluded from the plugin manifest but remain readable.

## Review cadence

| When | What | Where recorded |
|---|---|---|
| Every PR | validate + tests + gate smoke | CI |
| Weekly (automated) | spec page hash check | `spec-watch` workflow → issue on change |
| On each new major model/runtime release | `eval_runner.py` over all scenarios | `docs/MODEL_COMPATIBILITY.md` |
| Quarterly | dependency floor review (`cqa-analyzer` CLI/schema changes), links, this charter | `CHANGELOG.md` |
| Yearly | prune retired skills, re-read ADRs, confirm the principles still hold | ADR amendment |

## Exit criteria

Retire the repository when either holds for a full year: no skill has a
recorded compatible model, or the Agent Skills spec has been superseded and no
maintained runtime loads `SKILL.md`. Retirement means archiving with a final
`EVIDENCE.json`, not deletion.

## Evidence freshness

`python3 scripts/evidence.py` runs validation and full unittest discovery once,
then writes `EVIDENCE.json`. `--check` reruns those checks, requires positive
collected tests and green stored/current results, and compares a deterministic
SHA-256 digest of repository inputs. A failing collection never replaces evidence.
Commit, date and Python version are provenance, not freshness keys: committing
unchanged inputs together with evidence must not make the evidence stale.

Inputs are all existing tracked and non-ignored untracked files, including their
relative paths, contents and executable bits. `EVIDENCE.json`, generated
`evals/runs/` results, Python bytecode and pytest caches are excluded. Symlinked
inputs are refused. Inputs changing while validation runs invalidate collection.
Generate/check on the CI reference interpreter (Python 3.12) for release evidence;
the exact collected test count must agree. Other supported Python versions run
validation/tests but do not require identical skip outcomes. Evidence is not a
signature or proof of model compatibility, and no recorded green flag replaces
rerunning validation. CI checks freshness in pull requests as well as on main.

Repository tests import/mock the collector and CLI; they must never launch the
evidence subprocess from within full discovery (which would recurse).
