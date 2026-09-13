#!/usr/bin/env python3
"""Capture a codebase's architecture from cqa-analyzer output, then check drift.

Two modes:

  snapshot   Run the analyzer (or read an existing JSON with --from-json) and write
             ARCHITECTURE.snapshot.json (machine) + a generated section inside
             ARCHITECTURE.md (human). Hand-written text outside the markers is kept.
  check      Re-analyze and compare against the snapshot. Reports layers that
             appeared, patterns that moved into layers they never lived in, patterns
             that vanished, and score/label movement. --fail-on-drift exits 4 on
             violations (pattern in a new layer, or score drop >= --max-score-drop).

What "layer" means: the first --layer-depth path components of each source file
(default 2: ``cloudscale/adapters``, ``app/services``, ``tests``). The analyzer
reports which files carry which design/DSA patterns; grouping those by layer gives
an observed map of where each concern lives. The map is *observed*, not declared —
treat it as conventions to confirm, and re-snapshot deliberately when they change.

Standard library only. The analyzer is only needed when not using --from-json.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

SNAPSHOT_VERSION = "1.0.0"
BEGIN = "<!-- architecture-baseline:begin (generated; edit outside these markers) -->"
END = "<!-- architecture-baseline:end -->"


# ---------------------------------------------------------------- analyzer ---
def run_analyzer(project: Path) -> dict:
    exe = os.environ.get("CQA_CMD") or shutil.which("code-quality-analyzer")
    if not exe:
        raise SystemExit("code-quality-analyzer not found on PATH. Install: pipx install cqa-analyzer "
                         "(or set CQA_CMD=/abs/path, or pass --from-json <report.json>)")
    if os.environ.get("CQA_CMD") and not os.path.isabs(exe):
        raise SystemExit("CQA_CMD must be an absolute path")
    r = subprocess.run([exe, str(project), "--output-format", "json", "--offline"],
                       capture_output=True, text=True)
    if r.returncode not in (0, 4):  # 4 = findings gate; irrelevant here, output is still complete
        raise SystemExit(f"analyzer failed ({r.returncode}): {r.stderr.strip()[:400]}")
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"analyzer did not emit JSON: {exc}")


# ---------------------------------------------------------------- model ------
def layer_of(path: str, depth: int) -> str:
    parts = path.replace("\\", "/").split("/")
    return "/".join(parts[:depth]) if len(parts) > depth else "/".join(parts[:-1]) or "."


def pattern_layers(patterns: dict, depth: int) -> dict[str, dict[str, int]]:
    """pattern -> {layer: file_count}, from the analyzer's pattern -> {files:[...]} map."""
    out: dict[str, dict[str, int]] = {}
    for name, info in sorted((patterns or {}).items()):
        files = info.get("files", []) if isinstance(info, dict) else []
        counts = Counter(layer_of(f, depth) for f in files)
        if counts:
            out[name] = dict(sorted(counts.items()))
    return out


def build_snapshot(report: dict, depth: int, project_name: str) -> dict:
    design = pattern_layers(report.get("design_patterns", {}), depth)
    dsa = pattern_layers(report.get("dsa_patterns", {}), depth)
    layers: Counter = Counter()
    for pmap in (design, dsa):
        for lay in pmap.values():
            for layer, n in lay.items():
                layers[layer] += n
    br = report.get("breakdown", {})
    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project": project_name,
        "layer_depth": depth,
        "analyzer_version": report.get("analyzer_version"),
        "scoring_policy_version": report.get("scoring_policy_version"),
        "schema_version": report.get("schema_version"),
        "score": report.get("architecture_signal_score"),
        "label": report.get("architecture_signal_label"),
        "breakdown": {k: br.get(k) for k in ("dsa_score", "design_score", "maturity_score",
                                              "design_patterns_count", "dsa_patterns_count",
                                              "files_scanned", "total_lines")},
        "languages": (report.get("architecture_signal_scope") or {}).get("by_language", {}) and
                     sorted((report.get("architecture_signal_scope") or {}).get("by_language", {}).keys()),
        "layers": dict(sorted(layers.items())),          # layer -> pattern-occurrence count
        "design_patterns": design,                        # pattern -> {layer: count}
        "dsa_patterns": dsa,
        "dependencies": (report.get("package_intelligence") or {}).get("dependencies", []),
    }


# ---------------------------------------------------------------- drift ------
def compare(base: dict, cur: dict, max_score_drop: float) -> dict:
    violations: list[str] = []
    notes: list[str] = []

    for kind in ("design_patterns", "dsa_patterns"):
        b, c = base.get(kind, {}), cur.get(kind, {})
        for pat, layers in c.items():
            if pat not in b:
                notes.append(f"new {kind[:-1].replace('_', ' ')} '{pat}' in {sorted(layers)}")
                continue
            moved = sorted(set(layers) - set(b[pat]))
            if moved:
                violations.append(f"'{pat}' now appears in {moved}; baseline layers were {sorted(b[pat])}")
        for pat in b:
            if pat not in c:
                notes.append(f"pattern '{pat}' no longer detected (was in {sorted(b[pat])})")

    new_layers = sorted(set(cur.get("layers", {})) - set(base.get("layers", {})))
    if new_layers:
        notes.append(f"new layers carrying patterns: {new_layers}")

    bs, cs = base.get("score"), cur.get("score")
    if isinstance(bs, (int, float)) and isinstance(cs, (int, float)):
        if cs <= bs - max_score_drop:
            violations.append(f"architecture score dropped {bs} -> {cs} (>= {max_score_drop})")
        elif cs != bs:
            notes.append(f"architecture score {bs} -> {cs}")
    if base.get("label") != cur.get("label"):
        notes.append(f"label {base.get('label')!r} -> {cur.get('label')!r}")
    if base.get("scoring_policy_version") != cur.get("scoring_policy_version"):
        notes.append(f"scoring policy {base.get('scoring_policy_version')} -> "
                     f"{cur.get('scoring_policy_version')}: scores are not comparable; re-snapshot")
    return {"violations": violations, "notes": notes}


