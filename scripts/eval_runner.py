#!/usr/bin/env python3
"""Replay every skill's behaviour scenarios through ANY model/runtime and record the results.

The runner does not know about models. You give it a command that takes a prompt on
stdin (or as the final argument) and prints the agent's answer; it feeds each scenario
and stores the outputs. Grading is a separate, explicit step: a human or a grader
model reads the JSONL and marks each expected_behavior as met/unmet. A zero exit
code is a process outcome, never a behavioural pass; every record is written
with grading.status "ungraded".

    scripts/eval_runner.py --runner "kiro-cli chat --no-interactive --trust-tools=" \\
        --runner-id "kiro-cli 1.x, Bedrock default model" --label kiro-2026-09
    scripts/eval_runner.py --runner "claude -p" --runner-id "claude-code x.y" --label claude-code-x.y
    scripts/eval_runner.py --runner "some-cli --stdin" --stdin --runner-id "some-cli 3.1" --label whatever

--runner-id is the identity and version YOU state for the runner. The script never
runs `<runner> --version` or any other probe; the only thing it executes is the
runner itself, once per scenario.

Output: evals/runs/<label>.jsonl (mode 0600, created exclusively, never overwritten,
symlinked evals/ or evals/runs/ refused), one record per scenario:
    {record_version, label, runner_id, runner_argv_sha256, prompt_transport, dry_run,
     skill, index, mode, query, expected_behavior,
     skill_sha256, scenarios_file_sha256, scenario_sha256, prompt_sha256,
     process: {status, exit_code, signal, seconds, stdout_bytes, stderr_bytes,
               output_truncated, error},
     output, stderr,
     grading: {status: "ungraded", grader: null, expected_behavior_met: null}}

process.status is one of: not_run (dry run), exited, timeout, output_limit, spawn_error.
Records hold the scenario query and expected_behavior verbatim, in dry runs too, and,
for real runs, the runner's stdout/stderr (capped). Treat run files as possibly sensitive.
The runner argv is stored only as a hash, because it may contain tokens.

Process safety (real runs, POSIX only): each scenario runs in its own session and
process group. On timeout or when stdout+stderr together exceed --max-output-bytes,
the whole group gets SIGTERM, then SIGKILL after a short grace. The group also gets
SIGKILL after a normal exit, so background descendants are cleaned up. The leader is
reaped only after that, so its process-group id cannot be reused in between.
Descendants that create their own session (setsid, daemonising) leave the group and
cannot be tracked; the run still finishes on the deadline because the script stops
reading and closes its pipe ends. If the platform lacks the primitives this needs,
real runs fail before any artifact is created. Leader exit is detected with
waitid(WNOWAIT), or kqueue NOTE_EXIT where waitid is missing (macOS before Python 3.13).

Then record the graded result in docs/MODEL_COMPATIBILITY.md. Standard library only.
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import math
import os
import re
import select
import selectors
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
RUNS = ROOT / "evals" / "runs"

RECORD_VERSION = 2
SCENARIO_MODES = {"static", "integration"}
LABEL_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z", re.ASCII)
RUNNER_ID_MAX = 200
TIMEOUT_MAX = 3600
DEFAULT_TIMEOUT = 300
MAX_OUTPUT_LIMIT = 16 * 1024 * 1024
DEFAULT_MAX_OUTPUT = 1024 * 1024
READ_CHUNK = 64 * 1024
TERM_GRACE_SECONDS = 2.0
POLL_SECONDS = 0.02


class UsageError(Exception):
    """Invalid input; nothing has been written."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_prompt(skill_dir: Path, query: str) -> str:
    return prompt_from(skill_dir.joinpath("SKILL.md").read_text(encoding="utf-8"), query)


def prompt_from(skill_md: str, query: str) -> str:
    return (
        "You have the following skill loaded. Follow it exactly.\n\n"
        f"{skill_md}\n\n---\nUser: {query}\n"
    )


