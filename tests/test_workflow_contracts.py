"""Source contracts only; tests never dispatch or execute hosted workflow steps."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WorkflowContracts(unittest.TestCase):
    def test_evidence_check_is_not_deferred_until_after_merge(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        step = workflow.split("- name: EVIDENCE.json must match validated inputs", 1)[1]
        step = step.split("\n  gate-smoke:", 1)[0]
        self.assertIn("if: matrix.python-version == '3.12'", step)
        self.assertNotIn("github.ref", step)
        self.assertIn("python3 scripts/evidence.py --check", step)

    def test_smoke_version_is_pinned_and_refs_are_not_shell_expressions(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertIn("cqa-analyzer==3.4.1", workflow)
        self.assertEqual(workflow.count(' --base "$BASE_REF"'), 2)
        self.assertNotIn('--base "${{', workflow)
        self.assertIn('"$RUNNER_TEMP/cqa-manifest.json"', workflow)
        self.assertIn('"$RUNNER_TEMP/cqa-report.json"', workflow)


if __name__ == "__main__":
    unittest.main()
