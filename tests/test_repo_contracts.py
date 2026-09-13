"""Repository contract tests. Run: python3 -m unittest discover -s tests -v"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import validate  # noqa: E402

GEN = ROOT / "skills" / "code-quality-gate" / "scripts" / "changed_lines_manifest.py"
spec = importlib.util.spec_from_file_location("clm", GEN)
clm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clm)  # type: ignore[union-attr]

SKILL_DIRS = sorted(p for p in (ROOT / "skills").iterdir() if p.is_dir())


class ShippedSkillsAreValid(unittest.TestCase):
    def test_every_shipped_skill_passes_validator(self):
        for d in SKILL_DIRS:
            with self.subTest(skill=d.name):
                self.assertEqual(validate.validate_skill(d), [])

    def test_three_skills_shipped(self):
        self.assertEqual([d.name for d in SKILL_DIRS],
                         ["code-quality-gate", "learning-accelerator", "source-grounded-claims"])


class ValidatorRejectsBadSkills(unittest.TestCase):
    def _skill(self, name: str, frontmatter: str, evals: dict | None = None) -> Path:
        tmp = Path(tempfile.mkdtemp())
        d = tmp / name
        (d / "evals").mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n# body\n", encoding="utf-8")
        if evals is None:
            evals = {"scenarios": [
                {"skills": [name], "query": "q", "expected_behavior": ["b"], "mode": "static"}
                for _ in range(3)]}
        (d / "evals" / "scenarios.json").write_text(json.dumps(evals), encoding="utf-8")
        return d

    GOOD = 'name: {n}\ndescription: "does a thing; use when x"\nlicense: MIT\nmetadata:\n  author: t\n  version: "1.0.0"'

    def test_good_minimal_skill_passes(self):
        self.assertEqual(validate.validate_skill(self._skill("good-skill", self.GOOD.format(n="good-skill"))), [])

    def test_name_must_match_directory(self):
        errs = validate.validate_skill(self._skill("dir-name", self.GOOD.format(n="other-name")))
        self.assertTrue(any("!= directory" in e for e in errs))

    def test_uppercase_and_double_hyphen_rejected(self):
        for bad in ("Bad-Name", "bad--name", "-bad", "bad-"):
            errs = validate.validate_skill(self._skill("x", self.GOOD.format(n=bad)))
            self.assertTrue(any("lowercase" in e for e in errs), bad)

    def test_non_spec_top_level_keys_rejected(self):
        fm = self.GOOD.format(n="k") + "\ntags: [a, b]\nversion: 1.0.0"
        errs = validate.validate_skill(self._skill("k", fm))
        self.assertTrue(any("non-spec frontmatter keys" in e for e in errs))

    def test_missing_metadata_version_rejected(self):
        fm = 'name: v\ndescription: "d"\nlicense: MIT'
        errs = validate.validate_skill(self._skill("v", fm))
        self.assertTrue(any("metadata.version" in e for e in errs))

    def test_description_over_1024_rejected(self):
        fm = f'name: d\ndescription: "{"x" * 1025}"\nmetadata:\n  version: "1.0.0"'
        errs = validate.validate_skill(self._skill("d", fm))
        self.assertTrue(any("> 1024" in e for e in errs))

    def test_evals_need_static_scenario_and_three_entries(self):
        evals = {"scenarios": [{"skills": ["e"], "query": "q", "expected_behavior": ["b"], "mode": "integration"}]}
        errs = validate.validate_skill(self._skill("e", self.GOOD.format(n="e"), evals))
        self.assertTrue(any(">= 3" in e for e in errs))
        evals["scenarios"] *= 3
        errs = validate.validate_skill(self._skill("e", self.GOOD.format(n="e"), evals))
        self.assertTrue(any("static" in e for e in errs))


class ChangedLinesManifestGenerator(unittest.TestCase):
    DIFF = """\
