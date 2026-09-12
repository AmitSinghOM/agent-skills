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
#   run-gate.sh --base origin/main [--format sarif|json|text] [--fail-on warning|error]
#               [--baseline .code-quality-baseline.json] [--strict] [--report-only]
#   run-gate.sh --staged ...                  # pre-commit: gate staged lines
#   run-gate.sh --write-baseline .code-quality-baseline.json   # one-time on a legacy repo
#
# Exit codes are the analyzer's: 0 clean, 4 gate failed; 3 = this wrapper's
# own configuration error.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CQA_CMD="${CQA_CMD:-}"
BASE=""; STAGED=0; FORMAT="text"; FAIL_ON="warning"; BASELINE=""; WRITE_BASELINE=""
STRICT=0; REPORT_ONLY=0; PROJECT="."

die() { printf '[code-quality-gate] %s\n' "$1" >&2; exit "${2:-3}"; }
usage() { sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base)            BASE="$2"; shift 2 ;;
    --staged)          STAGED=1; shift ;;
    --format)          FORMAT="$2"; shift 2 ;;
    --fail-on)         FAIL_ON="$2"; shift 2 ;;
    --baseline)        BASELINE="$2"; shift 2 ;;
    --write-baseline)  WRITE_BASELINE="$2"; shift 2 ;;
    --strict)          STRICT=1; shift ;;
    --report-only)     REPORT_ONLY=1; shift ;;
    --project)         PROJECT="$2"; shift 2 ;;
    -h|--help)         usage ;;
    *) die "unknown flag: $1 (see --help)" ;;
  esac
done

case "$FORMAT" in json|sarif|text) ;; *) die "--format must be json|sarif|text (got '$FORMAT')" ;; esac
case "$FAIL_ON" in warning|error) ;; *) die "--fail-on must be warning|error (got '$FAIL_ON')" ;; esac

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

args=("$PROJECT" --output-format "$FORMAT" --offline)
[[ "$STRICT" -eq 1 ]] && args+=(--strict)

# --- baseline-writing mode (no gate) --------------------------------------
if [[ -n "$WRITE_BASELINE" ]]; then
  args+=(--write-baseline "$WRITE_BASELINE")
  printf '[code-quality-gate] writing baseline via %s\n' "$ANALYZER" >&2
  exec "$ANALYZER" "${args[@]}"
fi

# --- gate mode -------------------------------------------------------------
[[ -n "$BASE" || "$STAGED" -eq 1 ]] || die "pass --base <ref> or --staged (or --write-baseline for a one-time baseline)"

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
# operands (refs, paths) into CI logs where terminal escapes could be injected.
printf '[code-quality-gate] running: %s --output-format %s%s%s%s\n' "$ANALYZER" "$FORMAT" \
  "$([[ -n "$BASELINE" ]] && echo ' --baseline <file> --new-findings-only')" \
  "$([[ "$REPORT_ONLY" -eq 1 ]] || echo " --fail-on $FAIL_ON")" \
  "$([[ "$STRICT" -eq 1 ]] && echo ' --strict')" >&2

set +e
"$ANALYZER" "${args[@]}"
rc=$?
set -e
if [[ "$REPORT_ONLY" -eq 1 && "$rc" -eq 4 ]]; then rc=0; fi
exit "$rc"
