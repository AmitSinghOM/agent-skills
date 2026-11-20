# Contributing

Contributions from humans and from coding agents are both welcome; the bar is the
same. Read `docs/LONGEVITY.md` first — it explains why the rules below exist.

## Before opening a pull request

```bash
python3 scripts/validate.py                       # spec + repo contract
python3 -m unittest discover -s tests             # all suites
python3 scripts/evidence.py                       # regenerate EVIDENCE.json
```

All three must pass. Paste the last line of each into the PR description.

## Adding a skill

1. It must be backed by a runnable stdlib-only script **or** a behaviour contract
   that has actually been exercised. A prompt with a nice name is not enough
   (ADR 0001).
2. Directory `skills/<name>/` with `SKILL.md` (spec frontmatter, `name` == directory,
   `metadata.version: "1.0.0"`), `evals/scenarios.json` (≥ 3 scenarios, ≥ 1 static).
3. Add it to `.claude-plugin/marketplace.json`, the README table, and
   `tests/test_repo_contracts.py`'s shipped-skills list.
4. Tests for any script: happy path, every error path, and the "tool missing →
   fail loudly, never fabricate" path.

## Changing a skill

- Behaviour change → bump the skill's `metadata.version` minor; wording → patch.
- Bump `.claude-plugin/plugin.json` `version` and add a `CHANGELOG.md` entry.
- If the analyzer's CLI or report schema changed, say which version and what.

## Commit messages

Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`), imperative mood,
≤ 50-char subject, body explains *why*.

## Releasing

Maintainers only. Tag `vX.Y.Z` matching `plugin.json`, create a GitHub release,
attach the regenerated `EVIDENCE.json`, and update `docs/MODEL_COMPATIBILITY.md`
if a replay was run for the release.

## What will be declined

- Dependencies outside the standard library (ADR 0001).
- Skills that reference any employer's internal systems (this repo is public).
- Re-snapshotting or re-baselining to turn a red check green.
- Model-specific wording hacks without a recorded scenario failure that justifies them.
