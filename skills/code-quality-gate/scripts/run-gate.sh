#!/usr/bin/env bash
# run-gate.sh — changed-line code-quality gate on top of cqa-analyzer.
#
# Generates a changed-lines manifest from git (the analyzer never invokes git
# itself), then runs `code-quality-analyzer` so that only NEW findings on
# CHANGED lines can fail the build. Full-project signals are still computed.
#
# Requirements: python3, git, and the analyzer on PATH or via CQA_CMD:
#     pipx install cqa-analyzer        # or: pip install cqa-analyzer
#
# Usage:
#   run-gate.sh --base <ref> [--baseline FILE] [--format sarif|json|text] [--fail-on warning|error]
#               [--strict] [--report-only] [--config FILE | --no-project-config]
#               [--expect-config-fingerprint SHA256] [--project DIR]
#   run-gate.sh --staged ...                        # pre-commit: gate staged lines
#   run-gate.sh --write-baseline FILE [...]         # one-time on a legacy repo (no gate)
#
# Exit codes: 0 clean · 4 gate failed · 6 config fingerprint mismatch (analyzer)
#             2 usage error · 3 environment error (this wrapper)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CQA_CMD="${CQA_CMD:-}"
BASE=""; STAGED=0; FORMAT="text"; FAIL_ON="warning"; BASELINE=""; WRITE_BASELINE=""
STRICT=0; REPORT_ONLY=0; PROJECT="."; CONFIG=""; NO_PROJECT_CONFIG=0; EXPECT_FP=""

die()   { printf '[code-quality-gate] %s\n' "$1" >&2; exit "${2:-3}"; }
usage() { sed -n '2,19p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0; }

# need_value FLAG NEXT — a value must exist and must not itself look like a flag.
need_value() {
  [[ $# -ge 2 && -n "${2:-}" && "${2:0:2}" != "--" ]] || die "$1 requires a value" 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base)                      need_value "$1" "${2:-}"; BASE="$2"; shift 2 ;;
    --staged)                    STAGED=1; shift ;;
    --format)                    need_value "$1" "${2:-}"; FORMAT="$2"; shift 2 ;;
    --fail-on)                   need_value "$1" "${2:-}"; FAIL_ON="$2"; shift 2 ;;
    --baseline)                  need_value "$1" "${2:-}"; BASELINE="$2"; shift 2 ;;
    --write-baseline)            need_value "$1" "${2:-}"; WRITE_BASELINE="$2"; shift 2 ;;
    --config)                    need_value "$1" "${2:-}"; CONFIG="$2"; shift 2 ;;
    --no-project-config)         NO_PROJECT_CONFIG=1; shift ;;
    --expect-config-fingerprint) need_value "$1" "${2:-}"; EXPECT_FP="$2"; shift 2 ;;
    --project)                   need_value "$1" "${2:-}"; PROJECT="$2"; shift 2 ;;
    --strict)                    STRICT=1; shift ;;
    --report-only)               REPORT_ONLY=1; shift ;;
    -h|--help)                   usage ;;
    *) die "unknown flag: $1 (see --help)" 2 ;;
  esac
done

case "$FORMAT"  in json|sarif|text) ;; *) die "invalid value for --format: '$FORMAT' (json|sarif|text)" 2 ;; esac
case "$FAIL_ON" in warning|error)   ;; *) die "invalid value for --fail-on: '$FAIL_ON' (warning|error)" 2 ;; esac
[[ -n "$CONFIG" && "$NO_PROJECT_CONFIG" -eq 1 ]] && die "--config and --no-project-config are mutually exclusive" 2
[[ -n "$EXPECT_FP" && ! "$EXPECT_FP" =~ ^[0-9a-fA-F]{64}$ ]] && die "--expect-config-fingerprint must be a 64-hex SHA-256" 2

# Exactly one scan selector.
selectors=0
[[ -n "$BASE" ]]           && selectors=$((selectors+1))
[[ "$STAGED" -eq 1 ]]      && selectors=$((selectors+1))
[[ -n "$WRITE_BASELINE" ]] && selectors=$((selectors+1))
[[ "$selectors" -eq 0 ]] && die "pass exactly one of --base <ref>, --staged, or --write-baseline <file>" 2
[[ "$selectors" -gt 1 ]] && die "--base, --staged and --write-baseline are mutually exclusive" 2

