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
            "role": "builder", "family": "luna", "provider": "Foundry",
            "runtime_id": "synthetic-connection/" + POLICY.LUNA_MODEL, "available": True, "authorized": True,
            "context_capacity": 100, "route_evidence": {"source": "local", "verified": True, "provider": "Foundry", "family": "luna", "runtime_id": "synthetic-connection/" + POLICY.LUNA_MODEL},
        }

    def tearDown(self):
        self.temp.cleanup()

    def choose(self, assignment_id, candidates=None, **kwargs):
        return POLICY.select_assignment(
            self.state,
            assignment_id=assignment_id,
            task_id="task-" + assignment_id,
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
        self.assertNotEqual(first["task_id"], second["task_id"])
        self.assertEqual(first["selected_role"], second["selected_role"])
        self.assertEqual("builder", second["selected_role"])
        self.assertEqual(self.luna["runtime_id"], second["selected_runtime_id"])
        self.assertEqual(self.flash["runtime_id"], first["selected_runtime_id"])
        third = self.choose("role-a", explicit_role="builder")
        fourth = self.choose("role-b", explicit_role="builder")
        self.assertEqual("flash", third["selected_family"])
        self.assertEqual("luna", fourth["selected_family"])

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
            event_id="observed-1",
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
        for process in processes:
            process.stdin.write(json.dumps(request)); process.stdin.close(); process.stdin = None
        outputs = [process.communicate(timeout=30) for process in processes]
        self.assertTrue(all(process.returncode == 0 for process in processes), outputs)
        self.assertEqual(json.loads(outputs[0][0])["selected_model"], json.loads(outputs[1][0])["selected_model"])
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(1, state["next_slot"])
        self.assertEqual(1, len(state["events"]))

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

    def test_explicit_runtime_conflict_and_duplicate_routes(self):
        with self.assertRaises(POLICY.AssignmentBlocked):
            self.choose("duplicate", candidates=[self.flash, dict(self.flash, authorized=False)])
        with self.assertRaises(POLICY.AssignmentBlocked):
            self.choose("mixed-roles", candidates=[self.flash, dict(self.luna, role="reviewer")])
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

    def request(self, aid="a", **updates):
        return {"assignment_id": aid, "task_id": "task-" + aid, "task_class": "code-change",
                "acceptance_boundary": "ci-passed", "required_context": 50,
                "candidates": [self.flash, self.luna], **updates}

    def test_cutoff_exports_only_historical_assignment_evidence(self):
        self.choose("history", now="2026-09-17T10:00:00Z")
        POLICY.admit_assignment(self.state, **self.request("history", now="2026-09-18T10:00:00Z"))
        POLICY.record_outcome(self.state, "history", outcome="verified", event_id="verified",
                              attempts=2, cumulative=True, evidence=["synthetic:tests"],
                              target_revision="synthetic-sha", target_environment="synthetic",
                              timestamp="2026-09-18T10:00:00.100Z")
        self.choose("future", now="2026-09-18T10:00:00.200Z")
        historical = POLICY.export_state(self.state, "2026-09-17T11:00:00Z")
        self.assertEqual(["allocated"], [e["type"] for e in historical["events"]])
        self.assertEqual({"history"}, set(historical["assignments"]))
        snapshot = historical["assignments"]["history"]
        self.assertEqual("unknown", snapshot["outcome"])
        self.assertEqual("unknown", snapshot["admission_status"])
        self.assertIsNone(snapshot["attempts"])
        self.assertEqual("unverified", snapshot["verification"])
        self.assertEqual([], snapshot["evidence"])
        self.assertEqual("unknown", POLICY.assignment_from_export(historical, "history")["outcome"])
        fraction = POLICY.export_state(self.state, "2026-09-18T12:00:00.100+02:00")
        self.assertEqual("unknown", fraction["assignments"]["history"]["outcome"])
        self.assertEqual(2, POLICY.export_state(self.state)["assignments"]["history"]["attempts"])
        persisted = json.loads(self.state.read_text())
        self.assertNotIn("outcome", persisted["assignments"]["history"])
        self.assertNotIn("admitted", persisted["assignments"]["history"])

    def test_rejected_writes_preserve_bytes_and_replay_is_immutable(self):
        self.choose("a", now="2026-09-17T10:00:00Z")
        record = dict(outcome="blocked", attempts=1, cumulative=True, event_id="r1", timestamp="2026-09-17T11:00:00Z")
        first = POLICY.record_outcome(self.state, "a", **record)
        POLICY.record_outcome(self.state, "a", **{**record, "attempts": 2, "event_id": "r2", "timestamp": "2026-09-17T12:00:00Z"})
        before = self.state.read_bytes()
        self.assertEqual(first, POLICY.record_outcome(self.state, "a", **record))
        invalid = [
            {"event_id": "backdated", "attempts": 3, "timestamp": "2026-09-17T09:00:00Z"},
            {"event_id": "decrease", "attempts": 1, "timestamp": "2026-09-17T13:00:00Z"},
            {"attempts": 2}, {"timestamp": "2026-09-17T11:00:00.001Z"},
            {"event_id": "bool", "attempts": True}, {"event_id": "negative", "reassignments": -1},
            {"event_id": "float", "attempts": 1.5}, {"event_id": "bad-evidence", "evidence": "not-a-list"},
            {"event_id": "bad-target", "target_revision": {}}, {"event_id": "bad-time", "timestamp": "not-a-time"},
            {"event_id": "bad-mode", "cumulative": 1},
        ]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises((ValueError, POLICY.AssignmentBlocked)):
                POLICY.record_outcome(self.state, "a", **{**record, **changes})
            self.assertEqual(before, self.state.read_bytes())
            self.assertEqual(2, POLICY.export_state(self.state)["assignments"]["a"]["attempts"])
        with self.assertRaises(ValueError): self.choose("backdated-allocation", now="2026-09-17T09:00:00Z")
        self.assertEqual(before, self.state.read_bytes())

    def test_fresh_admit_handoff_and_resume_constraints(self):
        request = self.request()
        selected = POLICY.select_assignment(self.state, **request)
        self.assertNotIn("handoff", selected)
        admitted = POLICY.admit_assignment(self.state, **request)
        self.assertEqual(self.flash["runtime_id"], admitted["handoff"]["selected_runtime_id"])
        with self.assertRaises(POLICY.AssignmentBlocked):
            POLICY.admit_assignment(self.state, **{**request, "candidates": [dict(self.flash, authorized=False), self.luna]})
        denied = POLICY.export_state(self.state)["assignments"]["a"]
        self.assertEqual("blocked", denied["admission_status"])
        self.assertNotIn("handoff", denied)
        self.assertNotIn("handoff", POLICY.select_assignment(self.state, **request))
        self.assertFalse(hasattr(POLICY, "spawn_handoff"))
        for changes in ({"explicit_model": self.luna["runtime_id"]}, {"explicit_provider": "Foundry"},
                        {"explicit_role": "reviewer"}, {"required_context": 101}, {"large_context": True}):
            for action in (POLICY.select_assignment, POLICY.admit_assignment):
                before = self.state.read_bytes()
                with self.subTest(changes=changes, action=action.__name__), self.assertRaises(POLICY.AssignmentBlocked):
                    action(self.state, **{**request, **changes})
                self.assertEqual(before, self.state.read_bytes())

    def test_unknown_outcome_evidence_and_qualified_github_identity(self):
        qualified = "synthetic-github/" + POLICY.FLASH_MODEL
        flash = {**self.flash, "runtime_id": qualified, "route_evidence": {**self.flash["route_evidence"], "runtime_id": qualified}}
        self.assertEqual(qualified, self.choose("qualified", candidates=[flash])["selected_runtime_id"])
        for i, updates in enumerate(({}, {"evidence": ["synthetic:test"]}, {"evidence": ["synthetic:test"], "target_revision": "unknown", "target_environment": "test"})):
            outcome = POLICY.record_outcome(self.state, "qualified", event_id=f"unknown-{i}", outcome="verified", cumulative=True, **updates)
            self.assertEqual("unverified", outcome["verification"])
            self.assertEqual("unknown", outcome["actual_runtime_id"])
            self.assertIsNone(outcome["attempts"])
            self.assertIsNone(outcome["reassignments"])
        measured = POLICY.record_outcome(self.state, "qualified", event_id="measured", outcome="blocked",
                                         attempts=2, reassignments=1, cumulative=True)
        unmeasured = POLICY.record_outcome(self.state, "qualified", event_id="no-new-count", outcome="blocked", cumulative=True)
        self.assertEqual(measured["attempts"], unmeasured["attempts"])
        self.assertEqual(measured["reassignments"], unmeasured["reassignments"])

    def test_concurrent_distinct_selection_and_same_event_replay(self):
        import subprocess, sys
        command = [sys.executable, str(ROOT / "scripts" / "model_assignment.py"), "--state", str(self.state)]
        requests = [self.request("parallel-" + str(i)) for i in range(4)]
        processes = [subprocess.Popen([*command, "select"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in requests]
        for process, request in zip(processes, requests):
            process.stdin.write(json.dumps(request)); process.stdin.close(); process.stdin = None
        outputs = [process.communicate(timeout=30) for process in processes]
        self.assertTrue(all(p.returncode == 0 for p in processes), outputs)
        state = POLICY.export_state(self.state)
        self.assertEqual(4, len(state["assignments"]))
        self.assertEqual(2, sum(a["selected_family"] == "flash" for a in state["assignments"].values()))
        requests = [{"assignment_id": "parallel-0", "event_id": "same-event", "outcome": "blocked", "attempts": 1, "cumulative": True}] * 2
        processes = [subprocess.Popen([*command, "record"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in requests]
        for process, request in zip(processes, requests):
            process.stdin.write(json.dumps(request)); process.stdin.close(); process.stdin = None
        outputs = [process.communicate(timeout=30) for process in processes]
        self.assertTrue(all(p.returncode == 0 for p in processes), outputs)
        self.assertEqual(json.loads(outputs[0][0]), json.loads(outputs[1][0]))
        self.assertEqual(5, len(POLICY.export_state(self.state)["events"]))

    def test_cli_invalid_schema_fails_closed_and_empty_home_falls_back(self):
        import os, subprocess, sys
        script = str(ROOT / "scripts" / "model_assignment.py")
        for text in ("{", "[]", "{}", json.dumps(self.request(typo=True))):
            result = subprocess.run([sys.executable, script, "select", "--state", str(self.state)], input=text, text=True, capture_output=True)
            self.assertEqual(2, result.returncode, result.stdout)
            self.assertIn("message", json.loads(result.stderr))
            self.assertFalse(self.state.exists())
        home = Path(self.temp.name) / "home"
        env = {**os.environ, "COPILOT_HOME": "", "HOME": str(home), "USERPROFILE": str(home)}
        result = subprocess.run([sys.executable, script, "select"], input=json.dumps(self.request()), text=True, capture_output=True, env=env)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue((home / ".copilot" / "token-mizer" / "model-assignments.json").exists())
        stale = subprocess.run([sys.executable, script, "spawn", "--state", str(self.state)], input="{}", text=True, capture_output=True)
        self.assertEqual(2, stale.returncode)

if __name__ == "__main__":
    unittest.main()
