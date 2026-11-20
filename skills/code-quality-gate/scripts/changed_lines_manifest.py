#!/usr/bin/env python3
"""Generate a cqa-analyzer changed-lines manifest (schema 1.0.0) from git.

The analyzer deliberately never invokes git; something has to turn a diff into
the JSON it accepts. This does that with the standard library only.

Usage:
    changed_lines_manifest.py --base origin/main [--repo .] [--output changed-lines.json]
    changed_lines_manifest.py --staged            # lines staged for commit (pre-commit hook)
    changed_lines_manifest.py --base origin/main --include-deletions   # what run-gate.sh uses

Output schema (https://github.com/AmitSinghOM/code-quality-analyzer/blob/main/docs/CHANGED_LINES.md):
    {"schema_version": "1.0.0",
     "files": [{"path": "src/x.py", "ranges": [{"start_line": 12, "end_line": 18}]}]}

What is compared (the manifest describes the tree the analyzer will scan):
    --base REF   merge-base(REF, HEAD) -> the WORKING TREE, plus untracked
                 non-ignored files as wholly new. Uncommitted edits are covered.
                 If REF does not resolve, or shares no history with HEAD, every
                 tracked line is treated as changed, and that is announced.
    --staged     index vs HEAD. Refused if tracked files under --repo have
                 unstaged edits, because the scan would read different lines.

Paths are POSIX and relative to --repo (git --relative), so they match what
the analyzer scans when it is pointed at that same directory. Only new-side
lines are listed; with --include-deletions a pure deletion is anchored to the
line that now follows it (to the last line when it was at end of file).
Diffs are parsed fail-closed: malformed or truncated hunks, combined diffs,
undecodable paths, or results the analyzer would reject are errors (exit 3).
External diff drivers and textconv filters are never run.

Exit codes: 0 ok · 2 usage · 3 git/diff/manifest error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path

SCHEMA_VERSION = "1.0.0"

# Mirrors cqa_analyzer.changed_lines (3.4.x) so nothing emitted here is rejected there.
MAX_MANIFEST_BYTES = 5 * 1024 * 1024
MAX_FILES = 20_000
MAX_RANGES = 100_000
MAX_PATH_LENGTH = 4_096
MAX_LINE = 2_147_483_647
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")

# Local safety bounds on what we are willing to read.
MAX_DIFF_BYTES = 256 * 1024 * 1024
MAX_LINE_BYTES = 1024 * 1024
MAX_COUNT_BYTES = 256 * 1024 * 1024
MIN_GIT = (2, 24)  # --end-of-options

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: |$)")
_C_ESCAPE = re.compile(r'\\([abfnrtv\\"]|[0-7]{1,3})')
_C_ESCAPES = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v",
              "\\": "\\", '"': '"'}

# Settings that would otherwise let user/repo config change what we parse or run.
_GIT_CONFIG = ("-c", "core.quotePath=true", "-c", "core.fsmonitor=false",
               "-c", "diff.suppressBlankEmpty=false", "-c", "color.ui=never")
_DIFF_FLAGS = ("--no-color", "--no-ext-diff", "--no-textconv", "--text", "--unified=0",
               "--inter-hunk-context=0", "--src-prefix=a/", "--dst-prefix=b/",
               "--find-renames", "--relative", "--diff-filter=ACMRT")


class ManifestError(ValueError):
    """The diff or the repository state cannot be turned into a trustworthy manifest."""


# --- paths --------------------------------------------------------------------

def _unquote_git_path(raw: str) -> str:
    """Undo git's C-style quoting ("caf\\303\\251.py"); quoted bytes must be UTF-8."""
    if not raw.startswith('"'):
        return raw
    if len(raw) < 2 or not raw.endswith('"'):
        raise ManifestError("diff header has an unterminated quoted path")
    body = raw[1:-1]
    rest = _C_ESCAPE.sub("", body)
    if "\\" in rest or '"' in rest:
        raise ManifestError("diff header has an invalid quoted path")

    def replace(m: re.Match[str]) -> str:
        tok = m.group(1)
        if tok in _C_ESCAPES:
            return _C_ESCAPES[tok]
        if int(tok, 8) > 0xFF:
            raise ManifestError("diff header has an invalid octal escape")
        return chr(int(tok, 8))

    try:
        return _C_ESCAPE.sub(replace, body).encode("latin-1").decode("utf-8")
    except UnicodeError as error:
        raise ManifestError("diff header path is not valid UTF-8") from error