_HAS_WAITID = all(hasattr(os, n) for n in ("waitid", "WNOWAIT", "P_PID", "WNOHANG"))
_HAS_KQUEUE = hasattr(select, "kqueue") and hasattr(select, "KQ_NOTE_EXIT")


def process_group_cleanup_supported() -> bool:
    return (os.name == "posix" and hasattr(os, "killpg") and hasattr(os, "set_blocking")
            and (_HAS_WAITID or _HAS_KQUEUE))


def _bounded_int(lo: int, hi: int):
    def parse(text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"expected an integer, got {text!r}") from None
        if not lo <= value <= hi:
            raise argparse.ArgumentTypeError(f"must be between {lo} and {hi}, got {value}")
        return value
    return parse


def _outcome(status: str, *, exit_code=None, sig=None, seconds=0.0, stdout=b"", stderr=b"",
             truncated=False, error=None) -> dict:
    return {"status": status, "exit_code": exit_code, "signal": sig, "seconds": round(seconds, 1),
            "stdout_bytes": len(stdout), "stderr_bytes": len(stderr), "output_truncated": truncated,
            "error": error, "_stdout": stdout, "_stderr": stderr}


class _ExitWatch:
    """Detects the leader's exit WITHOUT reaping it, so the zombie keeps its pgid reserved.

    waitid(WNOWAIT) where available (Linux; macOS from Python 3.13), else kqueue
    NOTE_EXIT (BSD/macOS on older Pythons), which is event-driven.
    """

    def __init__(self, pid: int):
        self.pid, self.exited, self.kq = pid, False, None
        if not _HAS_WAITID:
            self.kq = select.kqueue()
            ev = select.kevent(pid, select.KQ_FILTER_PROC, select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                               select.KQ_NOTE_EXIT)
            try:
                self.kq.control([ev], 0, 0)
            except ProcessLookupError:  # unreaped child gone from the proc filter: already a zombie
                self.exited = True

    def wait(self, until: float) -> bool:
        while not self.exited:
            remaining = until - time.monotonic()
            if self.kq is not None:
                self.exited = bool(self.kq.control(None, 1, max(0.0, remaining)))
            else:
                try:
                    self.exited = os.waitid(os.P_PID, self.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
                except ChildProcessError:
                    self.exited = True
                if not self.exited and remaining > 0:
                    time.sleep(min(POLL_SECONDS, remaining))
            if remaining <= 0:
                break
        return self.exited

    def close(self) -> None:
        if self.kq is not None:
            self.kq.close()


def _signal_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        # ESRCH: group gone. EPERM: macOS reports it for a group of zombies only.
        pass


def _terminate_group(proc: subprocess.Popen, watch: _ExitWatch) -> None:
    _signal_group(proc.pid, signal.SIGTERM)
    watch.wait(time.monotonic() + TERM_GRACE_SECONDS)
    _signal_group(proc.pid, signal.SIGKILL)


def run_one(runner: list[str], prompt: str, use_stdin: bool, timeout: float,
            max_output: int = DEFAULT_MAX_OUTPUT) -> dict:
    """Run the runner once with bounded time and memory and return the process outcome."""
    if not process_group_cleanup_supported():
        raise RuntimeError("unsupported platform: process-group cleanup cannot be guaranteed")
    if not runner:
        raise ValueError("empty runner")
    if not (isinstance(timeout, (int, float)) and math.isfinite(timeout) and 0 < timeout <= TIMEOUT_MAX):
        raise ValueError(f"timeout must be finite, > 0 and <= {TIMEOUT_MAX}")
    if not (isinstance(max_output, int) and 0 < max_output <= MAX_OUTPUT_LIMIT):
        raise ValueError(f"max_output must be an int in 1..{MAX_OUTPUT_LIMIT}")
    payload = prompt.encode("utf-8")
    cmd = runner if use_stdin else [*runner, prompt]
    t0 = time.monotonic()
    deadline = t0 + timeout
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE if use_stdin else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True, close_fds=True)
    except OSError as e:
        return _outcome("spawn_error", seconds=time.monotonic() - t0,
                        error=f"{type(e).__name__}: {e.strerror or e}")

    bufs = {"stdout": bytearray(), "stderr": bytearray()}
    total = 0
    status = None
    reaped = False
    watch = None
    try:
        watch = _ExitWatch(proc.pid)
        with selectors.DefaultSelector() as sel:
            for name in ("stdout", "stderr"):
                fd = getattr(proc, name).fileno()
                os.set_blocking(fd, False)
                sel.register(fd, selectors.EVENT_READ, name)
            sent = 0
            if use_stdin:
                if payload:
                    os.set_blocking(proc.stdin.fileno(), False)
                    sel.register(proc.stdin.fileno(), selectors.EVENT_WRITE, "stdin")
                else:
                    proc.stdin.close()
            while sel.get_map() and status is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    status = "timeout"
                    break
                for key, _ in sel.select(remaining):
                    if key.data == "stdin":
                        try:
                            sent += os.write(key.fd, payload[sent:sent + READ_CHUNK])
                        except BlockingIOError:
                            continue
                        except OSError:  # EPIPE: runner closed stdin early; that is its choice
                            sent = len(payload)
                        if sent >= len(payload):
                            sel.unregister(key.fd)
                            proc.stdin.close()
                        continue
                    try:
                        chunk = os.read(key.fd, min(READ_CHUNK, max_output - total + 1))
                    except BlockingIOError:
                        continue
                    if not chunk:
                        sel.unregister(key.fd)
                        continue
                    keep = chunk[:max_output - total]
                    bufs[key.data] += keep
                    total += len(keep)
                    if len(keep) < len(chunk):
                        status = "output_limit"
                        break
        if status is None and not watch.wait(deadline):
            status = "timeout"
        if status is None:
            _signal_group(proc.pid, signal.SIGKILL)  # leftover descendants of a finished run
            status = "exited"
        else:
            _terminate_group(proc, watch)
        proc.wait()
        reaped = True
    finally:
        if not reaped:  # interrupted (e.g. KeyboardInterrupt): never leave the group running
            _signal_group(proc.pid, signal.SIGKILL)
            proc.wait()
        if watch is not None:
            watch.close()
        for f in (proc.stdin, proc.stdout, proc.stderr):
            if f is not None:
                try:
                    f.close()
                except OSError:
                    pass
    rc = proc.returncode
    exit_code, sig = (None, -rc) if rc is not None and rc < 0 else (rc, None)
    if status != "exited":
        exit_code = None
    return _outcome(status, exit_code=exit_code, sig=sig, seconds=time.monotonic() - t0,
                    stdout=bytes(bufs["stdout"]), stderr=bytes(bufs["stderr"]),
                    truncated=status == "output_limit")


