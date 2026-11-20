#!/usr/bin/env python3
"""Validate every skill in skills/ against the Agent Skills spec and this repo's
eval contract. Standard library only; exits non-zero on any violation.

Spec: https://agentskills.io/specification
  - name: 1-64 chars, [a-z0-9-], no leading/trailing/double hyphen, == directory name
  - description: 1-1024 chars, non-empty
  - license (optional), compatibility (optional, <= 500 chars)
  - metadata (optional): map of string -> string
  - allowed-tools (optional): string
Repo contract:
  - evals/scenarios.json exists with >= 3 scenarios, each {skills, query, expected_behavior, mode}
  - mode in {static, integration}; at least one static scenario
  - SKILL.md body under 500 lines (spec recommendation, enforced here)
  - metadata.version present (semver-ish) so releases are traceable
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = ROOT / "skills"
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
KNOWN_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}


class Problem(Exception):
    pass


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Minimal YAML-subset parser: scalars, quoted strings, one nested map (metadata)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise Problem("SKILL.md must start with a '---' frontmatter line")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise Problem("frontmatter is not closed with '---'")
    fm: dict = {}
    current_map: str | None = None
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith(("  ", "\t")):
            if current_map is None:
                raise Problem(f"indented line outside a mapping: {raw!r}")
            k, _, v = raw.strip().partition(":")
            fm[current_map][k.strip()] = _scalar(v)
            continue
        k, sep, v = raw.partition(":")
        if not sep:
            raise Problem(f"malformed frontmatter line: {raw!r}")
        k = k.strip()
        if v.strip() == "":
            fm[k] = {}
            current_map = k
        else:
            fm[k] = _scalar(v)
            current_map = None
    return fm, "\n".join(lines[end + 1:])


def _scalar(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return v


def validate_skill(skill_dir: Path) -> list[str]:
    errors: list[str] = []
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return ["missing SKILL.md"]
    try:
        fm, body = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    except Problem as exc:
        return [str(exc)]

    unknown = set(fm) - KNOWN_KEYS
    if unknown:
        errors.append(f"non-spec frontmatter keys: {sorted(unknown)} (put extras under metadata)")

    name = fm.get("name")
    if not isinstance(name, str) or not name:
        errors.append("name is required")
    else:
        if not (1 <= len(name) <= 64):
            errors.append(f"name length {len(name)} not in 1..64")
        if not NAME_RE.match(name):
            errors.append(f"name {name!r} must be lowercase [a-z0-9-], no leading/trailing/double hyphen")
        if name != skill_dir.name:
            errors.append(f"name {name!r} != directory {skill_dir.name!r}")

    desc = fm.get("description")
    if not isinstance(desc, str) or not desc.strip():
        errors.append("description is required and non-empty")
    elif len(desc) > 1024:
        errors.append(f"description length {len(desc)} > 1024")

    compat = fm.get("compatibility")
    if compat is not None and not (1 <= len(compat) <= 500):
        errors.append("compatibility must be 1..500 chars when present")

    meta = fm.get("metadata")
    if meta is not None:
        if not isinstance(meta, dict) or not all(isinstance(v, str) for v in meta.values()):
            errors.append("metadata must be a map of string -> string")
        else:
            ver = meta.get("version")
            if not ver or not SEMVER_RE.match(ver):
                errors.append("metadata.version must be present and semver (x.y.z)")
    else:
        errors.append("metadata.version is required by this repo (traceable releases)")

    if body.count("\n") + 1 > 500:
        errors.append("SKILL.md body exceeds 500 lines; move detail to references/")

    errors.extend(validate_evals(skill_dir))
    return errors


def validate_evals(skill_dir: Path) -> list[str]:
    path = skill_dir / "evals" / "scenarios.json"
    if not path.is_file():
        return ["missing evals/scenarios.json"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"evals/scenarios.json invalid JSON: {exc}"]
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) < 3:
        return ["evals need >= 3 scenarios"]
    errors: list[str] = []
    modes: set[str] = set()
    for i, s in enumerate(scenarios):
        for key in ("skills", "query", "expected_behavior", "mode"):
            if not s.get(key):
                errors.append(f"scenario {i}: missing/empty {key}")
        if s.get("mode") not in {"static", "integration"}:
            errors.append(f"scenario {i}: mode must be static|integration")
        else:
            modes.add(s["mode"])
        if skill_dir.name not in (s.get("skills") or []):
            errors.append(f"scenario {i}: skills must include {skill_dir.name!r}")
    if "static" not in modes:
        errors.append("at least one static (tool-free) scenario is required")
    return errors


def main() -> int:
    if not SKILLS_DIR.is_dir():
        print("no skills/ directory", file=sys.stderr)
        return 1
    rc = 0
    dirs = sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir())
    for d in dirs:
        errs = validate_skill(d)
        if errs:
            rc = 1
            print(f"✗ {d.name}")
            for e in errs:
                print(f"    - {e}")
        else:
            print(f"✓ {d.name}")
    print("RESULT:", "all skills valid" if rc == 0 else "violations found")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
