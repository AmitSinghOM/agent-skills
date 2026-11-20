"""Safety tests for the changed-lines manifest generator and its wrapper.

Static fixtures in GOLDEN are shaped for differential replay against the core
analyzer's ``cqa_analyzer.changed_lines.diff_to_manifest``: set CQA_CORE_SRC to
a checkout of code-quality-analyzer to run that comparison (it is skipped
otherwise, so this repository keeps no analyzer import dependency).
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "code-quality-gate" / "scripts"
GEN = SCRIPTS / "changed_lines_manifest.py"
GATE = SCRIPTS / "run-gate.sh"
_spec = importlib.util.spec_from_file_location("clm_safety", GEN)
clm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(clm)  # type: ignore[union-attr]


def _m(files: dict[str, list[tuple[int, int]]]) -> dict:
    return clm.build_manifest(files)


# Verbatim copy of the core analyzer's tests/test_mcp_server.py::_SAMPLE_DIFF.
CORE_SAMPLE_DIFF = """\
diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,4 +1,5 @@
 import os
-x = 1
+x = 2
+y = 3
 z = 4
 w = 5
@@ -10,3 +11,2 @@ def f():
     a = 1
-    b = 2
     c = 3
diff --git a/new.go b/new.go
new file mode 100644
--- /dev/null
+++ b/new.go
@@ -0,0 +1,2 @@
+package main
+func main() {}
diff --git a/gone.rs b/gone.rs
deleted file mode 100644
--- a/gone.rs
+++ /dev/null
@@ -1,2 +0,0 @@
-fn a() {}
-fn b() {}
diff --git a/old_name.ts b/new_name.ts
similarity index 90%
rename from old_name.ts
rename to new_name.ts
--- a/old_name.ts
+++ b/new_name.ts
@@ -3,2 +3,2 @@
-const a = 1;
+const a = 2;
 const b = 3;
