"""Evidence tests never recursively launch unittest discovery."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("evidence", ROOT / "scripts/evidence.py")
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


def green():
    return {
        "evidence_version": evidence.EVIDENCE_VERSION,
        "inputs": {"sha256": "a" * 64, "file_count": 3},
        "release_version": "1.0.0",
        "skills": {"s": "1.0.0"},
        "tests": {"exit_code": 0, "ok": True, "count": 2},
        "validator": {"exit_code": 0},
        "commit": "old",
        "python": "3.12.0",
    }


class EvidenceCli(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "EVIDENCE.json"

    def run_cli(self, current=None, stored=None, check=True):
        if stored is not None:
            self.path.write_text(json.dumps(stored))
        out, err = io.StringIO(), io.StringIO()
        with patch.object(evidence, "collect", return_value=current or green()) as collect:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                result = evidence.main(
                    ["--output", str(self.path)] + (["--check"] if check else [])
                )
        self.assertEqual(out.getvalue(), "")
        return result, err.getvalue(), collect

    def test_generation_and_check_allow_commit_python_provenance_changes(self):
        code, err, collect = self.run_cli(check=False)
        self.assertEqual(code, 0, err)
        self.assertIn("validator exit 0", err)
        collect.assert_called_once_with()
        stored = json.loads(self.path.read_text())
        current = green()
        current.update(commit="new", python="3.13.0")
        code, err, collect = self.run_cli(current, stored)
        self.assertEqual(code, 0, err)
        self.assertIn("is current", err)
        collect.assert_called_once_with()

    def test_missing_malformed_or_nonobject_evidence_fails_before_collection(self):
        for text in (None, "{", "[]", "null", '"text"'):
            with self.subTest(text=text):
                self.path.unlink(missing_ok=True)
                if text is not None:
                    self.path.write_text(text)
                code, err, collect = self.run_cli()
                self.assertEqual(code, 1, err)
                collect.assert_not_called()

    def test_stale_or_malformed_stored_results_fail(self):
        variants = []
        for key in ("evidence_version", "inputs", "release_version", "skills"):
            bad = green()
            bad[key] = None
            variants.append(bad)
        for value in (
            None,
            [],
            "bad",
            {},
            {"count": 2, "ok": True, "exit_code": True},
            {"count": 0, "ok": True, "exit_code": 0},
            {"count": 3, "ok": True, "exit_code": 0},
        ):
            bad = green()
            bad["tests"] = value
            variants.append(bad)
        for bad in variants:
            with self.subTest(bad=bad):
                code, err, collect = self.run_cli(stored=bad)
                self.assertEqual(code, 1, err)
                collect.assert_called_once_with()

    def test_failed_current_run_never_overwrites_prior_evidence(self):
        for field, value in (
            ("count", 0),
            ("count", None),
            ("count", True),
            ("ok", False),
            ("exit_code", 1),
        ):
            current = green()
            current["tests"][field] = value
            self.path.write_text("keep previous evidence")
            code, err, _ = self.run_cli(current=current, check=False)
            self.assertEqual(code, 1, err)
            self.assertEqual(self.path.read_text(), "keep previous evidence")
        current = green()
        current["validator"]["exit_code"] = 1
        self.assertEqual(self.run_cli(current=current, check=False)[0], 1)

    def test_collection_and_output_errors_are_reported(self):
        for failure in (
            OSError("unreadable"),
            ValueError("changed"),
            subprocess.TimeoutExpired(["validator"], 120),
        ):
            with patch.object(evidence, "collect", side_effect=failure):
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(evidence.main(["--output", str(self.path)]), 1)
            self.assertFalse(self.path.exists())
        self.path = self.path / "missing-parent" / "out.json"
        self.assertEqual(self.run_cli(check=False)[0], 1)

    def test_unrelated_output_is_not_overwritten(self):
        for text in ("unrelated source", "{}", "[]"):
            self.path.write_text(text)
            self.assertEqual(self.run_cli(check=False)[0], 1)
            self.assertEqual(self.path.read_text(), text)

    def test_failed_atomic_replace_preserves_previous_evidence(self):
        self.path.write_text(json.dumps(green()))
        original = self.path.read_bytes()
        with patch.object(evidence.os, "replace", side_effect=OSError("write failed")):
            self.assertEqual(self.run_cli(check=False)[0], 1)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob(".evidence-*")), [])

    def test_symlink_output_is_refused(self):
        target = self.path.with_name("target")
        target.write_text("preserve")
        self.path.symlink_to(target)
        self.assertEqual(self.run_cli(check=False)[0], 1)
        self.assertEqual(target.read_text(), "preserve")


class EvidenceCollector(unittest.TestCase):
    def test_full_discovery_runs_exactly_once_and_in_order(self):
        results = [
            subprocess.CompletedProcess([], 0, "valid\n", ""),
            subprocess.CompletedProcess([], 0, "", "Ran 2 tests in 0.01s\n\nOK\n"),
            subprocess.CompletedProcess([], 0, "abc\n", ""),
            subprocess.CompletedProcess([], 0, " M file\n", ""),
        ]
        with (
            patch.object(evidence, "input_digest", return_value=green()["inputs"]),
            patch.object(evidence, "sh", side_effect=results) as sh,
        ):
            result = evidence.collect()
        self.assertEqual(result["tests"], green()["tests"])
        self.assertEqual(
            sh.call_args_list,
            [
                call(sys.executable, "scripts/validate.py"),
                call(sys.executable, "-m", "unittest", "discover", "-s", "tests"),
                call("git", "rev-parse", "HEAD"),
                call("git", "status", "--porcelain", "--", ":!EVIDENCE.json"),
            ],
        )

    def test_zero_missing_or_unsuccessful_test_summary_is_not_green(self):
        for stderr, exit_code in (
            ("Ran 0 tests in 0s\nOK\n", 0),
            ("OK\n", 0),
            ("Ran 1 test in 0s\nNOT OK\n", 0),
            ("Ran 2 tests in 0s\nOK\n", 1),
        ):
            outputs = [
                subprocess.CompletedProcess([], 0, "valid", ""),
                subprocess.CompletedProcess([], exit_code, "", stderr),
                subprocess.CompletedProcess([], 0, "abc", ""),
                subprocess.CompletedProcess([], 0, "", ""),
            ]
            with (
                patch.object(evidence, "input_digest", return_value=green()["inputs"]),
                patch.object(evidence, "sh", side_effect=outputs),
            ):
                self.assertFalse(evidence.is_green(evidence.collect()))

    def test_inputs_changing_during_validation_fail(self):
        response = subprocess.CompletedProcess([], 0, "abc", "Ran 1 test in 0s\nOK\n")
        with (
            patch.object(evidence, "sh", return_value=response),
            patch.object(evidence, "input_digest", side_effect=[{"a": 1}, {"a": 2}]),
        ):
            with self.assertRaisesRegex(ValueError, "changed during"):
                evidence.collect()

    def test_git_failure_is_not_green_provenance(self):
        with patch.object(
            evidence, "sh", return_value=subprocess.CompletedProcess([], 128, "", "")
        ):
            with self.assertRaises(ValueError):
                evidence.input_digest()


class InputDigest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.patch = patch.object(evidence, "ROOT", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.git("init", "-q")
        (self.root / "tracked.py").write_text("x = 1\n")
        self.git("add", "tracked.py")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)

    def test_tracks_untracked_content_paths_deletions_and_executable_bits(self):
        initial = evidence.input_digest()
        new = self.root / "new.txt"
        new.write_text("one")
        added = evidence.input_digest()
        self.assertNotEqual(initial, added)
        new.write_text("two")
        changed = evidence.input_digest()
        self.assertNotEqual(added, changed)
        new.chmod(0o700)
        executable = evidence.input_digest()
        self.assertNotEqual(changed, executable)
        new.rename(self.root / "renamed.txt")
        self.assertNotEqual(executable, evidence.input_digest())
        (self.root / "tracked.py").unlink()
        self.assertEqual(evidence.input_digest()["file_count"], 1)

    def test_commit_and_generated_outputs_do_not_change_digest(self):
        initial = evidence.input_digest()
        (self.root / "EVIDENCE.json").write_text("generated")
        runs = self.root / "evals/runs"
        runs.mkdir(parents=True)
        (runs / "run.jsonl").write_text("generated")
        self.git("add", ".")
        self.git(
            "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "test"
        )
        self.assertEqual(initial, evidence.input_digest())

    def test_unsafe_paths_nonfiles_and_empty_input_sets_fail(self):
        folder = self.root / "folder"
        folder.mkdir()
        for names in ("../outside\0", "/absolute\0", "folder\0", "EVIDENCE.json\0"):
            with (
                self.subTest(names=names),
                patch.object(evidence, "git_output", return_value=names),
            ):
                with self.assertRaises(ValueError):
                    evidence.input_digest()

    def test_symlink_inputs_fail_without_reading_external_content(self):
        (self.root / "linked.py").symlink_to(self.root / "tracked.py")
        with self.assertRaisesRegex(ValueError, "symlinked"):
            evidence.input_digest()


if __name__ == "__main__":
    unittest.main()
