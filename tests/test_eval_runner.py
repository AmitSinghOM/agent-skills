"""Tests for scripts/eval_runner.py using inert fake runners (no model, no network).

Each fake is a tiny Python script run with sys.executable inside a throwaway repo
layout (skills/<name>/SKILL.md + evals/scenarios.json), so nothing touches the real
evals/runs directory.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shlex
import shutil
import signal
import stat
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "eval_runner.py"
spec = importlib.util.spec_from_file_location("eval_runner", SCRIPT)
er = importlib.util.module_from_spec(spec)
spec.loader.exec_module(er)  # type: ignore[union-attr]

SCENARIOS = {"scenarios": [
    {"skills": ["alpha"], "query": "static question", "expected_behavior": ["does X"], "mode": "static"},
    {"skills": ["alpha"], "query": "tool question", "expected_behavior": ["runs Y"], "mode": "integration"},
]}

POSIX = er.process_group_cleanup_supported()


def wait_dead(pid: int, seconds: float = 5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


class EvalRunnerBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="eval-runner-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "repo"
        self.add_skill("alpha", "# alpha skill\n", SCENARIOS)
        self.marker = self.tmp / "runner-was-called"
        self.pids: list[int] = []
        patcher = mock.patch.object(er, "TERM_GRACE_SECONDS", 0.3)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        for pid in self.pids:  # never leave a fake behind, even when an assertion failed
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)

    def add_skill(self, name: str, skill_md: str, scenarios) -> Path:
        d = self.root / "skills" / name
        (d / "evals").mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(skill_md, encoding="utf-8")
        (d / "evals" / "scenarios.json").write_text(json.dumps(scenarios), encoding="utf-8")
        return d

    def fake(self, body: str, name: str = "fake.py") -> str:
        path = self.tmp / name
        header = f"import os, sys, time, signal, subprocess\nMARKER = {str(self.marker)!r}\n"
        path.write_text(header + textwrap.dedent(body), encoding="utf-8")
        return f"{shlex.quote(sys.executable)} {shlex.quote(str(path))}"

    def run_main(self, *args: str) -> tuple[int, str]:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            try:
                rc = er.main(list(args), repo_root=self.root)
            except SystemExit as e:
                rc = e.code
        return rc, err.getvalue()

    def base_args(self, runner: str, label: str = "run1", *extra: str) -> list[str]:
        return ["--runner", runner, "--runner-id", "fake-runner 0.0.1", "--label", label, *extra]

    def run_file(self, label: str = "run1") -> Path:
        return self.root / "evals" / "runs" / f"{label}.jsonl"

    def records(self, label: str = "run1") -> list[dict]:
        return [json.loads(line) for line in self.run_file(label).read_text(encoding="utf-8").splitlines()]

    def assert_no_artifacts(self):
        self.assertFalse((self.root / "evals").exists(), "validation failure must not create evals/")


ECHO = """
prompt = sys.argv[-1] if len(sys.argv) > 1 else sys.stdin.read()
open(MARKER, "a").write("called\\n")
import hashlib
print("ANSWER", hashlib.sha256(prompt.encode()).hexdigest())
"""


class InputValidation(EvalRunnerBase):
    def test_label_traversal_and_unsafe_basenames_rejected_before_any_write(self):
        runner = self.fake(ECHO)
        for label in ("../escape", "a/b", "/abs", "..", ".hidden", "", "x" * 101, "a\x00b", "a b",
                      "tab\tname", "new\nline", "-dash", "ünicode", "a\\b"):
            with self.subTest(label=label):
                rc, err = self.run_main(*self.base_args(runner, label, "--dry-run"))
                self.assertEqual(2, rc, err)
                self.assertIn("--label", err)
                self.assert_no_artifacts()
        self.assertFalse((self.tmp / "escape.jsonl").exists())
        self.assertFalse(self.marker.exists())

    def test_empty_unparseable_or_missing_runner(self):
        for runner, msg in (("", "--runner is empty"), ("   ", "--runner is empty"),
                            ("'unbalanced", "cannot be parsed"),
                            ("definitely-not-a-real-runner-xyz", "runner executable not found")):
            with self.subTest(runner=runner):
                rc, err = self.run_main(*self.base_args(runner))
                self.assertEqual(2, rc, err)
                self.assertIn(msg, err)
                self.assert_no_artifacts()

    def test_runner_id_required_and_bounded(self):
        runner = self.fake(ECHO)
        self.assertEqual(2, self.run_main("--runner", runner, "--label", "x")[0])
        for rid in ("", "   ", "a" * 201, "bad\nid"):
            with self.subTest(rid=rid):
                rc, err = self.run_main("--runner", runner, "--runner-id", rid, "--label", "x", "--dry-run")
                self.assertEqual(2, rc, err)
                self.assertIn("--runner-id", err)
        self.assert_no_artifacts()

    def test_invalid_skill_selection(self):
        rc, err = self.run_main(*self.base_args(self.fake(ECHO), "x", "--skill", "../alpha"))
        self.assertEqual(2, rc)
        self.assertIn("unknown --skill", err)
        rc, err = self.run_main(*self.base_args(self.fake(ECHO), "x", "--skill", "nope", "--skill", "alpha"))
        self.assertEqual(2, rc)
        self.assertIn("'nope'", err)
        self.assert_no_artifacts()

    def test_no_scenarios_selected(self):
        self.add_skill("beta", "# beta\n", {"scenarios": [SCENARIOS["scenarios"][0]]})
        rc, err = self.run_main(*self.base_args(self.fake(ECHO), "x", "--skill", "beta", "--mode", "integration"))
        self.assertEqual(2, rc)
        self.assertIn("no scenarios selected", err)
        self.assert_no_artifacts()

    def test_malformed_scenarios_rejected(self):
        self.add_skill("beta", "# beta\n", {"scenarios": [{"query": 1, "mode": "static"}]})
        rc, err = self.run_main(*self.base_args(self.fake(ECHO), "x", "--dry-run"))
        self.assertEqual(2, rc)
        self.assertIn("beta[0]", err)
        self.assert_no_artifacts()

    def test_invalid_scenario_mode_or_expected_behavior_rejected(self):
        bad = [{"query": "q", "mode": "statc", "expected_behavior": ["x"]},
               {"query": "q", "mode": "static", "expected_behavior": []},
               {"query": "q", "mode": "static", "expected_behavior": ["", 3]},
               {"query": " ", "mode": "static", "expected_behavior": ["x"]}]
        for sc in bad:
            with self.subTest(sc=sc):
                self.add_skill("beta", "# beta\n", {"scenarios": [sc]})
                rc, err = self.run_main(*self.base_args(self.fake(ECHO), "x", "--dry-run", "--mode", "all"))
                self.assertEqual(2, rc)
                self.assertIn("beta[0]", err)
                self.assert_no_artifacts()

    @unittest.skipIf(os.geteuid() == 0, "root ignores directory permissions")
    def test_unwritable_output_dir_is_clean_cli_error(self):
        (self.root / "evals").mkdir()
        (self.root / "evals").chmod(0o500)
        self.addCleanup((self.root / "evals").chmod, 0o700)
        rc, err = self.run_main(*self.base_args(self.fake(ECHO), "x", "--dry-run"))
        self.assertEqual(2, rc)
        self.assertIn("cannot create evals/runs/x.jsonl: PermissionError", err)
        self.assertNotIn("Traceback", err)

    def test_timeout_and_output_bounds(self):
        runner = self.fake(ECHO)
        for flag, value in (("--timeout", "0"), ("--timeout", "-5"), ("--timeout", "3601"), ("--timeout", "1.5"),
                            ("--max-output-bytes", "0"), ("--max-output-bytes", str(16 * 1024 * 1024 + 1))):
            with self.subTest(flag=flag, value=value):
                rc, _ = self.run_main(*self.base_args(runner, "x", flag, value))
                self.assertEqual(2, rc)
        self.assert_no_artifacts()

    def test_unsupported_platform_fails_real_run_but_allows_dry_run(self):
        runner = self.fake(ECHO)
        with mock.patch.object(er, "process_group_cleanup_supported", return_value=False):
            rc, err = self.run_main(*self.base_args(runner))
            self.assertEqual(2, rc)
            self.assertIn("unsupported platform", err)
            self.assert_no_artifacts()
            self.assertEqual(0, self.run_main(*self.base_args(runner, "dry", "--dry-run"))[0])
        self.assertFalse(self.marker.exists())
        with mock.patch.object(er, "process_group_cleanup_supported", return_value=False):
            with self.assertRaises(RuntimeError):
                er.run_one([sys.executable, "-c", "pass"], "p", False, 5)


class OutputSafety(EvalRunnerBase):
    def test_dry_run_records_provenance_without_calling_runner(self):
        rc, err = self.run_main(*self.base_args(self.fake(ECHO), "dry", "--dry-run"))
        self.assertEqual(0, rc, err)
        self.assertFalse(self.marker.exists(), "dry run must not invoke the runner")
        (rec,) = self.records("dry")
        skill_md = (self.root / "skills/alpha/SKILL.md").read_bytes()
        scen_file = (self.root / "skills/alpha/evals/scenarios.json").read_bytes()
        sc = SCENARIOS["scenarios"][0]
        self.assertEqual(rec["runner_id"], "fake-runner 0.0.1")
        self.assertTrue(rec["dry_run"])
        self.assertEqual(rec["skill_sha256"], hashlib.sha256(skill_md).hexdigest())
        self.assertEqual(rec["scenarios_file_sha256"], hashlib.sha256(scen_file).hexdigest())
        self.assertEqual(rec["scenario_sha256"], hashlib.sha256(
            json.dumps(sc, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest())
        prompt = er.build_prompt(self.root / "skills/alpha", sc["query"])
        self.assertEqual(rec["prompt_sha256"], hashlib.sha256(prompt.encode()).hexdigest())
        self.assertEqual(rec["process"]["status"], "not_run")
        self.assertIsNone(rec["output"])
        self.assertEqual(rec["grading"], {"status": "ungraded", "grader": None, "expected_behavior_met": None})

    def test_file_is_private_and_never_overwritten(self):
        runner = self.fake(ECHO)
        self.assertEqual(0, self.run_main(*self.base_args(runner, "dup", "--dry-run"))[0])
        path = self.run_file("dup")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o077, 0, "run file must not be group/world readable")
        before = path.read_bytes()
        rc, err = self.run_main(*self.base_args(runner, "dup", "--dry-run"))
        self.assertEqual(2, rc)
        self.assertIn("refusing to overwrite", err)
        self.assertEqual(before, path.read_bytes())
        for rel in ("evals", "evals/runs"):  # directories this script created
            self.assertEqual(stat.S_IMODE((self.root / rel).stat().st_mode) & 0o077, 0, rel)

    def test_case_insensitive_collision_refused(self):
        probe = self.tmp / "CaseProbe"
        probe.write_text("")
        if not (self.tmp / "caseprobe").exists():
            self.skipTest("case-sensitive filesystem")
        runner = self.fake(ECHO)
        self.assertEqual(0, self.run_main(*self.base_args(runner, "Run", "--dry-run"))[0])
        rc, err = self.run_main(*self.base_args(runner, "run", "--dry-run"))
        self.assertEqual(2, rc)
        self.assertIn("refusing to overwrite", err)

    def test_symlinked_run_file_refused_and_target_untouched(self):
        runs = self.root / "evals" / "runs"
        runs.mkdir(parents=True)
        target = self.tmp / "victim.txt"
        target.write_text("keep me")
        (runs / "link.jsonl").symlink_to(target)
        (runs / "dangling.jsonl").symlink_to(self.tmp / "does-not-exist")
        for label in ("link", "dangling"):
            with self.subTest(label=label):
                rc, err = self.run_main(*self.base_args(self.fake(ECHO), label, "--dry-run"))
                self.assertEqual(2, rc)
                self.assertIn("refusing", err)
        self.assertEqual("keep me", target.read_text())
        self.assertFalse((self.tmp / "does-not-exist").exists())

    def test_symlinked_run_dirs_refused(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        (self.root / "evals").mkdir()
        (self.root / "evals" / "runs").symlink_to(outside, target_is_directory=True)
        rc, err = self.run_main(*self.base_args(self.fake(ECHO), "x", "--dry-run"))
        self.assertEqual(2, rc)
        self.assertIn("refusing evals/runs", err)
        self.assertEqual([], list(outside.iterdir()))

        (self.root / "evals" / "runs").unlink()
        (self.root / "evals").rmdir()
        (self.root / "evals").symlink_to(outside, target_is_directory=True)
        rc, err = self.run_main(*self.base_args(self.fake(ECHO), "x", "--dry-run"))
        self.assertEqual(2, rc)
        self.assertIn("refusing evals", err)
        self.assertEqual([], list(outside.iterdir()))


@unittest.skipUnless(POSIX, "process-group cleanup is POSIX-only")
class ProcessOutcomes(EvalRunnerBase):
    def test_success_via_argv_and_stdin_is_ungraded(self):
        for label, extra in (("argv", ()), ("stdin", ("--stdin",))):
            with self.subTest(transport=label):
                rc, err = self.run_main(*self.base_args(self.fake(ECHO), label, *extra))
                self.assertEqual(0, rc, err)
                (rec,) = self.records(label)
                self.assertEqual(rec["prompt_transport"], label)
                self.assertEqual(rec["process"]["status"], "exited")
                self.assertEqual(rec["process"]["exit_code"], 0)
                self.assertEqual(rec["output"], f"ANSWER {rec['prompt_sha256']}")
                self.assertEqual(rec["grading"]["status"], "ungraded")
                self.assertIsNone(rec["grading"]["expected_behavior_met"], "exit 0 is not a behavioural pass")
                self.assertNotIn("pass", json.dumps(rec["process"]))

    def test_failure_exit_code_and_stderr_recorded_separately(self):
        runner = self.fake("print('partial'); print('boom', file=sys.stderr); sys.exit(3)\n")
        rc, err = self.run_main(*self.base_args(runner))
        self.assertEqual(0, rc, err)
        (rec,) = self.records()
        self.assertEqual((rec["process"]["status"], rec["process"]["exit_code"]), ("exited", 3))
        self.assertEqual((rec["output"], rec["stderr"]), ("partial", "boom"))
        self.assertIsNone(rec["grading"]["expected_behavior_met"])

    def test_oversized_combined_output_is_capped_and_group_killed(self):
        runner = self.fake("""
            chunk = b"x" * 65536
            while True:
                sys.stdout.buffer.write(chunk); sys.stdout.flush()
                sys.stderr.buffer.write(chunk); sys.stderr.flush()
        """)
        t0 = time.monotonic()
        rc, err = self.run_main(*self.base_args(runner, "big", "--max-output-bytes", "100000", "--timeout", "30"))
        self.assertEqual(0, rc, err)
        self.assertLess(time.monotonic() - t0, 10)
        (rec,) = self.records("big")
        p = rec["process"]
        self.assertEqual(p["status"], "output_limit")
        self.assertTrue(p["output_truncated"])
        self.assertLessEqual(p["stdout_bytes"] + p["stderr_bytes"], 100000)
        self.assertIsNone(p["exit_code"])

    def test_output_exactly_at_cap_is_not_truncated(self):
        runner = self.fake("sys.stdout.write('y' * 500)\n")
        self.assertEqual(0, self.run_main(*self.base_args(runner, "cap", "--max-output-bytes", "500"))[0])
        (rec,) = self.records("cap")
        self.assertEqual((rec["process"]["status"], rec["process"]["stdout_bytes"]), ("exited", 500))

    def _spawn_grandchild_body(self, pidfile: Path, *, detach_pipes: bool, leader: str) -> str:
        io_args = "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, " if detach_pipes else ""
        return f"""
            g = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                                 {io_args}stdin=subprocess.DEVNULL)
            open({str(pidfile)!r}, "w").write(str(g.pid))
            {leader}
        """

    def _grandchild_pid(self, pidfile: Path) -> int:
        pid = int(pidfile.read_text())
        self.pids.append(pid)
        return pid

    def test_timeout_kills_term_ignoring_leader_and_inherited_pipe_grandchild(self):
        pidfile = self.tmp / "gc.pid"
        runner = self.fake(self._spawn_grandchild_body(
            pidfile, detach_pipes=False,
            leader="signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)"))
        t0 = time.monotonic()
        rc, err = self.run_main(*self.base_args(runner, "slow", "--timeout", "1"))
        self.assertEqual(0, rc, err)
        self.assertLess(time.monotonic() - t0, 10)
        (rec,) = self.records("slow")
        self.assertEqual(rec["process"]["status"], "timeout")
        self.assertEqual(rec["process"]["signal"], signal.SIGKILL)
        self.assertIsNone(rec["process"]["exit_code"])
        self.assertTrue(wait_dead(self._grandchild_pid(pidfile)), "grandchild survived the group kill")

    def test_leader_exits_but_descendant_holds_pipe_is_timeout_and_cleaned(self):
        pidfile = self.tmp / "gc.pid"
        runner = self.fake(self._spawn_grandchild_body(pidfile, detach_pipes=False, leader="print('done')"))
        t0 = time.monotonic()
        rc, err = self.run_main(*self.base_args(runner, "hold", "--timeout", "1"))
        self.assertEqual(0, rc, err)
        self.assertLess(time.monotonic() - t0, 10)
        (rec,) = self.records("hold")
        self.assertEqual(rec["process"]["status"], "timeout")
        self.assertEqual(rec["output"], "done")
        self.assertTrue(wait_dead(self._grandchild_pid(pidfile)))

    def test_background_descendant_killed_after_normal_exit(self):
        pidfile = self.tmp / "gc.pid"
        runner = self.fake(self._spawn_grandchild_body(pidfile, detach_pipes=True, leader="print('ok')"))
        rc, err = self.run_main(*self.base_args(runner, "bg", "--timeout", "30"))
        self.assertEqual(0, rc, err)
        (rec,) = self.records("bg")
        self.assertEqual((rec["process"]["status"], rec["process"]["exit_code"]), ("exited", 0))
        self.assertTrue(wait_dead(self._grandchild_pid(pidfile)))

    def test_large_stdin_not_read_does_not_deadlock(self):
        self.add_skill("alpha", "# alpha\n" + "z" * (2 * 1024 * 1024), SCENARIOS)
        runner = self.fake("print('ignored stdin')\n")
        t0 = time.monotonic()
        rc, err = self.run_main(*self.base_args(runner, "bigin", "--stdin", "--timeout", "20"))
        self.assertEqual(0, rc, err)
        self.assertLess(time.monotonic() - t0, 10)
        (rec,) = self.records("bigin")
        self.assertEqual((rec["process"]["status"], rec["output"]), ("exited", "ignored stdin"))

    def test_runner_that_reads_large_stdin_gets_all_of_it(self):
        self.add_skill("alpha", "# alpha\n" + "q" * (1024 * 1024), SCENARIOS)
        rc, err = self.run_main(*self.base_args(self.fake(ECHO), "readall", "--stdin", "--timeout", "20"))
        self.assertEqual(0, rc, err)
        (rec,) = self.records("readall")
        self.assertEqual(rec["output"], f"ANSWER {rec['prompt_sha256']}")

    def test_run_one_spawn_error_and_bounds(self):
        out = er.run_one([str(self.tmp / "missing-binary")], "p", False, 5)
        self.assertEqual((out["status"], out["error"].split(":")[0]), ("spawn_error", "FileNotFoundError"))
        for timeout, cap in ((0, 10), (-1, 10), (float("inf"), 10), (float("nan"), 10), (3601, 10),
                             (5, 0), (5, 16 * 1024 * 1024 + 1)):
            with self.subTest(timeout=timeout, cap=cap), self.assertRaises(ValueError):
                er.run_one([sys.executable, "-c", "pass"], "p", False, timeout, cap)



@unittest.skipUnless(POSIX and er._HAS_KQUEUE and er._HAS_WAITID, "kqueue fallback already the default path")
class ProcessOutcomesKqueueFallback(ProcessOutcomes):
    """Same process tests with waitid disabled, exercising the kqueue NOTE_EXIT path."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(er, "_HAS_WAITID", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_watch_on_already_exited_leader_reports_exit_immediately(self):
        import subprocess
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        self.addCleanup(p.wait)
        while not os.waitid(os.P_PID, p.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT):
            time.sleep(0.01)  # zombie, not reaped
        watch = er._ExitWatch(p.pid)
        self.addCleanup(watch.close)
        self.assertTrue(watch.wait(time.monotonic()))


if __name__ == "__main__":
    unittest.main()