def _header_path(line: str, marker: str) -> str | None:
    value = line[len(marker):].rstrip("\r\n").split("\t", 1)[0]
    if value == "/dev/null":
        return None
    value = _unquote_git_path(value)
    if value.startswith(("a/", "b/")):
        value = value[2:]
    return value


def _validate_path(value: str) -> str:
    """Same acceptance rule as the analyzer's manifest loader."""
    try:
        value.encode("utf-8")
    except UnicodeError as error:
        raise ManifestError("changed path is not valid UTF-8") from error
    parts = value.split("/")
    if (not value or len(value) > MAX_PATH_LENGTH or "\x00" in value or "\\" in value
            or value.startswith("/") or _SCHEME.match(value)
            or any(p in {"", ".", ".."} for p in parts)):
        raise ManifestError(f"changed path {value!r} is not a project-relative POSIX path the "
                            "analyzer accepts (backslash, scheme, '..' or empty segment)")
    return value


# --- diff walker ----------------------------------------------------------------

class _DiffWalker:
    """Line-at-a-time unified diff reader driven by hunk counts.

    While a hunk has lines outstanding every line is content, so a source line
    that starts with "+++", "---", "@@" or "diff --git" is never read as a header.
    """

    def __init__(self, include_deletions: bool, strict: bool) -> None:
        self.include_deletions = include_deletions
        self.strict = strict
        self.files: dict[str, list[tuple[int, int]]] = {}
        self.anchor_max: dict[str, int] = {}
        self.current: str | None = None
        self.target_seen = False
        self.old_remaining = self.new_remaining = self.new_line = 0
        self.hunks = self.headers = self.ranges = 0
        self.saw_input = False

    @property
    def in_hunk(self) -> bool:
        return self.old_remaining > 0 or self.new_remaining > 0

    def feed(self, line: str) -> None:
        self.saw_input = True
        if self.in_hunk:
            self._content(line)
        else:
            self._header(line)

    def finish(self) -> dict[str, list[tuple[int, int]]]:
        if self.in_hunk:
            raise ManifestError("unified diff ends inside a hunk (truncated diff)")
        if self.saw_input and not self.hunks and not self.headers:
            raise ManifestError("input is not a unified diff (no 'diff --git' header or hunk)")
        return {p: merge_ranges(r) for p, r in self.files.items() if r}

    def _header(self, line: str) -> None:
        if line.startswith("diff --git ") or line.startswith("diff --cc ") or line.startswith("diff --combined "):
            if line.startswith("diff --git "):
                self.headers += 1
                self.current, self.target_seen = None, False
                return
            raise ManifestError("combined (merge-conflict) diffs are not supported; resolve conflicts first")
        if line.startswith("+++ "):
            self.current = _header_path(line, "+++ ")
            if self.current is not None:
                _validate_path(self.current)
            self.target_seen = True
        elif line.startswith(("Binary files ", "GIT binary patch")):
            self.current = None
        elif line.startswith("@@"):
            m = _HUNK.match(line)
            if not m:
                if self.strict or line.startswith("@@@"):
                    raise ManifestError("unparseable or combined hunk header in diff")
                return
            if self.strict and not self.target_seen:
                raise ManifestError("hunk appears before its '+++' file header")
            self._open(m)
        elif self.strict and (line[:1] == " " or (line[:1] in "+-" and line[:4] not in ("+++ ", "--- "))):
            raise ManifestError("unified diff hunk is longer than its header declares")
        # "--- ", "index", mode, rename, similarity, "\ No newline": nothing to record.

    def _open(self, m: re.Match[str]) -> None:
        nums = [int(g) if g is not None else 1 for g in (m.group(1), m.group(2), m.group(3), m.group(4))]
        if any(n > MAX_LINE for n in nums):
            raise ManifestError("hunk header line numbers exceed the 32-bit limit")
        self.hunks += 1
        _, self.old_remaining, self.new_line, self.new_remaining = nums
        if self.new_remaining == 0:
            self.new_line += 1  # git names the line *before* a pure-deletion gap

    def _content(self, line: str) -> None:
        tag = line[:1]
        if tag == "\\":
            return  # "\ No newline at end of file"
        if tag == "+":
            self.new_remaining -= 1
            self._touch(self.new_line, deletion=False)
            self.new_line += 1
        elif tag == "-":
            self.old_remaining -= 1
            if self.include_deletions:
                self._touch(max(self.new_line, 1), deletion=True)
        elif tag == " " or (tag == "" and not self.strict):
            self.old_remaining -= 1
            self.new_remaining -= 1
            self.new_line += 1
        elif (not self.strict and self.current is None and self.target_seen
              and line.startswith("diff --git ")):
            # Legacy leniency: a hand-trimmed deleted-file hunk. Nothing is recorded
            # for /dev/null targets, so resynchronising cannot misplace a range.
            self.old_remaining = self.new_remaining = 0
            self._header(line)
            return
        else:
            raise ManifestError("unified diff hunk contains a line that is not +, -, space or \\")
        if self.old_remaining < 0 or self.new_remaining < 0:
            raise ManifestError("unified diff hunk is longer than its header declares")
        if self.new_line > MAX_LINE + 1:
            raise ManifestError("diff line numbers exceed the 32-bit limit")

    def _touch(self, line_no: int, deletion: bool) -> None:
        if self.current is None:
            return
        ranges = self.files.setdefault(self.current, [])
        if deletion:
            self.anchor_max[self.current] = max(self.anchor_max.get(self.current, 0), line_no)
        if ranges and ranges[-1][0] <= line_no <= ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], max(ranges[-1][1], line_no))
            return
        self.ranges += 1
        if self.ranges > MAX_RANGES:
            raise ManifestError(f"diff touches more than {MAX_RANGES} separate ranges; narrow --repo")
        if len(self.files) > MAX_FILES:
            raise ManifestError(f"diff touches more than {MAX_FILES} files; narrow --repo")
        ranges.append((line_no, line_no))