diff --git a/img.png b/img.png
Binary files a/img.png and b/img.png differ
diff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"
--- "a/caf\\303\\251.py"
+++ "b/caf\\303\\251.py"
@@ -7 +7 @@
-print(1)
+print(2)
"""

GUARD_DIFF = """\
diff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"
--- "a/caf\\303\\251.py"
+++ "b/caf\\303\\251.py"
@@ -2 +1,0 @@
-    if not user.is_admin: raise PermissionError
"""

BEGIN_END_DIFF = """\
diff --git a/g.py b/g.py
--- a/g.py
+++ b/g.py
@@ -1 +0,0 @@
-assert_safe()
@@ -5 +3,0 @@
-audit()
"""

# Content lines that look like headers; hunk counts must keep them as content.
HOSTILE_DIFF = """\
diff --git a/h.py b/h.py
--- a/h.py
+++ b/h.py
@@ -1,0 +2,4 @@
+++ b/evil.py
+@@ -1 +1000 @@
+diff --git a/evil.py b/evil.py
+--- a/evil.py
@@ -9 +13 @@
---- a/x
++++ b/y
"""

# (name, diff, include_deletions, expected manifest). Replayable against core.
GOLDEN = [
    ("core_sample_with_deletions", CORE_SAMPLE_DIFF, True, _m({
        "src/app.py": [(2, 3), (12, 12)], "new.go": [(1, 2)],
        "new_name.ts": [(3, 3)], "café.py": [(7, 7)]})),
    ("core_sample_without_deletions", CORE_SAMPLE_DIFF, False, _m({
        "src/app.py": [(2, 3)], "new.go": [(1, 2)],
        "new_name.ts": [(3, 3)], "café.py": [(7, 7)]})),
    ("unicode_guard_deletion_line2", GUARD_DIFF, True, _m({"café.py": [(2, 2)]})),
    ("deletion_at_beginning_and_end", BEGIN_END_DIFF, True, _m({"g.py": [(1, 1), (4, 4)]})),
    ("header_looking_content", HOSTILE_DIFF, False, _m({"h.py": [(2, 5), (13, 13)]})),
]

MALFORMED = {
    "truncated hunk": "--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,2 @@\n+a\n",
    "hunk longer than header": "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n+c\n",
    "unexpected hunk line": "--- a/x.py\n+++ b/x.py\n@@ -1 +1,2 @@\n-a\n+b\n?c\n",
    "not a diff": "hello world\n",
    "combined diff": "diff --cc x.py\nindex 1,2..3\n@@@ -1,1 -1,1 +1,1 @@@\n",
    "unterminated quote": '--- "a/x.py\n+++ "b/x.py\n@@ -1 +1 @@\n-a\n+b\n',
    "quoted non-utf8": '--- "a/\\377.py"\n+++ "b/\\377.py"\n@@ -1 +1 @@\n-a\n+b\n',
    "backslash path": "--- a/x\\\\y.py\n+++ b/x\\\\y.py\n@@ -1 +1 @@\n-a\n+b\n",
    "dotdot path": "--- a/../x.py\n+++ b/../x.py\n@@ -1 +1 @@\n-a\n+b\n",
    "32-bit overflow": "--- a/x.py\n+++ b/x.py\n@@ -1 +2147483648 @@\n-a\n+b\n",
}


class StaticParser(unittest.TestCase):
    def test_golden_fixtures(self):
        for name, diff, deletions, expected in GOLDEN:
            with self.subTest(name):
                got = clm.build_manifest(clm.parse_diff(diff, include_deletions=deletions, strict=True))
                self.assertEqual(expected, got)

    def test_default_keeps_legacy_contract_no_deletions(self):
        self.assertEqual({}, clm.parse_diff(BEGIN_END_DIFF))

    def test_malformed_input_fails_closed(self):
        for name, diff in MALFORMED.items():
            with self.subTest(name):
                with self.assertRaises(clm.ManifestError):
                    clm.parse_diff(diff, include_deletions=True, strict=True)

    def test_empty_diff_means_no_changes(self):
        self.assertEqual({}, clm.parse_diff("", strict=True))

    def test_strict_rejects_hunk_without_file_header(self):
        with self.assertRaises(clm.ManifestError):
            clm.parse_diff("diff --git a/x b/x\n@@ -1 +1 @@\n-a\n+b\n", strict=True)

    def test_line_separators_other_than_newline_stay_inside_content(self):
        diff = "--- a/f.py\n+++ b/f.py\n@@ -0,0 +1,2 @@\n+a\x0cb\n+c\u2028d\x1ce\n"
        self.assertEqual({"f.py": [(1, 2)]}, clm.parse_diff(diff, strict=True))

    def test_range_bound_enforced(self):
        diff = "--- a/f.py\n+++ b/f.py\n" + "".join(f"@@ -0,0 +{n} @@\n+x\n" for n in range(1, 40, 2))
        old = clm.MAX_RANGES
        clm.MAX_RANGES = 5
        try:
            with self.assertRaises(clm.ManifestError):
                clm.parse_diff(diff, strict=True)
        finally:
            clm.MAX_RANGES = old

    def test_render_compacts_then_refuses_oversize(self):
        files = {f"d/{i:05d}.py": [(1, 1)] for i in range(3)}
        old = clm.MAX_MANIFEST_BYTES
        try:
            clm.MAX_MANIFEST_BYTES = len(json.dumps(clm.build_manifest(files), separators=(",", ":"))) + 1
            self.assertNotIn("\n  ", clm.render(clm.build_manifest(files)))
            clm.MAX_MANIFEST_BYTES = 10
            with self.assertRaises(clm.ManifestError):
                clm.render(clm.build_manifest(files))
        finally:
            clm.MAX_MANIFEST_BYTES = old

    @unittest.skipUnless(os.environ.get("CQA_CORE_SRC"), "set CQA_CORE_SRC to a code-quality-analyzer checkout")
    def test_golden_matches_core_analyzer(self):
        sys.path.insert(0, os.environ["CQA_CORE_SRC"])
        try:
            from cqa_analyzer.changed_lines import diff_to_manifest, parse_changed_lines
        finally:
            sys.path.pop(0)
        for name, diff, deletions, expected in GOLDEN:
            with self.subTest(name):
                self.assertEqual(expected, diff_to_manifest(diff, include_deletions=deletions))
                parse_changed_lines(expected)


class GitRepo(unittest.TestCase):
    """Real git repositories (temp dirs honour TMPDIR)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")

    def git(self, *a, cwd: Path | None = None) -> str:
        return subprocess.run(["git", "-C", str(cwd or self.repo), "-c", "user.name=t", "-c", "user.email=t@t", *a],
                              check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout

    def write(self, rel: str, text: str) -> None:
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def commit(self, msg: str = "c") -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)

    def cli(self, *args: str, repo: Path | None = None, env: dict | None = None):
        r = subprocess.run([sys.executable, str(GEN), *args, f"--repo={repo or self.repo}"],
                           capture_output=True, text=True, env=env)
        files = None
        if r.returncode == 0 and r.stdout:
            files = {f["path"]: [(x["start_line"], x["end_line"]) for x in f["ranges"]]
                     for f in json.loads(r.stdout)["files"]}
        return r, files


