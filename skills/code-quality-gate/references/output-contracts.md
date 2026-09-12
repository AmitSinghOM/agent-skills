# Output contracts — code-quality-gate

Field shapes below were captured from real `cqa-analyzer` runs (2.34–2.44), not
inferred. The analyzer's own schema is versioned (`schema_version` in JSON);
check it before depending on a field.

## Exit codes

| Code | Source | Meaning |
|---|---|---|
| `0` | analyzer | No selected finding met `--fail-on`; or `--report-only` |
| `4` | analyzer | Gate failed: at least one selected finding at or above `--fail-on` |
| `5` | analyzer | Score not applicable (no signal-capable language analyzed) — only with `--fail-under` |
| `6` | analyzer | Effective configuration differs from `--expect-config-fingerprint`; nothing was scanned |
| `2` | wrapper | Usage error: missing value, invalid enum, conflicting selectors, unknown flag |
| `3` | wrapper | Environment error: analyzer not found, non-absolute `CQA_CMD`, missing baseline file |
| other non-zero | analyzer | `--strict` violation, invalid manifest, unreadable project |

## JSON (`--format json`)

Top-level keys (selected):

```
schema_version            e.g. "1.12.0"
analyzer_version          e.g. "2.42.0"
scoring_policy_version    e.g. "2.0.0"  (scores are not comparable across policies)
configuration_fingerprint SHA-256 of the validated effective config (no paths/source);
                          the value to pin with --expect-config-fingerprint
analysis_health           {complete, authoritative, source_candidates, files_read,
                           files_successfully_analyzed, completeness_ratio, reasons[]}
scan_health               file-discovery health (skips, size limits)
changed_lines             {schema_version, file_count, range_count,
                           input_findings, selected_findings}       ← only with a manifest
findings[]                the SELECTED findings (after baseline + changed-line filtering)
finding_summary           counts by severity
architecture_signal_score / _label / _scope, rating, label
language_adapters, package_intelligence, project_analyses, privacy
```

One finding:

```json
{
  "rule_id": "PY-MAINT-001",
  "category": "maintainability",
  "severity": "warning",
  "confidence": "high",
  "message": "Function '_directory_names' has cyclomatic complexity 12 (limit 10).",
  "location": {"path": "cqa_analyzer/config.py", "line": 339, "column": 1},
  "remediation": "Extract independent decisions into focused helper functions."
}
```

Gate logic reads: `findings[]` (what to fix), `changed_lines.selected_findings`
(how many made it through the filters), `analysis_health.complete` (whether an
empty `findings[]` is trustworthy).

## SARIF (`--format sarif`)

SARIF 2.1.0. One run; `runs[0].tool.driver.name == "Code Quality Analyzer"`;
`runs[0].results[]` are the selected findings mapped to SARIF results with
`ruleId`, `level`, `message.text`, and `locations[].physicalLocation` (path +
region line). `runs[0].properties` carries the analyzer's health/score metadata.

Upload with GitHub's `codeql-action/upload-sarif` (or any SARIF-consuming code
host) to annotate changed lines inline. Do not hand-edit the file.

## Baseline file (`--write-baseline`)

JSON of hashed finding fingerprints — no source text, safe to commit. Rewrite it
deliberately (a one-time `--write-baseline` run) when you accept a new floor;
never regenerate it inside the gated run, which would silently accept every new
finding.

## Text (`--format text`)

Human-readable summary. Not stable; do not parse it.
