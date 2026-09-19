"""Static consumer contracts plus synthetic canonical calls, never host-load proof."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
SPEC = importlib.util.spec_from_file_location("integrate_allocator", ROOT / "scripts" / "model_assignment.py")
ALLOCATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ALLOCATOR)

# These fixtures describe consumer composition, not an executable routing policy.
COMPOSITION = (
    ("eligible inherited coordinator", "direct", "context_fits"),
    ("canonical fresh admission", "handoff_ready", "current_admission_passed"),
    ("failed first fix", "escalation_required", "first_fix_failed"),
    ("skill absent or unloadable", "blocked", "dependency_missing"),
    ("task contract absent", "blocked", "input_missing"),
    ("context absent or too small", "blocked", "context_unknown_or_insufficient"),
    ("paid gate denied", "blocked", "authorization_denied"),
    ("runtime missing", "blocked", "runtime_unavailable"),
    ("changed acceptance target", "blocked", "identity_conflict"),
    ("consumer repair bound reached", "blocked", "consumer_limit_reached"),
)


class IntegrationContractTests(unittest.TestCase):
    def setUp(self):
        self.skill = (SKILLS / "token-mizer-integrate" / "SKILL.md").read_text(encoding="utf-8")

    def test_static_consumer_result_fixtures_are_documented_not_allocator_schema(self):
        self.assertEqual({"direct", "handoff_ready", "escalation_required", "blocked"}, {row[1] for row in COMPOSITION})
        for scenario, status, reason in COMPOSITION:
            with self.subTest(scenario=scenario):
                self.assertIn(f"| {status} | {reason} |", self.skill)
        for field in ("task_id", "task_class", "acceptance_boundary", "original_acceptance_target"):
            self.assertIn(f"`{field}`", self.skill)
        self.assertIn("not allocator CLI JSON", self.skill)
        self.assertIn("static fixtures and synthetic allocator calls do not prove host", self.skill)
        self.assertIn("cleared merged release", self.skill)

    def test_explicit_shared_activation_and_staged_canonical_dependencies(self):
        self.assertIn("Installation alone never activates routing", self.skill)
        self.assertIn("Discover actual host-listed skill names", self.skill)
        for name in ("token-mizer-route", "token-mizer-budget", "token-mizer-handoff"):
            canonical = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn(f"`{name}`", self.skill)
            self.assertIn("through `token-mizer-integrate`", canonical)
        self.assertIn("Load canonical handoff only before dependent delegation", self.skill)
        self.assertIn("Plugin installation is separate", self.skill)
        self.assertIn("must not claim missing policy was consumed or start dependent delegation", self.skill)
        self.assertIn("independently established current eligibility", self.skill)

    def test_private_operational_identity_is_separate_from_shared_diagnostics(self):
        self.assertIn("Shared diagnostics allow only `status`, `reason`, `actual_model_label`, `evidence_reference`", self.skill)
        self.assertIn("Never log private policy values, provider/connection IDs, exact runtime IDs", self.skill)
        self.assertIn("handoff.selected_runtime_id", self.skill)
        self.assertIn("consumer decides and verifies repair acceptance", self.skill)
        self.assertIn("No cached spawn authorization", self.skill)

    def test_synthetic_consumer_handoff_uses_canonical_allocator_without_policy_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            runtime = "synthetic-local/gpt-5.6-luna"
            candidate = {"role": "builder", "family": "luna", "provider": "Foundry",
                         "runtime_id": runtime, "available": True, "authorized": True,
                         "context_capacity": 100,
                         "route_evidence": {"source": "local", "verified": True, "runtime_id": runtime}}
            request = {"assignment_id": "synthetic-repair", "task_id": "repair-task", "task_class": "repair",
                       "acceptance_boundary": "deployed-and-healthy", "required_context": 50,
                       "candidates": [candidate]}
            selected = ALLOCATOR.select_assignment(state, **request)
            admitted = ALLOCATOR.admit_assignment(state, **request)
            self.assertNotIn("handoff", selected)
            self.assertEqual(runtime, admitted["handoff"]["selected_runtime_id"])
            self.assertEqual(request["acceptance_boundary"], admitted["acceptance_boundary"])
            self.assertEqual("unknown", admitted["actual_model"])
            self.assertEqual("unknown", admitted["outcome"])
            for changes in ({"large_context": True}, {"required_context": 101},
                            {"acceptance_boundary": "ci-passed"},
                            {"candidates": [{**candidate, "authorized": False}]}):
                with self.subTest(changes=changes), self.assertRaises(ALLOCATOR.AssignmentBlocked):
                    ALLOCATOR.admit_assignment(state, **{**request, **changes})
            self.assertNotIn("handoff", ALLOCATOR.export_state(state)["assignments"]["synthetic-repair"])


if __name__ == "__main__":
    unittest.main()
