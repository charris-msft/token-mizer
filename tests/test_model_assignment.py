import importlib.util
import json
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
            "role": "builder", "family": "flash", "provider": "GitHub",
            "runtime_id": POLICY.FLASH_MODEL, "available": True, "authorized": True,
            "context_capacity": 100, "route_evidence": {"source": "host", "verified": True, "provider": "GitHub", "family": "flash", "runtime_id": POLICY.FLASH_MODEL},
        }
        self.luna = {
            "role": "reviewer", "family": "luna", "provider": "Foundry",
            "runtime_id": "synthetic-connection/" + POLICY.LUNA_MODEL, "available": True, "authorized": True,
            "context_capacity": 100, "route_evidence": {"source": "local", "verified": True, "provider": "Foundry", "family": "luna", "runtime_id": "synthetic-connection/" + POLICY.LUNA_MODEL},
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
            "role": "validator", "family": "sol",
            "provider": "Foundry",
            "runtime_id": "synthetic-connection/" + POLICY.SOL_MODEL,
            "available": True,
            "authorized": True,
            "context_capacity": 100,
            "route_evidence": {"source": "local", "verified": True, "provider": "Foundry", "family": "sol", "runtime_id": "synthetic-connection/" + POLICY.SOL_MODEL},
        }
        with self.assertRaises(POLICY.AssignmentBlocked):
            self.choose("sol-auto", candidates=[sol])
        selected = self.choose("sol-explicit", candidates=[sol], explicit_model="synthetic-connection/" + POLICY.SOL_MODEL)
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
            cumulative=True,
            evidence=["ci:run-1"],
        )
        self.assertEqual("blocked", observed["outcome"])
        self.assertEqual(POLICY.FLASH_MODEL, observed["actual_model"])
        self.assertEqual(selected["selected_model"], observed["selected_model"])
        persisted = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(2, len(persisted["events"]))

    def test_reuse_revalidates_current_admission_and_preserves_history(self):
        self.choose("reuse-1")
        with self.assertRaisesRegex(POLICY.AssignmentBlocked, "historical assignment preserved"):
            self.choose("reuse-1", candidates=[dict(self.flash, authorized=False), self.luna])
        persisted = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertIn("reuse-1", persisted["assignments"])
        self.assertEqual("reuse-blocked", persisted["events"][-1]["type"])

    def test_routes_are_validated_and_large_context_excludes_foundry(self):
        spoofed = dict(self.luna, runtime_id=POLICY.FLASH_MODEL)
        with self.assertRaises(POLICY.AssignmentBlocked):
            self.choose("spoofed", candidates=[spoofed])
        with self.assertRaises(POLICY.AssignmentBlocked):
            self.choose("large-foundry", candidates=[self.luna], large_context=True)

    def test_concurrent_cli_selection_is_identity_stable(self):
        import subprocess, sys
        script = ROOT / "scripts" / "model_assignment.py"
        request = {"assignment_id": "parallel-1", "task_id": "task-1", "task_class": "code-change", "acceptance_boundary": "ci-passed", "required_context": 50, "candidates": [self.flash, self.luna]}
        processes = [subprocess.Popen([sys.executable, str(script), "--state", str(self.state), "select"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        outputs = [process.communicate(json.dumps(request), timeout=30) for process in processes]
        self.assertTrue(all(process.returncode == 0 for process in processes), outputs)
        self.assertEqual(json.loads(outputs[0][0])["selected_model"], json.loads(outputs[1][0])["selected_model"])

    def test_cli_help_and_json_contract(self):
        import subprocess, sys
        script = ROOT / "scripts" / "model_assignment.py"
        help_result = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True, check=False)
        self.assertEqual(0, help_result.returncode)
        self.assertIn("select", help_result.stdout)
        request = {"assignment_id": "cli-1", "task_id": "task-1", "task_class": "code-change", "acceptance_boundary": "ci-passed", "required_context": 50, "candidates": [self.luna]}
        result = subprocess.run([sys.executable, str(script), "--state", str(self.state), "select"], input=json.dumps(request), capture_output=True, text=True, check=False)
        self.assertEqual(0, result.returncode)
        self.assertEqual("cli-1", json.loads(result.stdout)["assignment_id"])


    def test_large_context_excludes_foundry_on_direct_coordinator_and_astra_paths(self):
        for path in ("direct", "coordinator", "astra"):
            with self.assertRaises(POLICY.AssignmentBlocked):
                self.choose("large-" + path, candidates=[self.luna], large_context=True, path=path)

    def test_resume_revalidates_current_admission_and_identity(self):
        self.choose("resume-1")
        with self.assertRaises(POLICY.AssignmentBlocked):
            self.choose("resume-1", candidates=[dict(self.flash, available=False), self.luna])
        with self.assertRaises(POLICY.AssignmentBlocked):
            POLICY.select_assignment(self.state, assignment_id="resume-1", task_id="task-1", task_class="code-change", acceptance_boundary="ci-passed", required_context=60, candidates=[self.flash, self.luna])

    def test_runtime_identity_provider_family_matrix(self):
        self.assertEqual(POLICY.FLASH_MODEL, self.choose("github-bare", candidates=[self.flash])["selected_runtime_id"])
        self.assertTrue(self.choose("foundry-qualified", candidates=[self.luna])["selected_runtime_id"].endswith(POLICY.LUNA_MODEL))
        with self.assertRaises(POLICY.AssignmentBlocked):
            self.choose("foundry-bare", candidates=[dict(self.luna, runtime_id=POLICY.LUNA_MODEL)])
        with self.assertRaises(POLICY.AssignmentBlocked):
            self.choose("spoof-provider", candidates=[dict(self.flash, provider="Foundry")])

    def test_explicit_runtime_conflict_and_duplicate_roles(self):
        with self.assertRaises(POLICY.AssignmentBlocked):
            self.choose("duplicate", candidates=[self.flash, dict(self.luna, role="builder")])
        selected = self.choose("explicit-runtime", candidates=[self.luna], explicit_model=self.luna["runtime_id"])
        with self.assertRaises(POLICY.AssignmentBlocked):
            self.choose("explicit-runtime", candidates=[self.luna], explicit_model=POLICY.FLASH_MODEL)
        self.assertTrue(selected["selected_runtime_id"].endswith(POLICY.LUNA_MODEL))

    def test_cumulative_counter_and_idempotent_replay_contract(self):
        selected = self.choose("measure-1")
        first = POLICY.record_outcome(self.state, selected["assignment_id"], outcome="verified", attempts=1, reassignments=0, cumulative=True, event_id="measure-event", evidence=["ci:synthetic"], target_revision="sha", target_environment="synthetic")
        replay = POLICY.record_outcome(self.state, selected["assignment_id"], outcome="verified", attempts=1, reassignments=0, cumulative=True, event_id="measure-event", evidence=["ci:synthetic"], target_revision="sha", target_environment="synthetic")
        self.assertEqual(first["attempts"], replay["attempts"])
        with self.assertRaises(POLICY.AssignmentBlocked):
            POLICY.record_outcome(self.state, selected["assignment_id"], outcome="blocked", attempts=1, reassignments=0, cumulative=True, event_id="measure-event")

if __name__ == "__main__":
    unittest.main()
