"""Tests for skills/architecture-baseline/scripts/architecture_baseline.py (no analyzer needed)."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "architecture-baseline" / "scripts" / "architecture_baseline.py"
spec = importlib.util.spec_from_file_location("ab", SCRIPT)
ab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ab)  # type: ignore[union-attr]

REPORT = {
    "analyzer_version": "2.44.0", "scoring_policy_version": "2.1.0", "schema_version": "1.12.0",
    "architecture_signal_score": 8.0, "architecture_signal_label": "Good",
    "architecture_signal_scope": {"by_language": {"python": {"files": 10}}},
    "breakdown": {"dsa_score": 6.0, "design_score": 9.0, "maturity_score": 8.0,
                  "design_patterns_count": 2, "dsa_patterns_count": 1, "files_scanned": 10, "total_lines": 1000},
    "design_patterns": {
        "database_orm": {"files": ["app/adapters/pg.py", "app/adapters/sqlite.py", "migrations/001.py"]},
        "event_sourcing_cqrs": {"files": ["app/domain/events.py"]},
    },
    "dsa_patterns": {"hash_map": {"files": ["app/domain/index.py", "tests/test_x.py"]}},
    "package_intelligence": {"dependencies": ["fastapi==0.1"]},
}


class LayerModel(unittest.TestCase):
    def test_layer_of_depth(self):
        self.assertEqual(ab.layer_of("app/adapters/pg.py", 2), "app/adapters")
        self.assertEqual(ab.layer_of("migrations/001.py", 2), "migrations")
        self.assertEqual(ab.layer_of("setup.py", 2), ".")
        self.assertEqual(ab.layer_of("a/b/c/d.py", 3), "a/b/c")

    def test_snapshot_shape_and_pattern_layers(self):
        s = ab.build_snapshot(REPORT, 2, "proj")
        self.assertEqual(s["design_patterns"]["database_orm"], {"app/adapters": 2, "migrations": 1})
        self.assertEqual(s["design_patterns"]["event_sourcing_cqrs"], {"app/domain": 1})
        self.assertEqual(s["dsa_patterns"]["hash_map"], {"app/domain": 1, "tests": 1})
        self.assertEqual(set(s["layers"]), {"app/adapters", "migrations", "app/domain", "tests"})
        self.assertEqual(s["score"], 8.0)
        self.assertEqual(s["dependencies"], ["fastapi==0.1"])
        self.assertEqual(s["snapshot_version"], ab.SNAPSHOT_VERSION)


class Drift(unittest.TestCase):
    def setUp(self):
        self.base = ab.build_snapshot(REPORT, 2, "proj")

    def _cur(self, **changes):
        r = json.loads(json.dumps(REPORT))
        for k, v in changes.items():
            r[k] = v
        return ab.build_snapshot(r, 2, "proj")

    def test_no_change_no_drift(self):
        res = ab.compare(self.base, self._cur(), 0.5)
        self.assertEqual(res, {"violations": [], "notes": []})

    def test_pattern_in_new_layer_is_violation(self):
        dp = json.loads(json.dumps(REPORT["design_patterns"]))
        dp["database_orm"]["files"].append("app/domain/leak.py")
        res = ab.compare(self.base, self._cur(design_patterns=dp), 0.5)
        self.assertEqual(len(res["violations"]), 1)
        self.assertIn("'database_orm' now appears in ['app/domain']", res["violations"][0])

    def test_new_pattern_and_vanished_pattern_are_notes_not_violations(self):
        dp = json.loads(json.dumps(REPORT["design_patterns"]))
        dp.pop("event_sourcing_cqrs")
        dp["rate_limiting"] = {"files": ["app/adapters/limiter.py"]}
        res = ab.compare(self.base, self._cur(design_patterns=dp), 0.5)
        self.assertEqual(res["violations"], [])
        self.assertTrue(any("new design pattern 'rate_limiting'" in n for n in res["notes"]))
        self.assertTrue(any("no longer detected" in n for n in res["notes"]))

    def test_score_drop_threshold(self):
        self.assertTrue(ab.compare(self.base, self._cur(architecture_signal_score=7.5), 0.5)["violations"])
        res = ab.compare(self.base, self._cur(architecture_signal_score=7.7), 0.5)
        self.assertEqual(res["violations"], [])
        self.assertTrue(any("score 8.0 -> 7.7" in n for n in res["notes"]))

    def test_policy_change_is_flagged_not_compared(self):
        res = ab.compare(self.base, self._cur(scoring_policy_version="3.0.0"), 0.5)
        self.assertTrue(any("not comparable" in n for n in res["notes"]))


class CliAndMarkdown(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.report = self.tmp / "r.json"
        self.report.write_text(json.dumps(REPORT))

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args, "--project", str(self.tmp)],
                              capture_output=True, text=True)

    def test_snapshot_then_check_roundtrip_and_exit_codes(self):
        r = self.run_cli("snapshot", "--from-json", str(self.report))
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertTrue((self.tmp / "ARCHITECTURE.snapshot.json").is_file())
        md = (self.tmp / "ARCHITECTURE.md").read_text()
        self.assertIn(ab.BEGIN, md); self.assertIn("`database_orm`", md)
        self.assertIn("`event_sourcing_cqrs` appears only in `app/domain`", md)

        r = self.run_cli("check", "--from-json", str(self.report), "--fail-on-drift")
        self.assertEqual(0, r.returncode); self.assertIn("no drift", r.stdout)

        drift = json.loads(json.dumps(REPORT)); drift["design_patterns"]["database_orm"]["files"].append("app/domain/leak.py")
        (self.tmp / "d.json").write_text(json.dumps(drift))
        r = self.run_cli("check", "--from-json", str(self.tmp / "d.json"), "--fail-on-drift")
        self.assertEqual(4, r.returncode); self.assertIn("VIOLATION", r.stdout)
        r = self.run_cli("check", "--from-json", str(self.tmp / "d.json"))
        self.assertEqual(0, r.returncode, "without --fail-on-drift the check only reports")
        r = self.run_cli("check", "--from-json", str(self.tmp / "d.json"), "--json")
        self.assertEqual(1, len(json.loads(r.stdout)["violations"]))

    def test_hand_written_markdown_is_preserved_on_resnapshot(self):
        self.run_cli("snapshot", "--from-json", str(self.report))
        md = self.tmp / "ARCHITECTURE.md"
        md.write_text("# Why hexagonal\n\nBecause ports.\n\n" + md.read_text() + "\n## Decisions\n\nADR-1.\n")
        self.run_cli("snapshot", "--from-json", str(self.report))
        text = md.read_text()
        self.assertIn("Because ports.", text); self.assertIn("ADR-1.", text)
        self.assertEqual(text.count(ab.BEGIN), 1); self.assertEqual(text.count(ab.END), 1)

    def test_check_without_snapshot_fails_clearly(self):
        r = self.run_cli("check", "--from-json", str(self.report))
        self.assertEqual(1, r.returncode); self.assertIn("run `snapshot` first", r.stderr)

    def test_missing_analyzer_is_reported_not_faked(self):
        import os
        env = dict(os.environ, PATH="/nonexistent"); env.pop("CQA_CMD", None)
        r = subprocess.run([sys.executable, str(SCRIPT), "snapshot", "--project", str(self.tmp)],
                           env=env, capture_output=True, text=True)
        self.assertEqual(1, r.returncode); self.assertIn("not found", r.stderr)
        self.assertFalse((self.tmp / "ARCHITECTURE.snapshot.json").exists())


if __name__ == "__main__":
    unittest.main()