def list_skills(skills_root: Path) -> list[Path]:
    if not skills_root.is_dir():
        raise UsageError(f"skills directory not found: {skills_root}")
    return sorted(p for p in skills_root.iterdir() if p.is_dir() and not p.name.startswith("_"))


def collect_scenarios(skills_root: Path, selected: list[str] | None, mode: str) -> list[dict]:
    skills = list_skills(skills_root)
    known = {p.name for p in skills}
    if selected is not None:
        unknown = sorted(set(selected) - known)
        if unknown:
            raise UsageError(f"unknown --skill {', '.join(map(repr, unknown))}; known: {', '.join(sorted(known))}")
        skills = [p for p in skills if p.name in set(selected)]
    items = []
    for skill_dir in skills:
        try:
            skill_bytes = (skill_dir / "SKILL.md").read_bytes()
            scen_bytes = (skill_dir / "evals" / "scenarios.json").read_bytes()
            skill_md = skill_bytes.decode("utf-8")
            payload = json.loads(scen_bytes.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            raise UsageError(f"{skill_dir.name}: cannot read SKILL.md / evals/scenarios.json: {e}") from None
        scenarios = payload.get("scenarios") if isinstance(payload, dict) else None
        if not isinstance(scenarios, list):
            raise UsageError(f"{skill_dir.name}: evals/scenarios.json has no 'scenarios' list")
        for i, sc in enumerate(scenarios):
            expected = sc.get("expected_behavior") if isinstance(sc, dict) else None
            if not (isinstance(sc, dict) and isinstance(sc.get("query"), str) and sc["query"].strip()
                    and sc.get("mode") in SCENARIO_MODES and isinstance(expected, list) and expected
                    and all(isinstance(e, str) and e.strip() for e in expected)):
                raise UsageError(f"{skill_dir.name}[{i}]: scenario needs a non-empty query, mode in "
                                 f"{sorted(SCENARIO_MODES)} and non-empty expected_behavior strings")
            if mode != "all" and sc["mode"] != mode:
                continue
            prompt = prompt_from(skill_md, sc["query"])  # same bytes as skill_sha256
            items.append({
                "skill": skill_dir.name, "index": i, "mode": sc["mode"], "query": sc["query"],
                "expected_behavior": sc["expected_behavior"],
                "skill_sha256": sha256_bytes(skill_bytes),
                "scenarios_file_sha256": sha256_bytes(scen_bytes),
                "scenario_sha256": sha256_bytes(json.dumps(sc, sort_keys=True, ensure_ascii=False,
                                                           separators=(",", ":")).encode("utf-8")),
                "prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
                "_prompt": prompt,
            })
    if not items:
        raise UsageError(f"no scenarios selected (mode={mode}, skills={selected or 'all'})")
    return items


def open_run_file(repo_root: Path, label: str) -> int:
    """Create evals/runs/<label>.jsonl exclusively, refusing symlinked directories or files."""
    flags_dir = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(repo_root, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0))
    try:
        for i, part in enumerate(("evals", "runs")):
            rel = "/".join(("evals", "runs")[:i + 1])
            try:
                os.mkdir(part, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            try:
                nfd = os.open(part, flags_dir, dir_fd=fd)
            except OSError as e:
                if e.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise UsageError(f"refusing {rel}: it is a symlink or not a directory") from None
                raise
            os.close(fd)
            fd = nfd
            if not stat.S_ISDIR(os.fstat(fd).st_mode):
                raise UsageError(f"refusing {rel}: not a directory")
        name = f"{label}.jsonl"
        try:
            return os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                           | getattr(os, "O_CLOEXEC", 0), 0o600, dir_fd=fd)
        except FileExistsError:
            raise UsageError(f"refusing to overwrite evals/runs/{name} (or a symlink there); "
                             f"choose a new --label") from None
    finally:
        os.close(fd)