def _split_text(diff_text: str) -> Iterator[str]:
    """Split on "\\n" only: str.splitlines() also breaks on \\f, \\x1c, U+2028 ...,
    which would desynchronise hunk counts on legitimate source lines."""
    lines = diff_text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    yield from lines


def _walk(lines: Iterable[str], include_deletions: bool, strict: bool) -> _DiffWalker:
    walker = _DiffWalker(include_deletions, strict)
    for line in lines:
        walker.feed(line)
    walker.finish()
    return walker


def parse_diff(diff_text: str, include_deletions: bool = False, strict: bool = False
               ) -> dict[str, list[tuple[int, int]]]:
    """Map new-side path -> sorted inclusive (start, end) ranges.

    Defaults keep the original contract (added/modified lines only, tolerant of a
    hand-trimmed deleted-file hunk). ``strict=True`` is what git output gets.
    Raises ManifestError on anything that cannot be followed safely.
    """
    if not isinstance(diff_text, str):
        raise ManifestError("diff text must be a string")
    if len(diff_text) > MAX_DIFF_BYTES:
        raise ManifestError("diff text exceeds the 256 MiB safety limit")
    return _walk(_split_text(diff_text), include_deletions, strict).finish()


def merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping/adjacent inclusive ranges, sorted."""
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


# --- git ------------------------------------------------------------------------

def _git_env() -> dict[str, str]:
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    for var in ("GIT_EXTERNAL_DIFF", "GIT_DIFF_OPTS", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT"):
        env.pop(var, None)
    return env


def _git(repo: Path, *args: str, ok_codes: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(["git", *_GIT_CONFIG, "-C", str(repo), *args],
                                capture_output=True, stdin=subprocess.DEVNULL, env=_git_env(), check=False)
    except OSError as error:
        raise ManifestError(f"cannot run git: {error}") from error
    if result.returncode not in ok_codes:
        msg = result.stderr.decode("utf-8", "replace").strip()
        raise ManifestError(f"git {args[0]} failed ({result.returncode}): {msg}")
    return result


def _check_repo(repo: Path) -> None:
    if not repo.is_dir():
        raise ManifestError(f"--repo {str(repo)!r} is not a directory")
    out = _git(repo, "version").stdout.decode("ascii", "replace")
    m = re.search(r"(\d+)\.(\d+)", out)
    if not m or (int(m.group(1)), int(m.group(2))) < MIN_GIT:
        raise ManifestError(f"git >= {MIN_GIT[0]}.{MIN_GIT[1]} is required (found {out.strip()!r})")
    inside = _git(repo, "rev-parse", "--is-inside-work-tree").stdout.strip()
    if inside != b"true":
        raise ManifestError(f"--repo {str(repo)!r} is not inside a git working tree")


def _rev(repo: Path, spec: str) -> str | None:
    """Commit hash for ``spec`` or None if it does not name a commit. Never parses
    ``spec`` as an option, and never hides a real git failure."""
    r = _git(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{spec}^{{commit}}",
             ok_codes=(0, 1))
    return r.stdout.decode("ascii").strip() if r.returncode == 0 else None


def _empty_tree(repo: Path) -> str:
    """Hash of the empty tree for this repo's object format (git knows it without writing)."""
    return _git(repo, "hash-object", "-t", "tree", "--stdin").stdout.decode("ascii").strip()