# ---------------------------------------------------------------- render -----
def render_md(snap: dict) -> str:
    lines = [BEGIN, "",
             f"_Captured {snap['captured_at']} by cqa-analyzer {snap['analyzer_version']} "
             f"(scoring policy {snap['scoring_policy_version']}). Layer depth {snap['layer_depth']}._", "",
             f"**Architecture signal:** {snap['score']} — {snap['label']}  ",
             f"**Breakdown:** design {snap['breakdown'].get('design_score')} · "
             f"DSA {snap['breakdown'].get('dsa_score')} · maturity {snap['breakdown'].get('maturity_score')} · "
             f"{snap['breakdown'].get('files_scanned')} files / {snap['breakdown'].get('total_lines')} lines", "",
             "### Layers (by pattern occurrences)", "", "| Layer | Pattern occurrences |", "|---|---:|"]
    for layer, n in sorted(snap["layers"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{layer}` | {n} |")
    lines += ["", "### Where design patterns live", "", "| Pattern | Layers (files) |", "|---|---|"]
    for pat, layers in snap["design_patterns"].items():
        lines.append(f"| `{pat}` | " + ", ".join(f"`{l}` ({n})" for l, n in layers.items()) + " |")
    if snap["dsa_patterns"]:
        lines += ["", "### Where DSA patterns live", "", "| Pattern | Layers (files) |", "|---|---|"]
        for pat, layers in snap["dsa_patterns"].items():
            lines.append(f"| `{pat}` | " + ", ".join(f"`{l}` ({n})" for l, n in layers.items()) + " |")
    lines += ["", "### Observed constraints (confirm before relying on them)", ""]
    # A pattern confined to one layer is the strongest observable convention.
    confined = [(p, next(iter(l))) for p, l in snap["design_patterns"].items() if len(l) == 1]
    if confined:
        for p, layer in confined:
            lines.append(f"- `{p}` appears only in `{layer}`.")
    else:
        lines.append("- No design pattern is confined to a single layer.")
    lines += ["", END]
    return "\n".join(lines) + "\n"


def write_md(path: Path, generated: str) -> None:
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if BEGIN in text and END in text:
            pre, rest = text.split(BEGIN, 1)
            _, post = rest.split(END, 1)
            path.write_text(pre + generated.rstrip("\n") + post, encoding="utf-8")
            return
        path.write_text(text.rstrip("\n") + "\n\n## Architecture baseline\n\n" + generated, encoding="utf-8")
        return
    path.write_text("# Architecture\n\nHand-written context goes above or below the generated block.\n\n"
                    "## Architecture baseline\n\n" + generated, encoding="utf-8")


# ---------------------------------------------------------------- cli --------
def load_report(args) -> dict:
    if args.from_json:
        return json.loads(Path(args.from_json).read_text(encoding="utf-8"))
    return run_analyzer(Path(args.project))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("snapshot", "check"):
        p = sub.add_parser(name)
        p.add_argument("--project", default=".", help="project root (default: .)")
        p.add_argument("--from-json", help="use an existing analyzer JSON report instead of running it")
        p.add_argument("--snapshot", default="ARCHITECTURE.snapshot.json")
        p.add_argument("--layer-depth", type=int, default=2)
    sub.choices["snapshot"].add_argument("--md", default="ARCHITECTURE.md", help="human doc to (re)generate")
    sub.choices["snapshot"].add_argument("--no-md", action="store_true")
    sub.choices["check"].add_argument("--fail-on-drift", action="store_true", help="exit 4 on violations")
    sub.choices["check"].add_argument("--max-score-drop", type=float, default=0.5)
    sub.choices["check"].add_argument("--json", action="store_true", help="machine-readable report")
    args = ap.parse_args(argv)

    project = Path(args.project).resolve()
    snap_path = Path(args.snapshot)
    if not snap_path.is_absolute():
        snap_path = project / snap_path

    if args.cmd == "snapshot":
        snap = build_snapshot(load_report(args), args.layer_depth, project.name)
        snap_path.write_text(json.dumps(snap, indent=2) + "\n", encoding="utf-8")
        msg = f"wrote {snap_path}"
        if not args.no_md:
            md = Path(args.md) if Path(args.md).is_absolute() else project / args.md
            write_md(md, render_md(snap))
            msg += f" and {md}"
        print(msg, file=sys.stderr)
        return 0

    if not snap_path.is_file():
        raise SystemExit(f"no snapshot at {snap_path}; run `snapshot` first")
    base = json.loads(snap_path.read_text(encoding="utf-8"))
    cur = build_snapshot(load_report(args), base.get("layer_depth", args.layer_depth), project.name)
    result = compare(base, cur, args.max_score_drop)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"architecture check against {snap_path.name} ({base.get('captured_at')}):")
        for v in result["violations"]:
            print(f"  VIOLATION  {v}")
        for n in result["notes"]:
            print(f"  note       {n}")
        if not result["violations"] and not result["notes"]:
            print("  no drift")
    if result["violations"] and args.fail_on_drift:
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
