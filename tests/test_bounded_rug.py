import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import model_assignment as ASSIGNMENT

MODULE_PATH = ROOT / "scripts" / "bounded_rug.py"
REPORT_PATH = ROOT / "scripts" / "token_mizer_report.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


RUG = load_module("bounded_rug", MODULE_PATH)
REPORT = load_module("token_mizer_report_for_rug", REPORT_PATH)


class BoundedRugTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "task.json"
        self.record = RUG.new_record(
            "task-1", "Synthetic change", "change", "ci-passed", "bounded-rug",
            "coordinator", "abc123", "synthetic-windows", "2026-09-17T10:00:00Z",
        )

    def tearDown(self):
        self.temp.cleanup()

    def step(self, target, result, sequence, minute, **kwargs):
        if target in {"verify", "repair", "accepted"} or (target == "blocked" and result == "failed"):
            kwargs.setdefault("target_revision", "built-sha")
            kwargs.setdefault("target_environment", "synthetic-windows")
        self.record = RUG.apply_transition(
            self.record, target, result, f"event-{sequence}", sequence - 1,
            at=f"2026-09-17T10:0{minute}:00Z", **kwargs,
        )

    def successful_initial_build(self):
        self.step("build", "started", 1, 1)
        self.step("verify", "completed", 2, 2)
        self.step(
            "accepted", "passed", 3, 3, check="python -m unittest",
            evidence="synthetic:test-run-1", milestone="ci-passed",
        )

    def test_initial_build_verifies_and_accepts(self):
        self.successful_initial_build()

        self.assertEqual(self.record["state"], "accepted")
        self.assertEqual(
            self.record["counts"],
            {"build_attempts": 1, "repair_attempts": 0, "verifications": 1},
        )
        self.assertEqual(self.record["observed_milestones"], ["ci-passed"])

    def test_acceptance_binds_exact_verified_target(self):
        self.step("build", "started", 1, 1)
        self.step("verify", "completed", 2, 2)
        with self.assertRaisesRegex(RUG.RecordError, "must match"):
            self.step(
                "accepted", "passed", 3, 3, check="unit tests",
                evidence="synthetic:wrong-head", milestone="ci-passed",
                target_revision="different-sha",
            )
        self.step(
            "accepted", "passed", 3, 3, check="unit tests",
            evidence="synthetic:pass", milestone="ci-passed",
        )
        ledger = RUG.export_ledger(self.record, "target-pilot", "2026-09-17T11:00:00Z")
        task = ledger["tasks"][0]
        self.assertEqual(task["revision"], "built-sha")
        self.assertEqual(task["environment"], "synthetic-windows")
        self.assertEqual(task["bounded_rug"]["verified_target"]["revision"], "built-sha")

        ledger_path = self.root / "target-ledger.json"
        task["revision"] = "different-sha"
        ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
        db = self.root / "empty.db"
        with closing(sqlite3.connect(db)) as connection:
            connection.execute(
                "CREATE TABLE assistant_usage_events (id TEXT, session_id TEXT, model TEXT, output_tokens INTEGER, duration_ms INTEGER, created_at TEXT, agent_id TEXT, api_endpoint TEXT, total_nano_aiu INTEGER, input_tokens INTEGER)"
            )
        report = REPORT.build_task_report(db, ledger_path)
        self.assertEqual(report["portfolio"]["accepted_tasks"], 0)
        self.assertFalse(report["tasks"][0]["acceptance_evidence_valid"])

    def test_scope_completeness_is_explicit_and_irreversible(self):
        self.record = RUG.add_scope(
            self.record, "builder", "builder", "2026-09-17T10:00:30Z",
            "2026-09-17T10:02:00Z", expected_record_revision=0,
        )
        self.record = RUG.add_scope(
            self.record, "reviewer", "reviewer", "2026-09-17T10:02:00Z",
            "2026-09-17T10:03:00Z", expected_record_revision=1,
        )
        valid_scopes = self.record
        overlapping = RUG.add_scope(
            valid_scopes, "builder", "builder", "2026-09-17T10:01:30Z",
            "2026-09-17T10:02:30Z",
            expected_record_revision=valid_scopes["record_revision"],
        )
        self.successful_initial_build()
        valid_terminal = self.record
        partial = RUG.export_ledger(valid_terminal, "partial", "2026-09-17T11:00:00Z")
        self.assertFalse(partial["tasks"][0]["scope_complete"])
        self.assertEqual(partial["tasks"][0]["scope_status"], "unknown")

        self.record = overlapping
        self.successful_initial_build()
        with self.assertRaisesRegex(RUG.RecordError, "overlapping scopes"):
            RUG.attest_scope(
                self.record, "complete", self.record["record_revision"],
                at="2026-09-17T10:04:00Z",
            )

        self.record = RUG.attest_scope(
            valid_terminal, "complete", valid_terminal["record_revision"],
            at="2026-09-17T10:04:00Z",
        )
        complete = RUG.export_ledger(self.record, "complete", "2026-09-17T11:00:00Z")
        self.assertTrue(complete["tasks"][0]["scope_complete"])
        before_attestation = RUG.export_ledger(
            self.record, "before-attestation", "2026-09-17T10:03:30Z"
        )
        self.assertFalse(before_attestation["tasks"][0]["scope_complete"])
        self.assertEqual(before_attestation["tasks"][0]["scope_status"], "unknown")
        self.assertIsNone(before_attestation["tasks"][0]["scope_attested_at"])
        with self.assertRaisesRegex(RUG.RecordError, "cannot be reset"):
            RUG.attest_scope(self.record, "partial", self.record["record_revision"])

    def test_one_failed_verification_then_repair_succeeds(self):
        self.step("build", "started", 1, 1)
        self.step("verify", "completed", 2, 2)
        self.step("repair", "failed", 3, 3, check="unit tests", evidence="synthetic:failure-1")
        self.step("verify", "completed", 4, 4)
        self.step("accepted", "passed", 5, 5, check="unit tests", evidence="synthetic:pass-2", milestone="ci-passed")

        self.assertEqual(self.record["state"], "accepted")
        self.assertEqual(self.record["counts"]["repair_attempts"], 1)
        self.assertEqual(self.record["counts"]["verifications"], 2)

    def test_failed_verification_after_repair_is_blocked(self):
        self.step("build", "started", 1, 1)
        self.step("verify", "completed", 2, 2)
        self.step("repair", "failed", 3, 3, check="unit tests", evidence="synthetic:failure-1")
        self.step("verify", "completed", 4, 4)
        self.step("blocked", "failed", 5, 5, check="unit tests", evidence="synthetic:failure-2")

        self.assertEqual(self.record["state"], "blocked")
        with self.assertRaisesRegex(RUG.RecordError, "terminal state"):
            RUG.apply_transition(self.record, "verify", "completed", "late", 5)

    def test_premature_acceptance_and_missing_boundary_evidence_are_rejected(self):
        with self.assertRaisesRegex(RUG.RecordError, "invalid transition"):
            RUG.apply_transition(self.record, "accepted", "passed", "premature", 0)
        with self.assertRaisesRegex(RUG.RecordError, "only by an accepted transition"):
            RUG.apply_transition(
                self.record, "build", "started", "early-milestone", 0,
                milestone="ci-passed",
            )
        self.step("build", "started", 1, 1)
        self.step("verify", "completed", 2, 2)
        with self.assertRaisesRegex(RUG.RecordError, "selected boundary"):
            RUG.apply_transition(
                self.record, "accepted", "passed", "no-boundary", 2,
                check="unit tests", evidence="synthetic:pass",
                target_revision="built-sha", target_environment="synthetic-windows",
            )

    def test_invalid_replayed_and_stale_transitions_do_not_reset_persisted_counts(self):
        self.step("build", "started", 1, 1)
        RUG.atomic_write(self.path, self.record)
        resumed = RUG.read_record(self.path)
        with self.assertRaisesRegex(RUG.RecordError, "replayed event_id"):
            RUG.apply_transition(resumed, "verify", "completed", "event-1", 1)
        with self.assertRaisesRegex(RUG.RecordError, "stale sequence"):
            RUG.apply_transition(resumed, "verify", "completed", "new", 0)
        annotated = RUG.add_observation(
            resumed, "builder", "unknown", "unknown", "not observed",
            at="2026-09-17T10:01:30Z", expected_record_revision=1,
        )
        with self.assertRaisesRegex(RUG.RecordError, "stale record revision"):
            RUG.apply_transition(
                annotated, "verify", "completed", "stale-annotation", 1,
                expected_record_revision=1, at="2026-09-17T10:02:00Z",
            )
        resumed["counts"]["build_attempts"] = 0
        with self.assertRaisesRegex(RUG.RecordError, "attempt counts"):
            RUG.validate_record(resumed)
        self.assertEqual(RUG.read_record(self.path)["counts"]["build_attempts"], 1)

    def test_infrastructure_block_stops_before_repair(self):
        self.step("blocked", "budget-blocked", 1, 1, evidence="synthetic:no-authorized-budget")
        self.assertEqual(self.record["state"], "blocked")
        self.assertEqual(self.record["counts"]["build_attempts"], 0)

    def test_export_is_v1_ledger_compatible_and_preserves_unknown_cost(self):
        self.record = RUG.add_scope(
            self.record, "builder", "builder", "2026-09-17T10:01:00Z", "2026-09-17T10:02:30Z"
        )
        self.successful_initial_build()
        self.record = RUG.record_followup(
            self.record, reopened=False, rolled_back=None,
            expected_record_revision=self.record["record_revision"],
            at="2026-09-17T10:30:00Z",
        )
        before_followup = RUG.export_ledger(self.record, "pilot-early", "2026-09-17T10:10:00Z")
        self.assertNotIn("followup_matured", before_followup["tasks"][0])
        ledger = RUG.export_ledger(self.record, "pilot-1", "2026-09-17T11:00:00Z")
        ledger_path = self.root / "ledger.json"
        ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
        db = self.root / "usage.db"
        with closing(sqlite3.connect(db)) as connection:
            connection.execute(
                """CREATE TABLE assistant_usage_events (
                id TEXT, session_id TEXT, model TEXT, output_tokens INTEGER,
                duration_ms INTEGER, created_at TEXT, agent_id TEXT,
                api_endpoint TEXT, total_nano_aiu INTEGER, input_tokens INTEGER)"""
            )
            connection.executemany(
                "INSERT INTO assistant_usage_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("c", "coordinator", "synthetic-astra", 10, 1000, "2026-09-17T10:01:30Z", None, "/responses", 1_000_000_000, 20),
                    ("b", "builder", "synthetic-sol", 20, 2000, "2026-09-17T10:02:00Z", "worker", "/responses", None, 30),
                    ("r", "omitted-reviewer", "synthetic-reviewer", 200, 3000, "2026-09-17T10:02:30Z", "reviewer", "/responses", 9_000_000_000, 300),
                ],
            )
            connection.commit()

        report = REPORT.build_task_report(db, ledger_path)
        task = report["tasks"][0]

        self.assertEqual(report["schema_version"], "1.0")
        self.assertEqual(task["pilot_mode"], "bounded-rug")
        self.assertEqual(task["calls"], 2)
        self.assertEqual(task["known_cost_records"], 1)
        self.assertEqual(task["unknown_cost_records"], 1)
        self.assertEqual(task["cost_coverage"], "partial-observed-subtotal")
        self.assertTrue(task["followup_matured"])
        self.assertFalse(task["reopened"])
        self.assertIsNone(task["rolled_back"])
        pilot = report["portfolio"]["pilot_modes"][0]
        self.assertEqual(pilot["mode"], "bounded-rug")
        self.assertEqual(pilot["input_tokens"], 50)
        self.assertEqual(pilot["output_tokens"], 30)
        self.assertEqual(pilot["known_cost_records"], 1)
        self.assertEqual(pilot["unknown_cost_records"], 1)
        self.assertEqual(pilot["cost_coverage"], "partial-observed-subtotal")
        self.assertEqual(pilot["recorded_ai_credits"], 1.0)
        self.assertEqual(pilot["model_active_ms"], 3000.0)
        self.assertEqual(task["duration_coverage"]["status"], "complete")
        self.assertEqual(pilot["duration_coverage"]["status"], "complete")
        self.assertEqual(
            {role["role"]: role["calls"] for role in pilot["roles"]},
            {"builder": 1, "coordinator": 1},
        )
        self.assertTrue(
            all(role["duration_coverage"]["status"] == "complete" for role in pilot["roles"])
        )
        self.assertEqual(pilot["repair_attempts"], 0)
        self.assertEqual(pilot["followup_matured_tasks"], 1)
        self.assertEqual(pilot["reopened_tasks"], 0)
        self.assertIsNone(report["portfolio"]["task_types"][0]["credits_per_accepted_task"])
        stratum = report["portfolio"]["pilot_strata"][0]
        self.assertEqual(stratum["task_class"], "change")
        self.assertIsNone(stratum["credits_per_accepted_task"])

        ledger["tasks"][0]["outcome"] = "blocked"
        ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
        blocked_report = REPORT.build_task_report(db, ledger_path)
        self.assertEqual(blocked_report["portfolio"]["accepted_tasks"], 0)
        self.assertEqual(blocked_report["portfolio"]["pilot_modes"][0]["accepted_tasks"], 0)

    def assignment_request(self):
        runtime = "synthetic-connection/gpt-5.6-luna"
        return {"assignment_id": "a", "task_id": "task-1", "task_class": "change", "acceptance_boundary": "ci-passed",
                "required_context": 50, "now": "2026-09-17T10:00:00Z", "candidates": [
                    {"role": "builder", "family": "luna", "provider": "Foundry", "runtime_id": runtime,
                     "available": True, "authorized": True, "context_capacity": 100,
                     "route_evidence": {"source": "local", "verified": True, "runtime_id": runtime}}]}

    def assignment_export(self):
        state = self.root / "assignments.json"
        ASSIGNMENT.select_assignment(state, **self.assignment_request())
        ASSIGNMENT.record_outcome(state, "a", event_id="outcome", outcome="verified", cumulative=True,
                                  attempts=1, timestamp="2026-09-18T10:00:00Z", evidence=["synthetic:test"],
                                  target_revision="synthetic-sha", target_environment="synthetic")
        return ASSIGNMENT.export_state(state, "2026-09-18T11:00:00Z")

    def test_typed_assignment_binding_and_forged_cutoff_rejected(self):
        exported = self.assignment_export()
        for key, value in (("task_id", "wrong-task"), ("task_class", "wrong-class"), ("acceptance_boundary", "merged")):
            from copy import deepcopy
            other = deepcopy(self.record)
            other["task"][{"task_id": "id", "task_class": "class", "acceptance_boundary": "acceptance_boundary"}[key]] = value
            with self.subTest(key=key), self.assertRaises(RUG.RecordError):
                RUG.add_assignment(other, exported, "a")
        exported["export"]["metadata"]["cutoff"] = "2026-09-17T11:00:00Z"
        with self.assertRaisesRegex(RUG.RecordError, "cutoff"):
            RUG.add_assignment(self.record, exported, "a")
        exported["events"] = exported["events"][:1]
        with self.assertRaisesRegex(RUG.RecordError, "differs"):
            RUG.add_assignment(self.record, exported, "a")

    def test_newer_rug_snapshot_excluded_and_as_of_snapshot_survives(self):
        exported = self.assignment_export()
        self.record = RUG.add_assignment(self.record, exported, "a")
        self.step("build", "started", 1, 1)
        ledger = RUG.export_ledger(self.record, "synthetic", "2026-09-17T11:00:00Z")
        rug = ledger["tasks"][0]["bounded_rug"]
        self.assertEqual([], rug["model_assignments"])
        self.assertEqual(1, rug["assignment_evidence_coverage"]["excluded_newer_snapshots"])
        earlier = ASSIGNMENT.export_state(self.root / "assignments.json", "2026-09-17T10:30:00Z")
        self.record["model_assignments"] = []
        self.record = RUG.add_assignment(self.record, earlier, "a")
        ledger = RUG.export_ledger(self.record, "synthetic", "2026-09-17T11:00:00Z")
        snapshot = ledger["tasks"][0]["bounded_rug"]["model_assignments"][0]
        self.assertEqual("unknown", snapshot["outcome"])
        self.assertIsNone(snapshot["attempts"])
        self.assertEqual("unknown", snapshot["admission_status"])

    def test_public_cli_assignment_to_rug_and_report_end_to_end(self):
        assignment_state = self.root / "assignments.json"
        assignment_script = ROOT / "scripts" / "model_assignment.py"
        def allocate(action, request):
            result = subprocess.run([sys.executable, str(assignment_script), action, "--state", str(assignment_state)],
                                    input=json.dumps(request), text=True, capture_output=True)
            self.assertEqual(0, result.returncode, result.stderr)
            return json.loads(result.stdout)
        request = self.assignment_request()
        selected = allocate("select", request)
        admitted = allocate("admit", {**request, "now": "2026-09-17T10:00:01Z"})
        # Synthetic host boundary: consume the actual handoff, without an inference call.
        host = subprocess.run([sys.executable, "-c", "import json,sys; h=json.load(sys.stdin); print(json.dumps({'actual_runtime_id':h['selected_runtime_id'],'actual_provider':h['selected_provider'],'task_id':h['task_id']}))"],
                              input=json.dumps(admitted["handoff"]), text=True, capture_output=True)
        self.assertEqual(0, host.returncode, host.stderr)
        observed = json.loads(host.stdout)
        self.assertEqual(selected["selected_runtime_id"], observed["actual_runtime_id"])
        measured = allocate("record", {"assignment_id": "a", **observed, "event_id": "measured-1",
                                       "outcome": "verified", "cumulative": True, "attempts": 1,
                                       "evidence": ["synthetic:host-stub"], "target_revision": "synthetic-sha",
                                       "target_environment": "synthetic", "timestamp": "2026-09-17T10:03:00Z"})
        self.assertEqual("verified", measured["verification"])
        allocate("record", {"assignment_id": "a", "event_id": "future", "outcome": "blocked", "cumulative": True,
                            "attempts": 2, "timestamp": "2026-09-18T10:00:00Z"})
        historical = allocate("export", {"cutoff": "2026-09-17T10:04:00Z"})
        self.assertEqual(1, historical["assignments"]["a"]["attempts"])
        export_file = self.root / "assignment-export.json"
        export_file.write_text(json.dumps(historical), encoding="utf-8")
        init = self.run_cli("init", "--file", str(self.path), "--task-id", "task-1", "--label", "Synthetic",
                            "--task-class", "change", "--acceptance-boundary", "ci-passed", "--coordinator-session", "synthetic",
                            "--revision", "synthetic-sha", "--environment", "synthetic", "--at", "2026-09-17T10:00:00Z")
        self.assertEqual(0, init.returncode, init.stderr)
        attached = self.run_cli("add-assignment", "--file", str(self.path), "--assignment-export", str(export_file),
                                "--assignment-id", "a", "--expected-record-revision", "0")
        self.assertEqual(0, attached.returncode, attached.stderr)
        self.assertEqual(1, len(json.loads(attached.stdout)["model_assignments"]))
        before = self.path.read_bytes()
        historical["assignments"]["a"]["task_id"] = "mismatched"
        export_file.write_text(json.dumps(historical), encoding="utf-8")
        bad = self.run_cli("add-assignment", "--file", str(self.path), "--assignment-export", str(export_file),
                           "--assignment-id", "a", "--expected-record-revision", "1")
        self.assertEqual(2, bad.returncode)
        self.assertEqual(before, self.path.read_bytes())
        built = self.run_cli("transition", "--file", str(self.path), "--to", "build", "--result", "started",
                             "--event-id", "build", "--expected-sequence", "0", "--expected-record-revision", "1", "--at", "2026-09-17T10:01:00Z")
        self.assertEqual(0, built.returncode, built.stderr)
        ledger_path = self.root / "ledger.json"
        exported = self.run_cli("export-ledger", "--file", str(self.path), "--output", str(ledger_path),
                                "--analysis-id", "synthetic", "--cutoff", "2026-09-17T11:00:00Z")
        self.assertEqual(0, exported.returncode, exported.stderr)
        self.assertEqual(1, len(json.loads(exported.stdout)["tasks"][0]["bounded_rug"]["model_assignments"]))
        db = self.root / "session-store.db"
        with closing(sqlite3.connect(db)) as connection:
            connection.execute("CREATE TABLE assistant_usage_events (session_id TEXT, model TEXT, output_tokens INTEGER, duration_ms INTEGER, created_at TEXT, agent_id TEXT, api_endpoint TEXT)")
        report = subprocess.run([sys.executable, str(REPORT_PATH), "--db", str(db), "--start", "2026-09-17T10:00:00Z",
                                 "--end", "2026-09-17T11:00:00Z", "--model-like", "%", "--rug-ledger", str(ledger_path), "--format", "json"],
                                text=True, capture_output=True)
        self.assertEqual(0, report.returncode, report.stderr)
        evidence = json.loads(report.stdout)["rug_ingestion"]["assignment_evidence"]
        self.assertEqual(1, evidence["matched_count"])
        self.assertEqual(selected["selected_runtime_id"], evidence["matched"][0]["actual_runtime_id"])
        self.assertEqual(1, evidence["matched"][0]["attempts"])

    def run_cli(self, *arguments):
        return subprocess.run(
            [sys.executable, str(MODULE_PATH), "--registry", str(self.root / "registry.json"), *arguments],
            capture_output=True, text=True, check=False,
        )

    def test_registry_rejects_duplicate_task_identity_and_survives_restart(self):
        first = self.run_cli(
            "init", "--file", str(self.path), "--task-id", "durable-task",
            "--label", "First", "--task-class", "change",
            "--acceptance-boundary", "ci-passed", "--coordinator-session", "session-1",
            "--revision", "abc", "--environment", "synthetic",
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        duplicate = self.run_cli(
            "init", "--file", str(self.root / "other.json"), "--task-id", "durable-task",
            "--label", "Duplicate", "--task-class", "change",
            "--acceptance-boundary", "ci-passed", "--coordinator-session", "session-2",
            "--revision", "def", "--environment", "synthetic",
        )
        self.assertEqual(duplicate.returncode, 2)
        self.assertIn("already registered", duplicate.stderr)
        resumed = self.run_cli("show", "--file", str(self.path))
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(json.loads(resumed.stdout)["task"]["id"], "durable-task")

    def test_concurrent_stale_resumptions_cannot_both_write(self):
        initialized = self.run_cli(
            "init", "--file", str(self.path), "--task-id", "concurrent-task",
            "--label", "Concurrent", "--task-class", "change",
            "--acceptance-boundary", "ci-passed", "--coordinator-session", "session-1",
            "--revision", "abc", "--environment", "synthetic",
        )
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        built = self.run_cli(
            "transition", "--file", str(self.path), "--to", "build", "--result", "started",
            "--event-id", "build", "--expected-sequence", "0", "--expected-record-revision", "0",
        )
        self.assertEqual(built.returncode, 0, built.stderr)
        command = [
            sys.executable, str(MODULE_PATH), "--registry", str(self.root / "registry.json"),
            "transition", "--file", str(self.path), "--to", "verify", "--result", "completed",
            "--expected-sequence", "1", "--expected-record-revision", "1",
            "--target-revision", "built-sha", "--target-environment", "synthetic-windows",
        ]
        first = subprocess.Popen(
            [*command, "--event-id", "resume-a"], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        second = subprocess.Popen(
            [*command, "--event-id", "resume-b"], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        first_output = first.communicate(timeout=10)
        second_output = second.communicate(timeout=10)
        results = [(first.returncode, *first_output), (second.returncode, *second_output)]
        self.assertEqual(sorted(result[0] for result in results), [0, 2], results)
        persisted = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["sequence"], 2)
        self.assertEqual(persisted["counts"]["verifications"], 1)

    def test_cli_end_to_end_persists_and_exports(self):
        commands = [
            ["init", "--file", str(self.path), "--task-id", "cli-task", "--label", "CLI task", "--task-class", "change", "--acceptance-boundary", "ci-passed", "--coordinator-session", "session-1", "--revision", "abc", "--environment", "synthetic", "--at", "2026-09-17T10:00:00Z"],
            ["transition", "--file", str(self.path), "--to", "build", "--result", "started", "--event-id", "e1", "--expected-sequence", "0", "--expected-record-revision", "0", "--at", "2026-09-17T10:01:00Z"],
            ["transition", "--file", str(self.path), "--to", "verify", "--result", "completed", "--event-id", "e2", "--expected-sequence", "1", "--expected-record-revision", "1", "--at", "2026-09-17T10:02:00Z", "--target-revision", "built-sha", "--target-environment", "synthetic-windows"],
            ["transition", "--file", str(self.path), "--to", "accepted", "--result", "passed", "--event-id", "e3", "--expected-sequence", "2", "--expected-record-revision", "2", "--at", "2026-09-17T10:03:00Z", "--check", "unit tests", "--evidence", "synthetic:cli", "--milestone", "ci-passed", "--target-revision", "built-sha", "--target-environment", "synthetic-windows"],
            ["attest-scope", "--file", str(self.path), "--status", "complete", "--expected-record-revision", "3", "--at", "2026-09-17T10:04:00Z"],
        ]
        for command in commands:
            result = self.run_cli(*command)
            self.assertEqual(result.returncode, 0, result.stderr)
        ledger = self.root / "ledger.json"
        result = self.run_cli(
            "export-ledger", "--file", str(self.path), "--output", str(ledger),
            "--analysis-id", "cli-pilot", "--cutoff", "2026-09-17T11:00:00Z",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        exported = json.loads(ledger.read_text())
        self.assertEqual(exported["tasks"][0]["outcome"], "ci-passed")
        self.assertTrue(exported["tasks"][0]["scope_complete"])

        fallback_home = self.root / "fallback-home"
        fallback_copilot_home = fallback_home / ".copilot"
        fallback_copilot_home.mkdir(parents=True)
        with closing(sqlite3.connect(fallback_copilot_home / "session-store.db")) as connection:
            connection.execute(
                """CREATE TABLE assistant_usage_events (
                session_id TEXT, model TEXT, output_tokens INTEGER,
                duration_ms INTEGER, created_at TEXT, agent_id TEXT,
                api_endpoint TEXT)"""
            )
        environment = os.environ.copy()
        environment.pop("COPILOT_HOME", None)
        environment["HOME"] = str(fallback_home)
        environment["USERPROFILE"] = str(fallback_home)
        report = subprocess.run(
            [
                sys.executable, str(REPORT_PATH),
                "--start", "2026-09-17T10:00:00Z",
                "--end", "2026-09-17T11:00:00Z",
                "--model-like", "%",
                "--task-ledger", str(ledger),
                "--format", "json",
            ],
            capture_output=True, text=True, check=False, env=environment,
        )
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertEqual(json.loads(report.stdout)["task_analysis"]["portfolio"]["accepted_tasks"], 1)


if __name__ == "__main__":
    unittest.main()
