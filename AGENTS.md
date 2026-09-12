# AGENTS.md

Guidance for coding agents working in or with this repository.

## If you are *using* these skills

- Load a skill only when its `description` matches the task. Descriptions are
  written as "use when …" triggers on purpose.
- `code-quality-gate` runs external tools (`git`, `python3`, `cqa-analyzer`). If
  the analyzer is missing the wrapper exits 3 with an install hint — relay that
  to the user. Never report findings you did not obtain from a real run.
- `source-grounded-claims` applies to your own output. If you cite a source, you
  opened it. If you did not, say `unverified` and keep confidence below 80%.

## If you are *changing* this repository

1. One directory per skill under `skills/`. `name` in the frontmatter must equal
   the directory name. Frontmatter keys are limited to the spec set
   (`name`, `description`, `license`, `compatibility`, `metadata`, `allowed-tools`);
   anything else goes under `metadata`.
2. Every skill ships `evals/scenarios.json` with at least three scenarios and at
   least one `mode: static` (tool-free) scenario. Scenarios describe observable
   behaviour, not vibes.
3. Scripts are standard-library only and must fail loudly with an actionable
   message. No network calls.
4. Before proposing a change, run and paste the output of:
   ```bash
   python3 scripts/validate.py && python3 -m unittest discover -s tests
   ```
   Both must pass. CI runs the same two commands.
5. Bump `metadata.version` in the skill you changed and `version` in
   `.claude-plugin/plugin.json` when behaviour changes.
6. Do not add a skill that is only a prompt. A skill earns a slot here by being
   backed by a runnable tool or by scenarios that have actually been exercised.
7. Do not add anything referencing an employer's internal systems. This
   repository is public.
