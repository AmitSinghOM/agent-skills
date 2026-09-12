"""Tests for skills/code-quality-gate/scripts/run-gate.sh using a fake analyzer.

The fake records its argv as JSON so we can assert exactly what the wrapper
forwards, without needing cqa-analyzer installed.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "skills" / "code-quality-gate" / "scripts" / "run-gate.sh"

FAKE = """#!/usr/bin/env bash
# Fake analyzer: dump argv as JSON to $FAKE_ARGV, exit $FAKE_RC (default 0).
python3 - "$@" <<'PY'
import json, os, sys
open(os.environ["FAKE_ARGV"], "w").write(json.dumps(sys.argv[1:]))
PY
exit "${FAKE_RC:-0}"
"""


class RunGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.fake = self.tmp / "fake-analyzer"
        self.fake.write_text(FAKE)
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IXUSR)
        self.argv_file = self.tmp / "argv.json"
        # A tiny git repo so --base/--staged have something to diff.
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self._git("init", "-q", "-b", "main")
        (self.repo / "a.py").write_text("x = 1\n")
        self._git("add", "a.py")
        self._git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "one")
        (self.repo / "a.py").write_text("x = 1\ny = 2\n")
        self._git("add", "a.py")

    def _git(self, *a):
        subprocess.run(["git", "-C", str(self.repo), *a], check=True, capture_output=True)

    def run_gate(self, *args, analyzer: str | None = "fake", fake_rc: int = 0):
        env = dict(os.environ, FAKE_ARGV=str(self.argv_file), FAKE_RC=str(fake_rc))
        env.pop("CQA_CMD", None)
        if analyzer == "fake":
            env["CQA_CMD"] = str(self.fake)
        elif analyzer is not None:
            env["CQA_CMD"] = analyzer
        return subprocess.run([str(GATE), *args], env=env, cwd=self.repo, capture_output=True, text=True)

    def forwarded(self) -> list[str]:
        return json.loads(self.argv_file.read_text())

    # --- usage errors: exit 2 -------------------------------------------
    def test_requires_exactly_one_selector(self):
        r = self.run_gate()
        self.assertEqual(2, r.returncode); self.assertIn("exactly one of", r.stderr)
        r = self.run_gate("--base", "main", "--staged")
        self.assertEqual(2, r.returncode); self.assertIn("mutually exclusive", r.stderr)

    def test_missing_values_and_flag_as_value(self):
        for flag in ("--base", "--format", "--fail-on", "--baseline", "--write-baseline",
                     "--config", "--expect-config-fingerprint", "--project"):
            with self.subTest(flag=flag):
                r = self.run_gate(flag)
                self.assertEqual(2, r.returncode); self.assertIn(f"{flag} requires a value", r.stderr)
        r = self.run_gate("--base", "--staged")
        self.assertEqual(2, r.returncode); self.assertIn("--base requires a value", r.stderr)

    def test_invalid_enums_and_fingerprint(self):
        self.assertEqual(2, self.run_gate("--staged", "--format", "xml").returncode)
        self.assertEqual(2, self.run_gate("--staged", "--fail-on", "new").returncode)
        self.assertEqual(2, self.run_gate("--staged", "--expect-config-fingerprint", "abc").returncode)
        self.assertEqual(2, self.run_gate("--staged", "--config", "x.toml", "--no-project-config").returncode)
        self.assertEqual(2, self.run_gate("--frobnicate").returncode)

    # --- environment errors: exit 3 ---------------------------------------
    def test_cqa_cmd_must_be_absolute_executable(self):
        r = self.run_gate("--staged", analyzer="code-quality-analyzer")
        self.assertEqual(3, r.returncode); self.assertIn("absolute path", r.stderr)
        r = self.run_gate("--staged", analyzer=str(self.tmp / "missing"))
        self.assertEqual(3, r.returncode); self.assertIn("not an executable", r.stderr)

    def test_missing_baseline_file(self):
        r = self.run_gate("--staged", "--baseline", str(self.tmp / "nope.json"))
        self.assertEqual(3, r.returncode); self.assertIn("not found", r.stderr)

    # --- forwarding -------------------------------------------------------
    def test_staged_forwards_manifest_offline_and_fail_on(self):
        r = self.run_gate("--staged", "--format", "sarif")
        self.assertEqual(0, r.returncode, r.stderr)
        argv = self.forwarded()
        self.assertEqual(argv[:4], [".", "--output-format", "sarif", "--offline"])
        self.assertIn("--changed-lines-manifest", argv)
        self.assertEqual(argv[-2:], ["--fail-on", "warning"])
        manifest_idx = argv.index("--changed-lines-manifest") + 1
        # The manifest was a real file when the analyzer ran (wrapper cleans it up after).
        self.assertTrue(argv[manifest_idx].startswith(os.environ.get("TMPDIR", "/tmp").rstrip("/")))

    def test_baseline_config_pinning_and_strict_forwarded(self):
        baseline = self.tmp / "b.json"; baseline.write_text("{}")
        fp = "a" * 64
        r = self.run_gate("--staged", "--baseline", str(baseline), "--strict",
                          "--no-project-config", "--expect-config-fingerprint", fp)
        self.assertEqual(0, r.returncode, r.stderr)
        argv = self.forwarded()
        for expected in (["--strict"], ["--no-project-config"], ["--expect-config-fingerprint", fp],
                         ["--baseline", str(baseline)], ["--new-findings-only"]):
            self.assertTrue(all(e in argv for e in expected), expected)
        self.assertNotIn(fp, r.stderr, "digest must not be echoed to logs")

    def test_operands_are_distinct_argv_not_shell_interpolated(self):
        marker = self.tmp / "never"
        cfg = f"x.toml; touch {marker}"
        r = self.run_gate("--staged", "--config", cfg, "--report-only")
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertIn(cfg, self.forwarded())          # passed through verbatim as ONE arg
        self.assertFalse(marker.exists())              # never executed
        self.assertNotIn("touch", r.stderr)            # never echoed

    def test_write_baseline_mode_has_no_gate(self):
        r = self.run_gate("--write-baseline", str(self.tmp / "out.json"))
        self.assertEqual(0, r.returncode, r.stderr)
        argv = self.forwarded()
        self.assertIn("--write-baseline", argv)
        for absent in ("--fail-on", "--changed-lines-manifest", "--new-findings-only"):
            self.assertNotIn(absent, argv)

    # --- exit-code passthrough ---------------------------------------------
    def test_gate_failure_passes_through_and_report_only_remaps(self):
        self.assertEqual(4, self.run_gate("--staged", fake_rc=4).returncode)
        r = self.run_gate("--staged", "--report-only", fake_rc=4)
        self.assertEqual(0, r.returncode)
        self.assertIn("REPORT ONLY", r.stderr)
        self.assertNotIn("--fail-on", self.forwarded())
        self.assertEqual(6, self.run_gate("--staged", "--report-only", fake_rc=6).returncode,
                         "report-only must not mask a config fingerprint mismatch")


if __name__ == "__main__":
    unittest.main()