def _write_records(out_fd: int, items: list[dict], runner: list[str], common: dict,
                   args: argparse.Namespace, statuses: dict[str, int]) -> None:
    with os.fdopen(out_fd, "w", encoding="utf-8") as out:
        for item in items:
            prompt = item.pop("_prompt")
            if args.dry_run:
                proc = _outcome("not_run")
            else:
                proc = run_one(runner, prompt, args.stdin, args.timeout, args.max_output_bytes)
                print(f"{item['skill']}[{item['index']}] {proc['status']} exit={proc['exit_code']} "
                      f"{proc['seconds']:.0f}s", file=sys.stderr)
            stdout, stderr = proc.pop("_stdout"), proc.pop("_stderr")
            rec = {**common, **item, "process": proc,
                   "output": None if args.dry_run else stdout.decode("utf-8", "replace").strip(),
                   "stderr": None if args.dry_run else stderr.decode("utf-8", "replace").strip(),
                   "grading": {"status": "ungraded", "grader": None, "expected_behavior_met": None}}
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            statuses[proc["status"]] = statuses.get(proc["status"], 0) + 1


def main(argv: list[str] | None = None, *, repo_root: Path | None = None) -> int:
    root = repo_root or ROOT
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runner", required=True, help="command that answers a prompt (quoted string)")
    ap.add_argument("--runner-id", required=True,
                    help="runner identity and version as you state it, e.g. 'claude-code 2.3.1, model X'; "
                         "recorded verbatim, never probed")
    ap.add_argument("--label", required=True,
                    help="run file basename, e.g. claude-code-2.3 ([A-Za-z0-9][A-Za-z0-9._-]{0,99})")
    ap.add_argument("--stdin", action="store_true", help="pass the prompt on stdin instead of as the last argument")
    ap.add_argument("--mode", choices=["static", "integration", "all"], default="static",
                    help="which scenarios to replay (integration ones need the skill's tools installed)")
    ap.add_argument("--skill", action="append", help="limit to these skills (repeatable)")
    ap.add_argument("--timeout", type=_bounded_int(1, TIMEOUT_MAX), default=DEFAULT_TIMEOUT,
                    help=f"seconds per scenario, 1..{TIMEOUT_MAX} (default {DEFAULT_TIMEOUT})")
    ap.add_argument("--max-output-bytes", type=_bounded_int(1, MAX_OUTPUT_LIMIT), default=DEFAULT_MAX_OUTPUT,
                    help=f"cap on stdout+stderr bytes kept per scenario (default {DEFAULT_MAX_OUTPUT})")
    ap.add_argument("--dry-run", action="store_true",
                    help="do not call the runner; still writes each record's query, expected_behavior, "
                         "hashes and runner identity (no runner output)")
    args = ap.parse_args(argv)

    try:
        try:
            runner = shlex.split(args.runner)
        except ValueError as e:
            raise UsageError(f"--runner cannot be parsed: {e}") from None
        if not runner:
            raise UsageError("--runner is empty")
        runner_id = args.runner_id.strip()
        if not runner_id or len(runner_id) > RUNNER_ID_MAX or not runner_id.isprintable():
            raise UsageError(f"--runner-id must be 1..{RUNNER_ID_MAX} printable characters")
        if not LABEL_RE.match(args.label):
            raise UsageError("--label must match [A-Za-z0-9][A-Za-z0-9._-]{0,99} (a plain basename)")
        items = collect_scenarios(root / "skills", args.skill, args.mode)
        if not args.dry_run:
            if not process_group_cleanup_supported():
                raise UsageError("unsupported platform: real runs need POSIX process groups and "
                                 "waitid(WNOWAIT) or kqueue to guarantee cleanup; use --dry-run")
            if shutil.which(runner[0]) is None:
                raise UsageError(f"runner executable not found: {runner[0]!r}")
        out_fd = open_run_file(root, args.label)
    except UsageError as e:
        print(f"eval_runner: error: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"eval_runner: error: cannot create evals/runs/{args.label}.jsonl: "
              f"{type(e).__name__}: {e.strerror or e}", file=sys.stderr)
        return 2

    common = {"record_version": RECORD_VERSION, "label": args.label, "runner_id": runner_id,
              "runner_argv_sha256": sha256_bytes(json.dumps(runner).encode("utf-8")),
              "prompt_transport": "stdin" if args.stdin else "argv", "dry_run": args.dry_run}
    statuses: dict[str, int] = {}
    try:
        _write_records(out_fd, items, runner, common, args, statuses)
    except OSError as e:
        print(f"eval_runner: error: writing evals/runs/{args.label}.jsonl failed after "
              f"{sum(statuses.values())} records (file kept, partial): {type(e).__name__}: {e.strerror or e}",
              file=sys.stderr)
        return 1
    summary = ", ".join(f"{k}={v}" for k, v in sorted(statuses.items()))
    print(f"wrote evals/runs/{args.label}.jsonl ({len(items)} scenarios; process: {summary}). Process status is "
          f"not a grade: grade each expected_behavior, then record the result in docs/MODEL_COMPATIBILITY.md",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
