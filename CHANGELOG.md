# Changelog

All notable changes. Versions are the plugin/release version in
`.claude-plugin/plugin.json`; each skill also carries its own `metadata.version`.
Format follows Keep a Changelog.

## [Unreleased]

### Fixed
- Evidence collection no longer recursively invokes itself through contract tests.
  Freshness uses validated input content rather than an exact Git commit, requires
  positive test collection and green current/stored results, and is checked in PRs.
  Failed generation does not replace prior evidence or unrelated files.
- Changed-line and evaluation-runner safety fixes are local, unreleased work.
  See `docs/CHANGED_LINE_SAFETY.md` and `docs/MODEL_COMPATIBILITY.md` for contracts
  and limits. No model replay or compatibility claim is established by these tests.

## [1.3.0] - 2026-09-13

### Added
- Longevity structure: `docs/LONGEVITY.md` (principles, enforced-vs-policy, versioning,
  deprecation, review cadence, exit criteria); ADRs 0001–0003; `SECURITY.md`,
  `CONTRIBUTING.md`, this changelog.
- `scripts/eval_runner.py`: model-agnostic scenario replay through any CLI, results to
  `evals/runs/<label>.jsonl`; `docs/MODEL_COMPATIBILITY.md` records graded runs.
- `scripts/evidence.py`: machine-verifiable `EVIDENCE.json` (validator result, test count,
  versions, commit); `--check` fails when stale. CI enforces it on `main`.
- `spec-watch` workflow: weekly hash check of the Agent Skills spec source
  (`docs/spec.sha256`), opens an issue on drift. Dependabot for Actions.
- Contract test: scripts import standard library only.

### Changed
- Skill versions aligned: every skill at its own semver, recorded in `EVIDENCE.json`.

## [1.2.0] - 2026-09-13
### Added
- `architecture-baseline`: observed-architecture snapshot from cqa-analyzer's pattern map,
  `ARCHITECTURE.md` generation, drift check with exit 4.

## [1.1.1] - 2026-09-13
### Fixed
- `code-quality-gate`: manifest generator falls back to "every tracked line" when the base
  ref cannot be compared (single-commit repos, shallow clones) instead of crashing.

## [1.1.0] - 2026-09-12
### Added
- `code-quality-gate`: config pinning (`--config`, `--no-project-config`,
  `--expect-config-fingerprint`, exit 6); hardened wrapper arg parsing (exit 2 usage errors);
  fake-analyzer wrapper test suite.

## [1.0.0] - 2026-09-12
### Added
- Initial release: `code-quality-gate`, `learning-accelerator`, `source-grounded-claims`;
  spec validator with negative tests; Claude plugin manifests; CI 3.10–3.13 + live analyzer smoke.