class GitSafety(GitRepo):
    def test_fallback_does_not_read_caller_stdin(self):
        self.write("a.py", "x\n")
        self.commit()
        with subprocess.Popen(
            [sys.executable, str(GEN), "--base=missing", f"--repo={self.repo}"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ) as process:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                self.fail("manifest generator waited for caller stdin")
            stdout, stderr = process.communicate()
        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(json.loads(stdout)["files"][0]["path"], "a.py")

    def test_unicode_and_quoted_paths(self):
        names = ["café.py", "sp ace.py", 'q"uote.py', "tab\tname.py", "日本/語.py"]
        for n in names:
            self.write(n, "a\nb\nc\n")
        self.commit()
        for n in names:
            self.write(n, "a\nB\nc\n")
        r, files = self.cli("--base", "HEAD", "--include-deletions")
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual({n: [(2, 2)] for n in names}, files)

    def test_root_subdir_and_sibling_scope(self):
        for rel in ("pkg/a.py", "pkg-sib/a.py", "sib/b.py", "top.py"):
            self.write(rel, "1\n2\n")
        self.commit()
        for rel in ("pkg/a.py", "pkg-sib/a.py", "sib/b.py", "top.py"):
            self.write(rel, "1\n2\n3\n")
        _, root = self.cli("--base", "HEAD")
        self.assertEqual({"pkg/a.py", "pkg-sib/a.py", "sib/b.py", "top.py"}, set(root))
        r, sub = self.cli("--base", "HEAD", repo=self.repo / "pkg")
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual({"a.py": [(3, 3)]}, sub)
        self.write("pkg/new.py", "n\n")  # untracked, within scope
        _, sub = self.cli("--base", "HEAD", repo=self.repo / "pkg")
        self.assertEqual({"a.py": [(3, 3)], "new.py": [(1, 1)]}, sub)

    def test_deleted_guard_at_beginning_and_end(self):
        self.write("g.py", "guard_start()\nwork()\nmore()\nguard_end()\n")
        self.commit()
        self.write("g.py", "work()\nmore()\n")
        _, with_del = self.cli("--base", "HEAD", "--include-deletions")
        self.assertEqual({"g.py": [(1, 2)]}, with_del)  # start anchors to 1, EOF clamps to last line
        _, without = self.cli("--base", "HEAD")
        self.assertEqual({}, without)

    def test_guard_deleted_in_middle_anchors_following_line(self):
        self.write("café.py", "def f(user):\n    check(user)\n    return 1\n")
        self.commit()
        self.write("café.py", "def f(user):\n    return 1\n")
        _, files = self.cli("--base", "HEAD", "--include-deletions")
        self.assertEqual({"café.py": [(2, 2)]}, files)

    def test_missing_base_falls_back_announced(self):
        self.write("a.py", "x\ny\n")
        self.commit()
        r, files = self.cli("--base", "origin/nope")
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertIn("treating every tracked line as changed", r.stderr)
        self.assertEqual({"a.py": [(1, 2)]}, files)

    def test_unrelated_history_falls_back(self):
        self.write("a.py", "x\n")
        self.commit()
        empty = self.git("hash-object", "-t", "tree", "-w", "--stdin").strip()
        self.git("branch", "other", self.git("commit-tree", empty, "-m", "orphan").strip())
        r, files = self.cli("--base", "other")
        self.assertIn("treating every tracked line as changed", r.stderr)
        self.assertEqual({"a.py": [(1, 1)]}, files)

    def test_git_operational_errors_are_not_masked(self):
        plain = self.tmp / "not-a-repo"
        plain.mkdir()
        r, _ = self.cli("--base", "HEAD", repo=plain)
        self.assertEqual(3, r.returncode)
        self.assertNotIn("treating every tracked line", r.stderr)
        self.assertIn("error:", r.stderr)
        r, _ = self.cli("--base", "HEAD", repo=self.tmp / "missing")
        self.assertEqual(3, r.returncode)

    def test_external_diff_and_textconv_never_run(self):
        marker = self.tmp / "ran"
        hook = self.tmp / "hook.sh"
        hook.write_text(f"#!/bin/sh\ntouch '{marker}'\necho '@@ -1 +1,500 @@'\n")
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
        self.git("config", "diff.external", str(hook))
        self.git("config", "diff.evil.textconv", str(hook))
        self.git("config", "diff.evil.command", str(hook))
        self.write(".gitattributes", "*.py diff=evil\n*.txt -diff\n")
        self.write("a.py", "x\n")
        self.write("b.txt", "x\n")
        self.commit()
        self.write("a.py", "x\ny\n")
        self.write("b.txt", "x\ny\n")
        env = dict(os.environ, GIT_EXTERNAL_DIFF=str(hook))
        r, files = self.cli("--base", "HEAD", env=env)
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertFalse(marker.exists(), "external diff / textconv executed")
        self.assertEqual({"a.py": [(2, 2)], "b.txt": [(2, 2)]}, files)  # -diff cannot hide lines
        self.git("add", "-A")
        r, files = self.cli("--staged", env=env)
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertFalse(marker.exists())

    def test_option_looking_refs_are_revisions_not_options(self):
        self.write("a.py", "x\n")
        self.commit()
        out = self.tmp / "pwned"
        for ref in (f"--output={out}", "-p", "--all", "HEAD --staged"):
            with self.subTest(ref=ref):
                r, files = self.cli(f"--base={ref}")
                self.assertEqual(0, r.returncode, r.stderr)
                self.assertIn("treating every tracked line", r.stderr)
                self.assertEqual({"a.py": [(1, 1)]}, files)
                self.assertFalse(out.exists())

    def test_hostile_source_lines(self):
        self.write("h.py", "keep\n")
        self.commit()
        self.write("h.py", "keep\n++ b/evil.py\n@@ -1 +1000 @@\ndiff --git a/e b/e\n--- a/e\nf\x0cg\u2028h\n\\ x\n")
        r, files = self.cli("--base", "HEAD", "--include-deletions")
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual({"h.py": [(2, 7)]}, files)

    def test_base_mode_scans_worktree_including_uncommitted_and_untracked(self):
        self.write("a.py", "1\n2\n")
        self.commit()
        self.write("a.py", "1\n2\n3\n")      # unstaged edit
        self.write("new.py", "n1\nn2\n")     # untracked
        self.write("bin.dat", "a\0b")        # untracked binary: skipped
        self.write(".gitignore", "ignored.py\n")
        self.write("ignored.py", "i\n")
        r, files = self.cli("--base", "HEAD")
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual({"a.py": [(3, 3)], "new.py": [(1, 2)], ".gitignore": [(1, 1)]}, files)

    def test_staged_refuses_unstaged_tracked_changes(self):
        self.write("a.py", "1\n")
        self.commit()
        self.write("a.py", "1\n2\n")
        self.git("add", "a.py")
        r, files = self.cli("--staged")
        self.assertEqual({"a.py": [(2, 2)]}, files)
        self.write("a.py", "0\n1\n2\n")      # now worktree != index
        r, _ = self.cli("--staged")
        self.assertEqual(3, r.returncode)
        self.assertIn("unstaged changes", r.stderr)

    def test_staged_in_unborn_repo(self):
        self.write("a.py", "x\ny\n")
        self.git("add", "a.py")
        r, files = self.cli("--staged")
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual({"a.py": [(1, 2)]}, files)

    def test_base_and_staged_are_exclusive(self):
        r, _ = self.cli("--base", "HEAD", "--staged")
        self.assertEqual(2, r.returncode)


FAKE = """#!/usr/bin/env bash
python3 - "$@" <<'PY'
import json, os, sys
argv = sys.argv[1:]
m = None
if "--changed-lines-manifest" in argv:
    m = json.load(open(argv[argv.index("--changed-lines-manifest") + 1]))
json.dump({"argv": argv, "manifest": m}, open(os.environ["FAKE_OUT"], "w"))
PY
"""


class WrapperSafety(GitRepo):
    def setUp(self):
        super().setUp()
        self.fake = self.tmp / "fake"
        self.fake.write_text(FAKE)
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IXUSR)
        self.out = self.tmp / "out.json"

    def gate(self, *args: str, cwd: Path | None = None):
        env = dict(os.environ, CQA_CMD=str(self.fake), FAKE_OUT=str(self.out))
        return subprocess.run([str(GATE), *args], cwd=cwd or self.repo, env=env, capture_output=True, text=True)

    def manifest_files(self) -> dict:
        m = json.loads(self.out.read_text())["manifest"]
        return {f["path"]: [(r["start_line"], r["end_line"]) for r in f["ranges"]] for f in m["files"]}

    def test_wrapper_opts_into_deletions_and_project_relative_paths(self):
        self.write("svc/café.py", "a\nguard()\nb\n")
        self.write("other/x.py", "x\n")
        self.commit()
        self.write("svc/café.py", "a\nb\n")
        self.write("other/x.py", "y\n")
        r = self.gate("--base", "HEAD", "--project", "svc")
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual({"café.py": [(2, 2)]}, self.manifest_files())
        self.assertEqual("svc", json.loads(self.out.read_text())["argv"][0])

    def test_wrapper_staged_with_unstaged_edits_exits_3_without_running_analyzer(self):
        self.write("a.py", "1\n")
        self.commit()
        self.write("a.py", "1\n2\n")
        self.git("add", "a.py")
        self.write("a.py", "1\n2\n3\n")
        r = self.gate("--staged")
        self.assertEqual(3, r.returncode)
        self.assertIn("could not build the changed-lines manifest", r.stderr)
        self.assertFalse(self.out.exists())

    def test_wrapper_dash_ref_and_dash_project_are_values(self):
        self.write("a.py", "x\n")
        self.commit()
        r = self.gate("--base", "-p")
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertIn("treating every tracked line", r.stderr)
        self.assertEqual({"a.py": [(1, 1)]}, self.manifest_files())
        (self.repo / "-x").mkdir()
        self.write("-x/b.py", "b\n")
        r = self.gate("--base", "HEAD", "--project", "-x")
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual({"b.py": [(1, 1)]}, self.manifest_files())

    def test_wrapper_not_a_repo_is_environment_error(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        r = self.gate("--base", "HEAD", cwd=plain)
        self.assertEqual(3, r.returncode)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
