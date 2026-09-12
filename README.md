# agent-skills

Three [Agent Skills](https://agentskills.io) for coding agents (Claude Code,
Kiro, Codex, Cursor, and anything else that loads `SKILL.md`). Each one is
backed by a runnable tool or a tested behaviour contract — no skill here is
just a prompt with a nice name.

## Install

```bash
npx skills add AmitSinghOM/agent-skills            # skills.sh, all agents in this project
npx skills add AmitSinghOM/agent-skills --global   # for every project
```

Claude Code plugin marketplace:

```
/plugin marketplace add AmitSinghOM/agent-skills
/plugin install agent-skills@amitsinghom-agent-skills
```

Manual (Kiro CLI, Kiro Crew, or any client): copy the skill directory you want
into your agent's skills path, e.g. `~/.kiro/skills/` or `.kiro/skills/`.

```bash
git clone https://github.com/AmitSinghOM/agent-skills
cp -r agent-skills/skills/code-quality-gate ~/.kiro/skills/
```

## Skills

| Skill | What it does | Backed by |
|---|---|---|
| [`code-quality-gate`](skills/code-quality-gate/SKILL.md) | Deterministic pass/fail gate that fails only on **new** findings on the lines you **changed**. Generates the changed-lines manifest from git that the analyzer deliberately does not, drives the correct baseline/SARIF flags, and refuses to fabricate results if the analyzer is missing. | [`cqa-analyzer`](https://pypi.org/project/cqa-analyzer/) — privacy-first offline static analysis for Python, Go, TypeScript/JS, Java, Kotlin, C#, C/C++; 400 tests, published with attestations |
| [`learning-accelerator`](skills/learning-accelerator/SKILL.md) | Turns "teach me X" into a loop: 80/20 plan, exit-test-gated ladder, free-first verified resources, one-page cheat sheet, one-at-a-time escalating quiz, Feynman teach-back. Never fabricates resources. | Daily use in interview preparation; five behaviour scenarios |
| [`source-grounded-claims`](skills/source-grounded-claims/SKILL.md) | Every substantive claim gets a calibrated confidence percentage (80% = act on it) and every fact gets a source that was actually opened. Unreachable source → "unverified", never a guess. | Four behaviour scenarios; the standing rule behind every number in this README |

### code-quality-gate in 30 seconds

```bash
pipx install cqa-analyzer
skills/code-quality-gate/scripts/run-gate.sh --write-baseline .code-quality-baseline.json   # once, legacy repo
skills/code-quality-gate/scripts/run-gate.sh --base origin/main --baseline .code-quality-baseline.json --format sarif > results.sarif
echo $?   # 0 clean · 4 new finding on a changed line · 3 misconfigured
```

## How these are tested

- `python3 scripts/validate.py` checks every skill against the
  [spec](https://agentskills.io/specification): `name` matches its directory and
  the character rules, `description` ≤ 1024 chars, no non-spec frontmatter keys,
  `metadata.version` is semver, body under 500 lines, and each `evals/scenarios.json`
  has ≥ 3 well-formed scenarios including a tool-free one.
- `python3 -m unittest discover -s tests` runs the validator's own negative tests,
  the changed-lines manifest generator (new-side-only hunks, adjacent-range
  merging, deleted/binary files excluded), and checks that the plugin manifests
  and this README list exactly the skills that exist.
- CI runs both on every pull request across Python 3.10–3.13.
- `code-quality-gate`'s wrapper was verified end-to-end against the real analyzer:
  baseline write, gate pass (exit 0, valid SARIF 2.1.0), gate fail (exit 4),
  report-only (exit 0 with warning), staged mode, and all configuration errors.

## Layout

```
skills/<name>/SKILL.md          spec frontmatter + instructions
skills/<name>/evals/            behaviour scenarios (static | integration)
skills/<name>/scripts/          runnable code (stdlib only)
skills/<name>/references/       detail loaded on demand
.claude-plugin/                 plugin.json + marketplace.json
scripts/validate.py             spec + contract validator
tests/                          unittest suite
```

## Related

- [code-quality-analyzer](https://github.com/AmitSinghOM/code-quality-analyzer) — the analyzer behind `code-quality-gate`
- [Portfolio](https://amitsinghom.github.io/)

MIT © Amit Singh
