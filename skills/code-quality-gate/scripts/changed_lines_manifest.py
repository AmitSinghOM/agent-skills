#!/usr/bin/env python3
"""Generate a cqa-analyzer changed-lines manifest (schema 1.0.0) from git.

The analyzer deliberately never invokes git; something has to turn a diff into
the JSON it accepts. This does that with the standard library only.

Usage:
    changed_lines_manifest.py --base origin/main [--repo .] [--output changed-lines.json]
    changed_lines_manifest.py --staged            # lines staged for commit (pre-commit hook)

Output schema (https://github.com/AmitSinghOM/code-quality-analyzer/blob/main/docs/CHANGED_LINES.md):
    {"schema_version": "1.0.0",
     "files": [{"path": "src/x.py", "ranges": [{"start_line": 12, "end_line": 18}]}]}

Only added/modified lines on the *new* side of the diff are included. Deleted
files and pure deletions contribute nothing (there is no current line to gate).
Binary files are skipped. Paths are repo-relative POSIX, as the schema requires.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SCHEMA_VERSION = "1.0.0"
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _empty_tree(repo: Path) -> str:
    """Hash of the empty tree for this repo's object format, written so diff can read it."""
    out = subprocess.run(["git", "-C", str(repo), "hash-object", "-t", "tree", "-w", "--stdin"],
                         input="", capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _resolve_base(repo: Path, base: str) -> tuple[str, bool]:
    """Return (diff_spec, fell_back).

    Normal case: ``base`` is a commit sharing history with HEAD -> ``base...HEAD``
    (merge-base semantics, what a PR shows). If ``base`` does not resolve
    (single-commit repo and ``HEAD~1``; shallow clone without the ref) or shares
    no merge-base with HEAD, fall back to diffing the empty tree against HEAD:
    every tracked line is treated as changed, which is the honest answer for
    "there is nothing to compare against" and never silently gates zero lines.
    """
    def ok(*args: str) -> bool:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True).returncode == 0

    if ok("rev-parse", "--verify", "--quiet", f"{base}^{{commit}}") and ok("merge-base", base, "HEAD"):
        return f"{base}...HEAD", False
    return f"{_empty_tree(repo)} HEAD", True


def git_diff(repo: Path, base: str | None, staged: bool) -> str:
    """Return a unified diff with zero context lines, new-side only."""
    cmd = ["git", "-C", str(repo), "diff", "--unified=0", "--no-color", "--no-ext-diff",
           "--diff-filter=AMR"]  # added, modified, renamed: things with current lines
    if staged:
        cmd.append("--cached")
    elif base:
        spec, fell_back = _resolve_base(repo, base)
        if fell_back:
            print(f"note: base {base!r} is not a commit sharing history with HEAD; "
                  "treating every tracked line as changed", file=sys.stderr)
        cmd.extend(spec.split())
    else:
        raise SystemExit("error: pass --base <ref> or --staged")
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"git diff failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout


def parse_diff(diff_text: str) -> dict[str, list[tuple[int, int]]]:
    """Map new-side path -> list of inclusive (start, end) added/modified ranges."""
    files: dict[str, list[tuple[int, int]]] = {}
    current: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            target = line[4:]
            if target == "/dev/null":
                current = None
                continue
            if target.startswith("b/"):
                target = target[2:]
            current = target
            files.setdefault(current, [])
        elif line.startswith("Binary files"):
            current = None
        elif current is not None and line.startswith("@@"):
            m = _HUNK.match(line)
            if not m:
                continue
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) is not None else 1
            if count == 0:  # pure deletion: no current lines
                continue
            files[current].append((start, start + count - 1))
    return {p: merge_ranges(r) for p, r in files.items() if r}


def merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping/adjacent inclusive ranges, sorted."""
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def build_manifest(files: dict[str, list[tuple[int, int]]]) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "files": [
            {"path": path, "ranges": [{"start_line": s, "end_line": e} for s, e in ranges]}
            for path, ranges in sorted(files.items())
        ],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", help="git ref to diff against (merge-base semantics: BASE...HEAD)")
    ap.add_argument("--staged", action="store_true", help="use staged changes instead of --base")
    ap.add_argument("--repo", default=".", help="repository root (default: .)")
    ap.add_argument("--output", "-o", help="write manifest here (default: stdout)")
    args = ap.parse_args(argv)

    repo = Path(args.repo).resolve()
    manifest = build_manifest(parse_diff(git_diff(repo, args.base, args.staged)))
    text = json.dumps(manifest, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        n_files = len(manifest["files"])
        n_ranges = sum(len(f["ranges"]) for f in manifest["files"])
        print(f"wrote {args.output}: {n_files} file(s), {n_ranges} range(s)", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