# --- resolve the analyzer -------------------------------------------------
# If CQA_CMD is set it MUST be an absolute path to an executable: a bare name
# resolved through PATH in CI is a PATH-hijack vector. If unset, fall back to
# `code-quality-analyzer` on PATH and record what was resolved.
if [[ -n "$CQA_CMD" ]]; then
  case "$CQA_CMD" in /*) ;; *) die "CQA_CMD must be an absolute path (got '$CQA_CMD')" ;; esac
  [[ -f "$CQA_CMD" && -x "$CQA_CMD" ]] || die "CQA_CMD='$CQA_CMD' is not an executable file"
  ANALYZER="$CQA_CMD"
else
  ANALYZER="$(command -v code-quality-analyzer || true)"
  [[ -n "$ANALYZER" ]] || die "code-quality-analyzer not found on PATH. Install: pipx install cqa-analyzer (or set CQA_CMD=/abs/path)"
fi

# argv is an array: operands are never interpolated into a shell string.
args=("$PROJECT" --output-format "$FORMAT" --offline)
[[ "$STRICT" -eq 1 ]]            && args+=(--strict)
[[ -n "$CONFIG" ]]               && args+=(--config "$CONFIG")
[[ "$NO_PROJECT_CONFIG" -eq 1 ]] && args+=(--no-project-config)
[[ -n "$EXPECT_FP" ]]            && args+=(--expect-config-fingerprint "$EXPECT_FP")

# --- baseline-writing mode (no gate) --------------------------------------
if [[ -n "$WRITE_BASELINE" ]]; then
  args+=(--write-baseline "$WRITE_BASELINE")
  printf '[code-quality-gate] writing baseline via %s\n' "$ANALYZER" >&2
  exec "$ANALYZER" "${args[@]}"
fi

# --- gate mode -------------------------------------------------------------
MANIFEST="$(mktemp "${TMPDIR:-/tmp}/cqa-changed-lines.XXXXXX")"
trap 'rm -f "$MANIFEST"' EXIT
if [[ "$STAGED" -eq 1 ]]; then
  python3 "$HERE/changed_lines_manifest.py" --staged --repo "$PROJECT" --output "$MANIFEST"
else
  python3 "$HERE/changed_lines_manifest.py" --base "$BASE" --repo "$PROJECT" --output "$MANIFEST"
fi
args+=(--changed-lines-manifest "$MANIFEST")

if [[ -n "$BASELINE" ]]; then
  [[ -f "$BASELINE" ]] || die "baseline '$BASELINE' not found; create it once with --write-baseline"
  args+=(--baseline "$BASELINE" --new-findings-only)
fi

if [[ "$REPORT_ONLY" -eq 1 ]]; then
  printf '[code-quality-gate] REPORT ONLY: this run will exit 0 and will NOT block the build even if findings exist.\n' >&2
else
  args+=(--fail-on "$FAIL_ON")
fi

# Log resolved binary and flag *values* only; never echo user-controlled
# operands (refs, paths, digests) into CI logs where terminal escapes could be
# injected.
printf '[code-quality-gate] running: %s --output-format %s%s%s%s%s%s\n' "$ANALYZER" "$FORMAT" \
  "$([[ -n "$BASELINE" ]] && echo ' --baseline <file> --new-findings-only')" \
  "$([[ "$REPORT_ONLY" -eq 1 ]] || echo " --fail-on $FAIL_ON")" \
  "$([[ "$STRICT" -eq 1 ]] && echo ' --strict')" \
  "$([[ -n "$CONFIG" ]] && echo ' --config <file>')$([[ "$NO_PROJECT_CONFIG" -eq 1 ]] && echo ' --no-project-config')" \
  "$([[ -n "$EXPECT_FP" ]] && echo ' --expect-config-fingerprint <sha256>')" >&2

set +e
"$ANALYZER" "${args[@]}"
rc=$?
set -e
if [[ "$REPORT_ONLY" -eq 1 && "$rc" -eq 4 ]]; then rc=0; fi
exit "$rc"
