# Changed-line manifest safety

`skills/code-quality-gate/scripts/changed_lines_manifest.py` turns git state into
the analyzer's changed-lines manifest (schema 1.0.0). `run-gate.sh` uses it to
decide which findings can fail a build, so a wrong manifest produces a false
"clean". This page records what the generator guarantees, how it differs from
the core analyzer's `diff_to_manifest`, and what is still out of scope.

## Guarantees

**The manifest describes the tree that is scanned.**
- `--base REF` diffs `merge-base(REF, HEAD)` against the *working tree*, so
  uncommitted edits are covered. Untracked, non-ignored text files are added as
  wholly new (lines `1..N`; a file too large to count becomes `1..2147483647`).
- `--staged` diffs the index against HEAD. It refuses (exit 3) when tracked files
  under `--repo` have unstaged edits, because the analyzer would read different
  lines. Fix: stage them, or run `git stash --keep-index`.

**Paths are relative to the scanned directory.** Every git call runs as
`git -C <repo> diff --relative ... -- .`, so `--project svc` yields `app.py`, not
`svc/app.py`, and sibling directories (for example `svc-old/`) are excluded.

**Refs are revisions, never options.** A ref goes through
`rev-parse --verify --quiet --end-of-options REF^{commit}` to get a hash; only
hashes reach `merge-base` and `diff`. The wrapper passes `--base=REF`,
`--repo=DIR` and `--output=FILE`, so a value such as `-p` stays a value.

**The fallback is announced, and only taken when there is nothing to compare.**
If REF does not resolve, HEAD is unborn, or there is no merge-base, every tracked
line is treated as changed (empty tree compared to the worktree), and stderr
says `treating every tracked line as changed`. Every other git failure (not a
repository, git missing or older than 2.24, a failed diff) is exit 3. It never
becomes a fallback and never becomes an empty manifest.

**External diff and textconv programs are disabled.** The diff uses `--no-ext-diff` and
`--no-textconv`. `GIT_EXTERNAL_DIFF`, `GIT_DIFF_OPTS` and `GIT_CONFIG_PARAMETERS`
are dropped, `core.fsmonitor` is disabled, and `GIT_OPTIONAL_LOCKS=0` is set.
`--text` means a `-diff`/`binary` attribute cannot hide changed lines.
Output-shaping config is pinned: quoted paths, `a/` and `b/` prefixes, no
color, zero context, no blank-line suppression.

**Parsing fails closed.** Hunk counts drive the parser, and lines are split on
LF only. Content that looks like a header (`+++ b/x`, `@@ ...`, `diff --git`) or
contains `\f` or U+2028 stays content. In strict mode (all git output) exit 3 is
returned for any of the following:
- a truncated or overlong hunk
- an unexpected line inside a hunk
- a hunk before its `+++` header
- combined (conflict) diffs
- malformed quoted paths, or quoted paths that are not UTF-8
- paths the analyzer rejects (backslash, `..`, empty segment, absolute, URL scheme)
- line numbers above 32 bits
- input that is not a diff

**Bounded.** The diff stream is capped at 256 MiB and is read in chunks of at
most 1 MiB per line; only the first chunk of a long line is kept. The output is
held to the analyzer's limits: 20,000 files, 100,000 ranges, 4,096-character
paths, and 5 MB. Indented JSON falls back to compact JSON, and anything still
over a limit is an error.

**Deletions (opt-in, always on in `run-gate.sh`).** With `--include-deletions` a
pure deletion is anchored to the line that now follows it. That matches core,
so a removed guard still gates the code it protected. A deletion at end of file
has no following line, so it is clamped to the new last line.

## Compatibility kept

- `parse_diff(text)` defaults are unchanged: added/modified lines only. It still
  tolerates a hand-trimmed deleted-file (`+++ /dev/null`) hunk.
  `parse_diff(text, include_deletions=True, strict=True)` is the safe mode.
- `git_diff`, `merge_ranges` and `build_manifest` keep their signatures. There is
  still no import of the analyzer, and only the standard library is used.
- `CQA_CMD` must still be an absolute executable, and arguments are still arrays
  with no shell interpolation.
- Behaviour change: `--base` now compares against the working tree rather than
  `BASE...HEAD`. On a clean checkout the result is identical. `--base` and
  `--staged` are now mutually exclusive (exit 2).

## Differences from core `diff_to_manifest` (deliberate)

Parity is only claimed for the five `GOLDEN` fixtures in
`tests/test_manifest_safety.py`. They were compared against core 3.4.1 with
`CQA_CORE_SRC=<checkout> python3 -m unittest tests.test_manifest_safety`.

1. LF-only splitting. Core used `splitlines()`; it is fixed in the local unreleased core patch.
2. End-of-file deletion anchors are clamped to the last line on the git path.
   Bare-text `parse_diff` matches core.
3. Strict mode also rejects: stray `+`/`-`/space lines after a hunk ends, empty
   lines inside a hunk, `@@` before `+++`, non-UTF-8 or invalid quoted paths
   (core keeps a latin-1 decode), and combined diffs.
4. A `diff --git` header resets the current file.
5. The diff limit is 256 MiB streamed, not 5 MB of text, because the all-tracked
   fallback can be large. The output limits are the same as core's.
6. Untracked files are included in `--base` mode.

## Known limits

- The tree can change between manifest generation and the scan. Run both in one
  job and do not modify the checkout in between.
- Gitignored files that the analyzer still scans are never gated. In `--staged`
  mode untracked files are scanned but not gated, because they are not part of
  the commit.
- Unresolved merge conflicts, and any non-UTF-8 path in scope, fail with exit 3.
- A submodule pointer change yields a line-1 entry for the submodule path.
- The wrapper rejects option values that start with `--`.
