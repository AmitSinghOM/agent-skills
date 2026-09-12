# Changed-lines manifest — what `changed_lines_manifest.py` emits

Schema version `1.0.0`, as documented by the analyzer
([docs/CHANGED_LINES.md](https://github.com/AmitSinghOM/code-quality-analyzer/blob/main/docs/CHANGED_LINES.md)):

```json
{
  "schema_version": "1.0.0",
  "files": [
    {
      "path": "src/service.py",
      "ranges": [
        {"start_line": 12, "end_line": 18},
        {"start_line": 27, "end_line": 27}
      ]
    }
  ]
}
```

## Generator behaviour

- Source: `git diff --unified=0 --diff-filter=AMR <base>...HEAD` (merge-base
  semantics) or `--cached` for `--staged`.
- Only the **new side** of each hunk is recorded (`+start,count`). Pure
  deletions (`count == 0`) and deleted files contribute nothing — there is no
  current line for a finding to land on.
- Binary files are skipped.
- Overlapping or adjacent ranges are merged; files are sorted; output is
  deterministic for a given diff.
- Paths are repo-relative POSIX (`b/` prefix stripped), which is the identity the
  analyzer matches on regardless of `--redact-paths` or `--anonymize`.

## Analyzer-side validation you can rely on

The analyzer rejects: absolute, scheme-based, Windows-drive, backslash, NUL,
dot, empty-segment or parent-traversing paths; duplicate file entries; empty
range lists; unknown keys; unsupported schema versions; non-UTF-8 or
non-strict JSON. An empty `files` array is valid and selects nothing.

Paths need not exist — a changed file the analyzer does not scan simply never
matches a finding.

## Selection order inside the analyzer

1. Analyze the **full** project (architecture score, `--strict`, `--fail-under`
   stay full-project).
2. `--write-baseline` (if requested) from all findings — never a partial baseline.
3. Compare with `--baseline`; apply `--new-findings-only`.
4. Intersect with the manifest.
5. Render; apply `--fail-on` to the selected set.

So a changed-line run can never accidentally write a baseline that only covers
the diff.
