# Security

## What runs

- Scripts are Python standard library only; no third-party packages are installed
  by this repository.
- `code-quality-gate` and `architecture-baseline` execute one external program:
  `code-quality-analyzer`, resolved from `PATH` or from `CQA_CMD`. `CQA_CMD` must be
  an absolute path to an executable file; bare names and relative paths are refused
  to prevent PATH hijacking in CI.
- Wrappers build argv as arrays; user operands (refs, paths, digests) are never
  interpolated into a shell string and never echoed into logs.
- The analyzer is always invoked with `--offline`, which denies socket and
  name-resolution operations while it runs.
- `eval_runner.py` executes the runner command *you* pass it. It sends skill text
  and scenario queries to that command; do not point it at a runtime you would not
  trust with the repository's contents.

## What does not run

Nothing in this repository phones home, collects telemetry, or fetches code at run
time. The weekly `spec-watch` workflow fetches one text file from the Agent Skills
repository over HTTPS and compares a hash; it executes nothing from it.

## Reporting

Open a private security advisory on GitHub
(Security → Report a vulnerability) or email the address on
https://amitsinghom.github.io/. Expect an acknowledgement within 7 days. Please
do not open a public issue for a vulnerability.

## Supported versions

The latest release on `main`. Older tags receive fixes only when the fix is a
one-line backport.
