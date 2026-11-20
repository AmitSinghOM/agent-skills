# Model and runtime compatibility

A skill is compatible with a model or runtime only when a scenario replay has
been run and graded. This file is the record. Absence of a row means *unknown*,
not incompatible.

## Procedure (per new major model or runtime release)

1. Replay the static scenarios (no tools needed):
   ```bash
   scripts/eval_runner.py --runner "<cli that answers a prompt>" \
       --runner-id "<runtime> <version>, <model>" --label <runtime>-<version>
   ```
   Add `--mode all` when the skills' tools are installed in that environment.
   `--runner-id` is what you state the runner is; the script records it
   verbatim and never probes `--version`. `--dry-run` checks inputs and writes
   provenance records without calling the runner.
2. Grade `evals/runs/<label>.jsonl`: for each record, mark every
   `expected_behavior` met or unmet. Grading may be done by a human or by a
   grader model; record which, and never let a model grade its own run.
   Every record is written as `grading.status: "ungraded"`. `process.status`
   (`exited`, `timeout`, `output_limit`, `spawn_error`, `not_run`) and
   `process.exit_code` describe the process only: exit 0 is not a pass.
3. Add a row below. A skill passes when every static scenario has all expected
   behaviours met. Anything less is recorded as the fraction, not rounded up.
4. If a skill fails on a new model because of wording, fix the `SKILL.md`
   wording and bump the patch version; if it fails because the model ignores
   the contract, that is a finding about the model, not a reason to weaken the
   contract.

## Matrix

| Runtime / model | Date | architecture-baseline | code-quality-gate | learning-accelerator | source-grounded-claims | Grader | Notes |
|---|---|---|---|---|---|---|---|
| Kiro CLI (Bedrock default model) | 2026-09-12 | not run | not run | not run | manual spot-check 1/4 | human | rag-assistant era check; full replay pending |

Update the table with every replay, including failed ones. Runs are stored in
`evals/runs/`. Review and redact sensitive queries and outputs before explicitly
approving any commit or sharing; local mode 0600 does not protect a Git upload.

## Run file contract and limits

- One JSONL record per scenario (`record_version` 2) carrying `runner_id`, a
  hash of the runner argv (the argv itself may hold tokens, so it is not
  stored), `skill_sha256`, `scenarios_file_sha256`, `scenario_sha256` and
  `prompt_sha256`. The prompt is built from the same `SKILL.md` bytes that
  were hashed. Stdout and stderr are kept in separate fields.
- Records contain the scenario query and expected behaviours verbatim (dry
  runs too) and the runner's capped output. The file is created mode `0600`
  with exclusive creation: an existing name, a symlinked file, or a symlinked
  `evals/` or `evals/runs/` is refused, and nothing is overwritten. Labels
  must be plain basenames (`[A-Za-z0-9][A-Za-z0-9._-]{0,99}`).
- `--timeout` (1..3600 s, default 300) and `--max-output-bytes` (combined
  stdout+stderr, 1 B..16 MiB, default 1 MiB) are enforced per scenario. On
  either limit the runner's whole process group gets SIGTERM, then SIGKILL
  after a short grace period. Leftover group members are also killed after a
  normal exit.
- Real runs need POSIX process groups (`killpg` and either `waitid` with
  `WNOWAIT` or the macOS/BSD `kqueue` fallback);
  elsewhere they fail before writing anything. Processes that leave the group
  by starting their own session (`setsid`, daemonising) cannot be tracked or
  killed; the run still ends on the deadline.
