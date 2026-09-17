import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("model_assignment", ROOT / "scripts" / "model_assignment.py")
POLICY = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(POLICY)


class ModelAssignmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name) / "assignments.json"
        self.flash = {
            "provider": "GitHub",
            "model": POLICY.FLASH_MODEL,
            "available": True,
            "authorized": True,
            "context_capacity": 100,
        }
        self.luna = {
            "provider": "Foundry",
            "model": POLICY.LUNA_MODEL,
            "available": True,
            "authorized": True,
            "context_capacity": 100,
        }

    def tearDown(self):
        self.temp.cleanup()

    def choose(self, assignment_id, candidates=None, **kwargs):
        return POLICY.select_assignment(
            self.state,
            assignment_id=assignment_id,
            task_id="task-1",
            task_class="code-change",
            acceptance_boundary="ci-passed",
            required_context=50,
            candidates=candidates or [self.flash, self.luna],
            **kwargs,
        )

    def test_context_fit_precedes_alternation_and_large_task_does_not_fallback_to_luna(self):
        denied_flash = dict(self.flash, authorized=False)
        with self.assertRaisesRegex(POLICY.AssignmentBlocked, "GitHub"):
            self.choose("large-1", candidates=[denied_flash, self.luna], large_context=True)

    def test_both_fit_alternate_and_resume_reuses_assignment(self):
        first = self.choose("a")
        second = self.choose("b")
        self.assertEqual(POLICY.FLASH_MODEL, first["selected_model"])
        self.assertEqual(POLICY.LUNA_MODEL, second["selected_model"])
        resumed = self.choose("a")
        self.assertEqual(first, resumed)
        self.assertEqual("alternating-context-fitting-pool", first["selection_reason"])

    def test_unknown_capacity_never_asserts_fit(self):
        unknown_flash = dict(self.flash, context_capacity="unknown")
        with self.assertRaisesRegex(POLICY.AssignmentBlocked, "confirmed sufficient"):
            self.choose("unknown-1", candidates=[unknown_flash])

    def test_zero_budget_or_unavailable_flash_uses_luna_only_when_context_fits(self):
        denied_flash = dict(self.flash, authorized=False)
        selected = self.choose("small-1", candidates=[denied_flash, self.luna])
        self.assertEqual(POLICY.LUNA_MODEL, selected["selected_model"])
        self.assertIn("unauthorized", selected["eligibility"][0]["reasons"])

    def test_sol_is_explicit_only(self):
        sol = {
            "provider": "Foundry",
            "model": POLICY.SOL_MODEL,
            "available": True,
            "authorized": True,
            "context_capacity": 100,
        }
        with self.assertRaises(POLICY.AssignmentBlocked):
            self.choose("sol-auto", candidates=[sol])
        selected = self.choose("sol-explicit", candidates=[sol], explicit_model=POLICY.SOL_MODEL)
        self.assertEqual(POLICY.SOL_MODEL, selected["selected_model"])
        self.assertEqual("explicit-user-model", selected["selection_reason"])

    def test_outcome_records_actual_route_without_reassigning(self):
        selected = self.choose("outcome-1")
        observed = POLICY.record_outcome(
            self.state,
            selected["assignment_id"],
            outcome="blocked",
            actual_provider="GitHub",
            actual_model=POLICY.FLASH_MODEL,
            attempts=1,
            reassignments=0,
        )
        self.assertEqual("blocked", observed["outcome"])
        self.assertEqual(POLICY.FLASH_MODEL, observed["actual_model"])
        self.assertEqual(selected["selected_model"], observed["selected_model"])


if __name__ == "__main__":
    unittest.main()
