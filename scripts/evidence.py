#!/usr/bin/env python3
"""Collect local validation evidence, or --check it against the current input tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_VERSION = "1.1.0"


def sh(*cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=120)


def git_output(*args: str) -> str:
    result = sh("git", *args)
    if result.returncode:
        raise ValueError(f"git {args[0]} failed; evidence requires a readable repository")
    return result.stdout


def input_digest() -> dict:
    names = git_output("ls-files", "--cached", "--others", "--exclude-standard", "-z")
    records = []
    for name in sorted(set(names.split("\0")) - {""}):
        relative = Path(name)
        if (
            name == "EVIDENCE.json"
            or name.startswith("evals/runs/")
            or {"__pycache__", ".pytest_cache"}.intersection(relative.parts)
            or relative.suffix in {".pyc", ".pyo"}
        ):
            continue
        path = ROOT / relative
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsafe evidence input path")
        if any(p.is_symlink() for p in (path, *path.parents) if p != ROOT and ROOT in p.parents):
            raise ValueError(f"symlinked evidence input: {name}")
        if not path.exists():
            continue  # Tracked deletions are absent from the validated working tree.
        if not path.is_file():
            raise ValueError(f"non-file evidence input: {name}")
        records.append(
            [name, bool(path.stat().st_mode & 0o111), hashlib.sha256(path.read_bytes()).hexdigest()]
        )
    if not records:
        raise ValueError("no evidence inputs found")
    encoded = json.dumps(records, ensure_ascii=True, separators=(",", ":")).encode()
    return {"sha256": hashlib.sha256(encoded).hexdigest(), "file_count": len(records)}


def skill_versions() -> dict[str, str]:
    out = {}
    for d in sorted(
        p for p in (ROOT / "skills").iterdir() if p.is_dir() and not p.name.startswith("_")
    ):
        match = re.search(
            r'^\s+version:\s*"?([0-9.]+)"?', (d / "SKILL.md").read_text(encoding="utf-8"), re.M
        )
        out[d.name] = match.group(1) if match else "unknown"
    return out


def collect() -> dict:
    inputs = input_digest()
    validate = sh(sys.executable, "scripts/validate.py")
    tests = sh(sys.executable, "-m", "unittest", "discover", "-s", "tests")
    ran = re.search(r"^Ran (\d+) tests? in ", tests.stderr, re.M)
    count = int(ran.group(1)) if ran else 0
    commit = git_output("rev-parse", "HEAD").strip()
    dirty = bool(git_output("status", "--porcelain", "--", ":!EVIDENCE.json").strip())
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    versions = skill_versions()
    if input_digest() != inputs:
        raise ValueError("repository inputs changed during evidence collection")
    return {
        "evidence_version": EVIDENCE_VERSION,
        "inputs": inputs,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repository": "https://github.com/AmitSinghOM/agent-skills",
        "commit": commit,
        "working_tree_dirty": dirty,
        "release_version": plugin["version"],
        "skills": versions,
        "validator": {
            "exit_code": validate.returncode,
            "result": validate.stdout.strip().splitlines()[-1] if validate.stdout.strip() else "",
        },
        "tests": {
            "exit_code": tests.returncode,
            "count": count,
            "ok": tests.returncode == 0
            and count > 0
            and bool(re.search(r"^OK(?: \([^\n]+\))?\s*$", tests.stderr, re.M)),
        },
        "python": sys.version.split()[0],
        "regenerate": "python3 scripts/evidence.py",
    }


def is_green(evidence: dict) -> bool:
    validator, tests = evidence.get("validator"), evidence.get("tests")
    if not isinstance(validator, dict) or not isinstance(tests, dict):
        return False
    return (
        type(validator.get("exit_code")) is int
        and validator["exit_code"] == 0
        and type(tests.get("exit_code")) is int
        and tests["exit_code"] == 0
        and tests.get("ok") is True
        and type(tests.get("count")) is int
        and tests["count"] > 0
    )


def write_evidence(path: Path, evidence: dict) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("evidence output must be a regular file, not a symlink")
    if path.exists():
        previous = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(previous, dict) or "evidence_version" not in previous:
            raise ValueError("refusing to replace a file that is not evidence")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".evidence-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(evidence, indent=2) + "\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="rerun checks and compare input digest"
    )
    parser.add_argument("--output", default=str(ROOT / "EVIDENCE.json"))
    args = parser.parse_args(argv)
    path = Path(args.output)
    try:
        old = None
        if args.check:
            old = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(old, dict):
                raise ValueError("evidence must be a JSON object")
        evidence = collect()
        if not is_green(evidence):
            raise ValueError("validator or tests are not green now (positive test count required)")
        if args.check:
            problems = []
            if not is_green(old):
                problems.append("stored validation results are not green")
            for key in ("evidence_version", "inputs", "release_version", "skills"):
                if old.get(key) != evidence[key]:
                    problems.append(f"{key} changed")
            stored_tests = old.get("tests")
            if (
                not isinstance(stored_tests, dict)
                or stored_tests.get("count") != evidence["tests"]["count"]
            ):
                problems.append("test count changed; regenerate using the reference interpreter")
            if problems:
                raise ValueError("stale evidence: " + "; ".join(problems))
            print("EVIDENCE.json is current", file=sys.stderr)
        else:
            write_evidence(path, evidence)
            print(
                f"wrote {path}: release {evidence['release_version']}, "
                f"{evidence['tests']['count']} tests, validator exit 0",
                file=sys.stderr,
            )
        return 0
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"evidence failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