def _resolve_base(repo: Path, base: str) -> tuple[str, bool]:
    """Return (old-side tree-ish hash, fell_back).

    Normal case: merge-base(base, HEAD), what a PR shows. If ``base`` does not
    resolve (single-commit repo and ``HEAD~1``; shallow clone without the ref),
    HEAD is unborn, or there is no merge-base, fall back to the empty tree:
    every tracked line counts as changed, which is the honest answer for
    "there is nothing to compare against" and never silently gates zero lines.
    Any other git failure is raised, not treated as a fallback.
    """
    base_hash, head_hash = _rev(repo, base), _rev(repo, "HEAD")
    if base_hash and head_hash:
        mb = _git(repo, "merge-base", "--end-of-options", base_hash, head_hash, ok_codes=(0, 1))
        if mb.returncode == 0:
            return mb.stdout.decode("ascii").strip().splitlines()[0], False
    return _empty_tree(repo), True


def _diff_argv(repo: Path, base: str | None, staged: bool) -> list[str]:
    _check_repo(repo)
    argv = ["diff", *_DIFF_FLAGS]
    if staged:
        _reject_unstaged(repo)
        argv.append("--cached")
    elif base:
        old, fell_back = _resolve_base(repo, base)
        if fell_back:
            print(f"note: base {base!r} is not a commit sharing history with HEAD; "
                  "treating every tracked line as changed", file=sys.stderr)
        argv += ["--end-of-options", old]
    else:
        raise ManifestError("pass --base <ref> or --staged")
    return argv + ["--", "."]


def _reject_unstaged(repo: Path) -> None:
    """--staged lists index line numbers; the analyzer reads the working tree."""
    r = _git(repo, "diff", "--quiet", "--no-ext-diff", "--no-textconv", "--", ".", ok_codes=(0, 1))
    if r.returncode == 1:
        raise ManifestError("tracked files under --repo have unstaged changes, so the scanned "
                            "tree would not match the staged line numbers; stage them or run "
                            "'git stash --keep-index' first")