diff --git a/src/a.py b/src/a.py
--- a/src/a.py
+++ b/src/a.py
@@ -10,0 +11,3 @@
+x
+y
+z
@@ -20 +23 @@
-old
+new
@@ -30,2 +33,0 @@
-gone
-gone
diff --git a/bin/blob b/bin/blob
Binary files a/bin/blob and b/bin/blob differ
diff --git a/old.py b/old.py
--- a/old.py
+++ /dev/null
@@ -1,5 +0,0 @@
-a
diff --git a/src/b.py b/src/b.py
--- a/src/b.py
+++ b/src/b.py
@@ -1 +1 @@
-q
+r
@@ -2 +2 @@
-s
+t
"""

    def test_parses_new_side_only_and_merges_adjacent(self):
        files = clm.parse_diff(self.DIFF)
        self.assertEqual(files, {"src/a.py": [(11, 13), (23, 23)], "src/b.py": [(1, 2)]})

    def test_deleted_and_binary_files_excluded(self):
        files = clm.parse_diff(self.DIFF)
        self.assertNotIn("old.py", files)
        self.assertNotIn("bin/blob", files)

    def test_manifest_schema_shape(self):
        m = clm.build_manifest(clm.parse_diff(self.DIFF))
        self.assertEqual(m["schema_version"], "1.0.0")
        self.assertEqual(m["files"][0]["path"], "src/a.py")
        self.assertEqual(m["files"][0]["ranges"][0], {"start_line": 11, "end_line": 13})
        for f in m["files"]:
            self.assertFalse(f["path"].startswith("/"))
            self.assertTrue(f["ranges"])

    def test_merge_ranges(self):
        self.assertEqual(clm.merge_ranges([(5, 6), (1, 2), (3, 4), (10, 12), (11, 15)]), [(1, 6), (10, 15)])


class ChangedLinesManifestGitFallback(unittest.TestCase):
    """Base refs that cannot be compared must degrade to 'everything changed', not crash."""

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self._git("init", "-q", "-b", "main")
        (self.repo / "a.py").write_text("x = 1\ny = 2\n")
        self._git("add", "a.py")
        self._git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "one")

    def _git(self, *a):
        subprocess.run(["git", "-C", str(self.repo), *a], check=True, capture_output=True)

    def _files(self, base: str):
        return clm.parse_diff(clm.git_diff(self.repo, base, staged=False))

    def test_single_commit_repo_head_tilde_falls_back_to_all_lines(self):
        self.assertEqual(self._files("HEAD~1"), {"a.py": [(1, 2)]})

    def test_nonexistent_ref_falls_back(self):
        self.assertEqual(self._files("origin/does-not-exist"), {"a.py": [(1, 2)]})

    def test_real_base_uses_merge_base_semantics(self):
        (self.repo / "a.py").write_text("x = 1\ny = 2\nz = 3\n")
        self._git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "two")
        self.assertEqual(self._files("HEAD~1"), {"a.py": [(3, 3)]})

    def test_fallback_is_announced_on_stderr(self):
        r = subprocess.run([sys.executable, str(GEN), "--base", "HEAD~1", "--repo", str(self.repo)],
                           capture_output=True, text=True)
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertIn("treating every tracked line as changed", r.stderr)
        self.assertEqual(json.loads(r.stdout)["files"][0]["path"], "a.py")


class PluginManifestsAgreeWithSkills(unittest.TestCase):
    def test_marketplace_lists_every_skill_dir(self):
        mp = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
        listed = {Path(s).name for p in mp["plugins"] for s in p["skills"]}
        self.assertEqual(listed, {d.name for d in SKILL_DIRS})

    def test_plugin_json_has_required_fields(self):
        pj = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
        for k in ("name", "description", "version", "author", "license", "repository"):
            self.assertIn(k, pj)
        self.assertEqual(pj["license"], "MIT")

    def test_readme_mentions_every_skill(self):
        readme = (ROOT / "README.md").read_text()
        for d in SKILL_DIRS:
            self.assertIn(f"`{d.name}`", readme)


if __name__ == "__main__":
    unittest.main()
