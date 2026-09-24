import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("model_assignment", ROOT / "scripts" / "model_assignment.py")
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)
OPT_IN = {"builtin_uncapped_opt_in": True, "source": "local"}


def candidate(family, role="builder", capacity=100, **updates):
    provider, model = POLICY.FAMILIES[family]
    runtime = model if provider == "GitHub" else "synthetic-foundry/" + model
    value = {"role": role, "family": family, "provider": provider, "runtime_id": runtime,
             "available": True, "authorized": True, "context_capacity": capacity,
             "route_evidence": {"source": "host", "verified": True, "provider": provider,
                                "family": family, "runtime_id": runtime}}
    if family == "deepseek":
        value["capacity_evidence"] = {"source": "host", "verified": True,
                                      "runtime_id": runtime, "context_capacity": capacity}
    return {**value, **updates}


class ModelAssignmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name) / "assignments.json"

    def tearDown(self):
        self.temp.cleanup()

    def request(self, aid="a", **updates):
        return {"assignment_id": aid, "task_id": "task-" + aid,
                "task_class": "code-change", "acceptance_boundary": "ci-passed",
                "required_context": 50, "candidates": [candidate("sol6"), candidate("luna")],
                "paid_policy": OPT_IN, **updates}

    def choose(self, aid="a", **updates):
        return POLICY.select_assignment(self.state, **self.request(aid, **updates))

    def test_default_sol6_and_independent_luna_without_opt_in(self):
        self.assertEqual("sol6", self.choose()["selected_family"])
        self.assertEqual("context-fit-policy-preference", self.choose()["selection_reason"])
        self.assertEqual("luna", self.choose("economy", paid_policy=None)["selected_family"])
        with self.assertRaises(POLICY.AssignmentBlocked):
            self.choose("no-policy", candidates=[candidate("sol6")], paid_policy=None)
        for policy in ({"builtin_uncapped_opt_in": False, "source": "local"},
                       {"builtin_uncapped_opt_in": True, "source": "host"}):
            with self.assertRaises((ValueError, POLICY.AssignmentBlocked)):
                self.choose("denied", candidates=[candidate("sol6")], paid_policy=policy)
        self.assertEqual(POLICY.STATE_VERSION, json.loads(self.state.read_text())["schema_version"])

    def test_context_authorization_and_exact_host_provider_evidence(self):
        for update in ({"context_capacity": "unknown"}, {"context_capacity": 49},
                       {"available": False}, {"authorized": False}):
            with self.subTest(update=update), self.assertRaises(POLICY.AssignmentBlocked):
                self.choose("blocked", candidates=[candidate("sol6", **update)])
        for update in ({"provider": "Foundry"}, {"runtime_id": "foundry/gpt-6-sol"},
                       {"route_evidence": {"verified": True, "source": "local", "provider": "GitHub",
                                           "family": "sol6", "runtime_id": "gpt-6-sol"}}):
            with self.subTest(update=update), self.assertRaises(POLICY.AssignmentBlocked):
                self.choose("spoof", candidates=[candidate("sol6", **update)])
        with self.assertRaises(POLICY.AssignmentBlocked):
            self.choose("duplicate", candidates=[candidate("sol6"), candidate("sol6", authorized=False)])
        with self.assertRaises(POLICY.AssignmentBlocked):
            self.choose("mixed-roles", candidates=[candidate("sol6"), candidate("luna", role="reviewer")])
        with self.assertRaises(POLICY.AssignmentBlocked):
            self.choose("large", candidates=[candidate("sol6", authorized=False), candidate("luna")],
                        large_context=True)

    def test_urgent_and_failed_fix_are_evidenced_and_not_default(self):
        urgent = {"kind": "urgent", "source": "user", "evidence_reference": "user:urgent"}
        failed = {"kind": "failed-first-fix", "source": "host", "evidence_reference": "ci:failed"}
        pool = [candidate("sol6"), candidate("grok"), candidate("astra")]
        self.assertEqual("sol6", self.choose(candidates=pool)["selected_family"])
        self.assertEqual("grok", self.choose("urgent", candidates=pool, intent_evidence=urgent)["selected_family"])
        self.assertEqual("astra", self.choose("failed", candidates=pool, intent_evidence=failed)["selected_family"])
        for kind in ("urgent", "failed-first-fix"):
            family = "grok" if kind == "urgent" else "astra"
            with self.assertRaises(POLICY.AssignmentBlocked):
                self.choose(kind + "-no-proof", candidates=[candidate(family)])
        with self.assertRaises(POLICY.AssignmentBlocked):
            self.choose("claimed-urgent", candidates=[candidate("grok")],
                        intent_evidence={**urgent, "source": "host"})
        with self.assertRaises(POLICY.AssignmentBlocked):
            self.choose("fake-failure", candidates=[candidate("astra")],
                        intent_evidence={**failed, "evidence_reference": ""})

    def test_deepseek_pilot_only_bounded_repair_and_known_capacity(self):
        pilot = {"kind": "deployment-repair-pilot", "source": "user",
                 "evidence_reference": "user:approved-pilot", "bounded_attempts": 1,
                 "reproduction": True, "ci": True, "live_verification": True}
        request = self.request("pilot", task_class="deployment-repair", path="deepseek-pilot",
                               candidates=[candidate("deepseek")], intent_evidence=pilot, paid_policy=None)
        selected = POLICY.select_assignment(self.state, **request)
        self.assertEqual("deepseek", selected["selected_family"])
        self.assertEqual(candidate("deepseek")["runtime_id"],
                         POLICY.admit_assignment(self.state, **request)["handoff"]["selected_runtime_id"])
        admitted_bytes = self.state.read_bytes()
        with self.assertRaisesRegex(POLICY.AssignmentBlocked, "only one"):
            POLICY.admit_assignment(self.state, **request)
        self.assertEqual(admitted_bytes, self.state.read_bytes())
        for change in ({"context_capacity": "unknown"}, {"authorized": False}, {"available": False}):
            with self.subTest(change=change), self.assertRaises(POLICY.AssignmentBlocked):
                POLICY.select_assignment(self.state, **{**request, "assignment_id": str(change),
                    "candidates": [candidate("deepseek", **change)]})
        with self.assertRaisesRegex(POLICY.AssignmentBlocked, "host-verified"):
            self.choose("unknown-pilot", task_class="deployment-repair", path="deepseek-pilot",
                        candidates=[candidate("deepseek", capacity_evidence=None)], intent_evidence=pilot,
                        paid_policy=None)
        with self.assertRaisesRegex(POLICY.AssignmentBlocked, "second attempt"):
            POLICY.select_assignment(self.state, **{**request, "assignment_id": "second",
                                                     "task_id": request["task_id"]})
        self.assertEqual("admitted", POLICY.export_state(self.state)["assignments"]["pilot"]["admission_status"])
        for changes in ({"task_class": "code-change"}, {"path": "direct"},
                        {"intent_evidence": {**pilot, "bounded_attempts": 2}},
                        {"intent_evidence": {**pilot, "live_verification": False}}):
            with self.subTest(changes=changes), self.assertRaises(POLICY.AssignmentBlocked):
                POLICY.select_assignment(self.state, **{**request, "assignment_id": str(changes), **changes})
        with self.assertRaises(POLICY.AssignmentBlocked):
            self.choose("not-fallback", candidates=[candidate("deepseek")], intent_evidence=pilot)

    def test_fresh_admission_rechecks_policy_identity_and_runtime(self):
        request = self.request()
        selected = self.choose()
        self.assertNotIn("handoff", selected)
        self.assertEqual("gpt-6-sol", POLICY.admit_assignment(self.state, **request)["handoff"]["selected_runtime_id"])
        original = self.state.read_bytes()
        for updates in ({"paid_policy": None}, {"required_context": 51},
                        {"acceptance_boundary": "deployed"}, {"intent_evidence": {"kind": "urgent"}},
                        {"explicit_model": "gpt-6-sol"}):
            for action in (POLICY.select_assignment, POLICY.admit_assignment):
                with self.subTest(updates=updates, action=action.__name__), self.assertRaises(POLICY.AssignmentBlocked):
                    action(self.state, **{**request, **updates})
                self.assertEqual(original, self.state.read_bytes())
        with self.assertRaises(POLICY.AssignmentBlocked):
            POLICY.admit_assignment(self.state, **{**request, "candidates": [candidate("sol6", authorized=False), candidate("luna")]})
        self.assertNotIn("handoff", POLICY.select_assignment(self.state, **request))

    def test_v4_history_read_export_record_and_retired_admission_denied(self):
        old = self.choose("legacy", candidates=[candidate("luna")], paid_policy=None)
        state = json.loads(self.state.read_text())
        state["schema_version"] = "4.0"
        state["assignments"]["legacy"].pop("paid_policy")
        state["assignments"]["legacy"].pop("intent_evidence")
        self.state.write_text(json.dumps(state))
        self.assertEqual(old["task_id"], POLICY.export_state(self.state)["assignments"]["legacy"]["task_id"])
        measured = POLICY.record_outcome(self.state, "legacy", event_id="old-outcome",
                                         outcome="blocked", attempts=1, cumulative=True)
        self.assertEqual(1, measured["attempts"])
        self.assertEqual("luna", POLICY.assignment_from_export(
            POLICY.export_state(self.state, "2099-01-01T00:00:00Z"), "legacy")["selected_family"])
        before = self.state.read_bytes()
        for action in (POLICY.select_assignment, POLICY.admit_assignment):
            with self.assertRaises(POLICY.AssignmentBlocked):
                action(self.state, **self.request("legacy", candidates=[candidate("luna")], paid_policy=None))
            self.assertEqual(before, self.state.read_bytes())

    def test_v4_retired_flash_and_sol_history_never_remaps(self):
        for family, model, provider in (("flash", POLICY.FLASH_MODEL, "GitHub"),
                                        ("sol", POLICY.SOL_MODEL, "Foundry")):
            with self.subTest(family=family), tempfile.TemporaryDirectory() as folder:
                state_path = Path(folder) / "old.json"
                selected = POLICY.select_assignment(state_path, **self.request(
                    "historic", candidates=[candidate("luna")], paid_policy=None))
                state = json.loads(state_path.read_text())
                state["schema_version"] = "4.0"
                allocation = state["assignments"]["historic"]
                runtime = model if provider == "GitHub" else "synthetic-foundry/" + model
                legacy_route = {"role": "builder", "family": family, "provider": provider,
                                "runtime_id": runtime, "context_capacity": 100,
                                "available": True, "authorized": True,
                                "route_evidence": {"source": "host", "verified": True,
                                                   "runtime_id": runtime}}
                allocation.update(selected_family=family, selected_provider=provider,
                                  selected_runtime_id=runtime, selected_model=model,
                                  route_evidence=legacy_route["route_evidence"],
                                  eligibility=[{**legacy_route, "eligible": True,
                                                "reasons": ["context-fit-confirmed"]}],
                                  explicit_model=runtime if family == "sol" else None)
                allocation.pop("paid_policy")
                allocation.pop("intent_evidence")
                state_path.write_text(json.dumps(state))
                exported = POLICY.export_state(state_path, "2099-01-01T00:00:00Z")
                self.assertEqual(family, POLICY.assignment_from_export(exported, "historic")["selected_family"])
                self.assertEqual(model, POLICY.record_outcome(
                    state_path, "historic", outcome="blocked", event_id="observed",
                    actual_provider=provider, actual_runtime_id=runtime, actual_model=model,
                    attempts=1, cumulative=True)["selected_model"])
                original = state_path.read_bytes()
                for action in (POLICY.select_assignment, POLICY.admit_assignment):
                    with self.assertRaises(POLICY.AssignmentBlocked):
                        action(state_path, **self.request("historic", candidates=[candidate("sol6")]))
                    self.assertEqual(original, state_path.read_bytes())

    def test_retired_routes_rejected_even_with_explicit_override(self):
        for family, model, provider in (("flash", POLICY.FLASH_MODEL, "GitHub"),
                                        ("sol", POLICY.SOL_MODEL, "Foundry")):
            runtime = model if provider == "GitHub" else "synthetic-foundry/" + model
            raw = {"role": "builder", "family": family, "provider": provider, "runtime_id": runtime,
                   "available": True, "authorized": True, "context_capacity": 100,
                   "route_evidence": {"verified": True, "source": "host", "provider": provider,
                                      "family": family, "runtime_id": runtime}}
            with self.subTest(family=family), self.assertRaises(POLICY.AssignmentBlocked):
                self.choose("retired-" + family, candidates=[raw], explicit_model=runtime)

    def test_record_replay_cutoff_cli_and_concurrent_selection(self):
        self.choose(now="2026-09-17T10:00:00Z")
        result = POLICY.record_outcome(self.state, "a", event_id="event", outcome="verified",
                                       attempts=1, reassignments=0, cumulative=True,
                                       evidence=["ci:synthetic"], target_revision="sha",
                                       target_environment="test", timestamp="2026-09-17T11:00:00Z")
        self.assertEqual("verified", result["verification"])
        self.assertEqual("unknown", POLICY.assignment_from_export(
            POLICY.export_state(self.state, "2026-09-17T10:30:00Z"), "a")["outcome"])
        self.assertEqual(result, POLICY.record_outcome(self.state, "a", event_id="event",
                         outcome="verified", attempts=1, reassignments=0, cumulative=True,
                         evidence=["ci:synthetic"], target_revision="sha",
                         target_environment="test", timestamp="2026-09-17T11:00:00Z"))
        with self.assertRaises(POLICY.AssignmentBlocked):
            POLICY.record_outcome(self.state, "a", event_id="event", outcome="failed", cumulative=True)
        script = ROOT / "scripts" / "model_assignment.py"
        result = subprocess.run([sys.executable, str(script), "--state", str(self.state), "select"],
                                input=json.dumps(self.request("cli")), text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("sol6", json.loads(result.stdout)["selected_family"])

    def test_concurrent_same_task_cannot_duplicate_pilot_admission(self):
        pilot = {"kind": "deployment-repair-pilot", "source": "user",
                 "evidence_reference": "user:pilot", "bounded_attempts": 1,
                 "reproduction": True, "ci": True, "live_verification": True}
        request = self.request("pilot-race", task_class="deployment-repair", path="deepseek-pilot",
                               candidates=[candidate("deepseek")], intent_evidence=pilot, paid_policy=None)
        script = ROOT / "scripts" / "model_assignment.py"

        def concurrent(action, payloads):
            processes = [subprocess.Popen([sys.executable, str(script), "--state", str(self.state), action],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                         for _ in payloads]
            outputs = [process.communicate(json.dumps(payload), timeout=30) for process, payload in
                       zip(processes, payloads)]
            return [process.returncode for process in processes], outputs

        codes, _ = concurrent("select", [request, {**request, "assignment_id": "pilot-race-2"}])
        self.assertEqual([0, 2], sorted(codes))
        assigned = next(iter(POLICY.export_state(self.state)["assignments"]))
        actual = {**request, "assignment_id": assigned}
        codes, outputs = concurrent("admit", [actual, actual])
        self.assertEqual([0, 2], sorted(codes), outputs)
        self.assertEqual(1, sum(event["type"] == "admitted" for event in
                                POLICY.export_state(self.state)["events"]))

    def test_standing_user_reference_is_reusable_only_across_distinct_pilot_tasks(self):
        standing = {"kind": "deployment-repair-pilot", "source": "user",
                    "evidence_reference": "private:standing-user-approval", "bounded_attempts": 1,
                    "reproduction": True, "ci": True, "live_verification": True}
        for aid in ("repair-one", "repair-two"):
            with self.subTest(aid=aid):
                request = self.request(aid, task_class="deployment-repair",
                                       acceptance_boundary="deployed-and-healthy", path="deepseek-pilot",
                                       candidates=[candidate("deepseek")], intent_evidence=standing,
                                       paid_policy=None)
                self.assertEqual("deepseek", POLICY.select_assignment(self.state, **request)["selected_family"])
                self.assertEqual(aid, POLICY.admit_assignment(self.state, **request)["assignment_id"])
                with self.assertRaisesRegex(POLICY.AssignmentBlocked, "only one"):
                    POLICY.admit_assignment(self.state, **request)
                with self.assertRaisesRegex(POLICY.AssignmentBlocked, "second attempt"):
                    POLICY.select_assignment(self.state, **{**request, "assignment_id": aid + "-retry"})
        self.assertEqual(2, len(POLICY.export_state(self.state)["assignments"]))


if __name__ == "__main__":
    unittest.main()