def _stream_git(repo: Path, argv: list[str]) -> Iterator[str]:
    """Yield diff lines (no newline) with bounded memory; raise on git failure."""
    with tempfile.TemporaryFile() as err:
        try:
            proc = subprocess.Popen(["git", *_GIT_CONFIG, "-C", str(repo), *argv],
                                    stdout=subprocess.PIPE, stderr=err, env=_git_env())
        except OSError as error:
            raise ManifestError(f"cannot run git: {error}") from error
        total, completed = 0, False
        try:
            assert proc.stdout is not None
            partial = False
            while True:
                chunk = proc.stdout.readline(MAX_LINE_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DIFF_BYTES:
                    raise ManifestError("git diff output exceeds the 256 MiB safety limit; narrow --repo")
                ends = chunk.endswith(b"\n")
                if not partial:  # only the first chunk of an overlong line matters
                    yield chunk.rstrip(b"\n").decode("utf-8", "surrogateescape")
                partial = not ends
            completed = True
        finally:
            if not completed and proc.poll() is None:
                proc.kill()
            rc = proc.wait()
            proc.stdout.close()
        if rc != 0:
            err.seek(0)
            msg = err.read(64 * 1024).decode("utf-8", "replace").strip()
            raise ManifestError(f"git diff failed ({rc}): {msg}")


def git_diff(repo: Path, base: str | None, staged: bool) -> str:
    """Return the zero-context diff the manifest is built from (compatibility helper)."""
    return "\n".join(_stream_git(repo, _diff_argv(repo, base, staged))) + "\n"


def _line_count(path: Path) -> int | None:
    """Lines in a regular file (a final line without newline counts), or None if
    it is not a regular file or is too large to count."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_COUNT_BYTES:
        return None
    count, last = 0, b"\n"
    with open(path, "rb") as fh:
        while block := fh.read(1024 * 1024):
            count += block.count(b"\n")
            last = block[-1:]
    return count + (0 if last == b"\n" else 1)


def _clamp_eof_anchors(repo: Path, files: dict[str, list[tuple[int, int]]],
                       anchors: dict[str, int]) -> None:
    """A deletion at end of file has no following line; anchor it to the last line."""
    for path, anchor in anchors.items():
        if path not in files:
            continue
        n = _line_count(repo / path)
        if n is None or anchor <= n:
            continue
        top = max(n, 1)
        files[path] = merge_ranges([(min(s, top), min(e, top)) for s, e in files[path]])


def _untracked(repo: Path) -> dict[str, list[tuple[int, int]]]:
    """Untracked, non-ignored files under --repo are wholly new to the scan."""
    out = _git(repo, "ls-files", "-z", "--others", "--exclude-standard", "--", ".").stdout
    found: dict[str, list[tuple[int, int]]] = {}
    for raw in filter(None, out.split(b"\0")):
        try:
            rel = raw.decode("utf-8")
        except UnicodeError as error:
            raise ManifestError("an untracked path is not valid UTF-8") from error
        _validate_path(rel)
        if len(found) >= MAX_FILES:
            raise ManifestError(f"more than {MAX_FILES} untracked files; ignore or remove them")
        target = repo / rel
        try:
            if not stat.S_ISREG(os.lstat(target).st_mode):
                continue
            with open(target, "rb") as fh:
                if b"\0" in fh.read(8000):
                    continue  # binary
        except OSError as error:
            raise ManifestError(f"cannot read untracked file {rel!r}: {error.strerror}") from error
        n = _line_count(target)
        if n is None:
            found[rel] = [(1, MAX_LINE)]  # too large to count: everything is changed
        elif n:
            found[rel] = [(1, min(n, MAX_LINE))]
    return found


def collect(repo: Path, base: str | None, staged: bool, include_deletions: bool
            ) -> dict[str, list[tuple[int, int]]]:
    """Changed new-side ranges for the tree the analyzer will scan at ``repo``."""
    walker = _walk(_stream_git(repo, _diff_argv(repo, base, staged)), include_deletions, strict=True)
    files = walker.finish()
    if include_deletions:
        _clamp_eof_anchors(repo, files, walker.anchor_max)
    if not staged:
        for path, ranges in _untracked(repo).items():
            files[path] = merge_ranges(files.get(path, []) + ranges)
    return files


# --- manifest -------------------------------------------------------------------

def build_manifest(files: dict[str, list[tuple[int, int]]]) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "files": [
            {"path": path, "ranges": [{"start_line": s, "end_line": e} for s, e in ranges]}
            for path, ranges in sorted(files.items())
        ],
    }


def validate_manifest(manifest: dict) -> None:
    """Fail here, with a reason, on anything the analyzer's loader would reject."""
    entries = manifest.get("files")
    if manifest.get("schema_version") != SCHEMA_VERSION or not isinstance(entries, list):
        raise ManifestError("internal: malformed manifest envelope")
    if len(entries) > MAX_FILES:
        raise ManifestError(f"manifest would list more than {MAX_FILES} files; narrow --repo")
    seen: set[str] = set()
    total = 0
    for entry in entries:
        path = _validate_path(entry["path"])
        if path in seen:
            raise ManifestError(f"internal: duplicate manifest path {path!r}")
        seen.add(path)
        if not entry["ranges"]:
            raise ManifestError(f"internal: no ranges for {path!r}")
        total += len(entry["ranges"])
        for r in entry["ranges"]:
            s, e = r["start_line"], r["end_line"]
            if not (type(s) is int and type(e) is int and 1 <= s <= e <= MAX_LINE):
                raise ManifestError(f"manifest range {s}-{e} for {path!r} is out of bounds")
    if total > MAX_RANGES:
        raise ManifestError(f"manifest would list more than {MAX_RANGES} ranges; narrow --repo")


def render(manifest: dict) -> str:
    validate_manifest(manifest)
    text = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    if len(text.encode("utf-8")) > MAX_MANIFEST_BYTES:
        text = json.dumps(manifest, separators=(",", ":"), ensure_ascii=False) + "\n"
    if len(text.encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise ManifestError("manifest would exceed the analyzer's 5 MB limit; narrow --repo")
    return text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sel = ap.add_mutually_exclusive_group(required=True)
    sel.add_argument("--base", help="git ref to compare against (merge-base to working tree)")
    sel.add_argument("--staged", action="store_true", help="use staged changes instead of --base")
    ap.add_argument("--repo", default=".", help="directory the analyzer will scan (default: .)")
    ap.add_argument("--output", "-o", help="write manifest here (default: stdout)")
    ap.add_argument("--include-deletions", action="store_true",
                    help="anchor pure deletions to the following (or last) line")
    args = ap.parse_args(argv)

    repo = Path(args.repo).resolve()
    try:
        text = render(build_manifest(collect(repo, args.base, args.staged, args.include_deletions)))
    except ManifestError as error:
        print(f"error: {error}", file=sys.stderr)
        return 3
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        manifest = json.loads(text)
        n_files = len(manifest["files"])
        n_ranges = sum(len(f["ranges"]) for f in manifest["files"])
        print(f"wrote {args.output}: {n_files} file(s), {n_ranges} range(s)", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
