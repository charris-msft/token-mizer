import hashlib
import importlib.util
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "token_mizer_report.py"
SPEC = importlib.util.spec_from_file_location("token_mizer_report", MODULE_PATH)
REPORT = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(REPORT)


class TokenMizerReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "session-store.db"
        self.session_state = self.root / "session-state"
        self.data_db = self.root / "data.db"
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute(
                """
                CREATE TABLE assistant_usage_events (
                    id TEXT, session_id TEXT, turn_index INTEGER, agent_id TEXT,
                    parent_tool_call_id TEXT, model TEXT, input_tokens INTEGER,
                    output_tokens INTEGER, cache_read_tokens INTEGER,
                    cache_write_tokens INTEGER, reasoning_tokens INTEGER,
                    total_nano_aiu INTEGER, request_multiplier REAL,
                    duration_ms INTEGER, time_to_first_token_ms INTEGER,
                    inter_token_latency_ms REAL, initiator TEXT, api_endpoint TEXT,
                    reasoning_effort TEXT, finish_reason TEXT,
                    content_filter_triggered INTEGER, token_details_json TEXT,
                    created_at TEXT, output_ttft_ms INTEGER,
                    copilot_usage_model TEXT
                )
                """
            )
            connection.commit()

    def tearDown(self):
        self.temp.cleanup()

    def add_usage(
        self,
        session_id="s1",
        model="synthetic-opus",
        output_tokens=100,
        duration_ms=1000,
        created_at="2026-09-01T00:00:00Z",
        agent_id=None,
        api_endpoint="/responses",
        total_nano_aiu=1,
        input_tokens=None,
        cache_read_tokens=None,
        cache_write_tokens=None,
        reasoning_tokens=None,
        reasoning_effort=None,
        token_details_json=None,
        time_to_first_token_ms=None,
        output_ttft_ms=None,
        inter_token_latency_ms=None,
    ):
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute(
                """
                INSERT INTO assistant_usage_events (
                    id, session_id, model, output_tokens, duration_ms, created_at,
                    agent_id, api_endpoint, total_nano_aiu, input_tokens,
                    cache_read_tokens, cache_write_tokens, reasoning_tokens,
                    reasoning_effort, token_details_json, time_to_first_token_ms,
                    output_ttft_ms, inter_token_latency_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"id-{session_id}-{created_at}-{duration_ms}", session_id, model,
                    output_tokens, duration_ms, created_at, agent_id, api_endpoint,
                    total_nano_aiu, input_tokens, cache_read_tokens, cache_write_tokens,
                    reasoning_tokens, reasoning_effort, token_details_json,
                    time_to_first_token_ms, output_ttft_ms, inter_token_latency_ms,
                ),
            )
            connection.commit()

    def report(self, **overrides):
        args = {
            "db_path": self.db,
            "start_text": "2026-09-01T00:00:00Z",
            "end_text": "2026-09-02T00:00:00Z",
            "model_like": "%opus%",
        }
        args.update(overrides)
        return REPORT.build_report(**args)

    def test_mean_and_pooled_tpm_are_distinct_for_unequal_durations(self):
        self.add_usage(session_id="fast", output_tokens=100, duration_ms=1000)
        self.add_usage(session_id="slow", output_tokens=100, duration_ms=9000)

        group = self.report()["groups"][0]

        self.assertEqual(group["calls"], 2)
        self.assertEqual(group["sessions"], 2)
        self.assertAlmostEqual(group["mean_per_call_output_tpm"], 3333.333, places=2)
        self.assertAlmostEqual(group["pooled_overall_output_tpm"], 1200.0)
        self.assertNotEqual(
            group["mean_per_call_output_tpm"], group["pooled_overall_output_tpm"]
        )

    def test_timestamp_normalization_and_exclusive_cutoff(self):
        self.add_usage(session_id="sqlite-start", created_at="2026-09-01 00:00:00")
        self.add_usage(session_id="offset-start", created_at="2026-08-31T19:00:00-05:00")
        self.add_usage(session_id="offset-from-prior-date", created_at="2026-08-31T23:30:00-01:00")
        self.add_usage(session_id="offset-from-next-date", created_at="2026-09-02T00:30:00+01:00")
        self.add_usage(session_id="before-start", created_at="2026-08-31T18:59:59-05:00")
        self.add_usage(session_id="end-cutoff", created_at="2026-09-01T19:00:00-05:00")

        result = self.report()

        self.assertEqual(result["qualifying_records"], 4)
        self.assertEqual(result["candidate_records"], 6)
        self.assertEqual(result["exclusions"]["outside_exact_window"], 2)

    def test_excludes_invalid_duration_subagent_aggregate_and_nonpositive_output(self):
        self.add_usage(session_id="valid")
        self.add_usage(session_id="zero-duration", duration_ms=0)
        self.add_usage(session_id="negative-duration", duration_ms=-1)
        self.add_usage(session_id="subagent", agent_id="worker")
        self.add_usage(session_id="aggregate", api_endpoint=None)
        self.add_usage(session_id="zero-output", output_tokens=0)

        result = self.report()

        self.assertEqual(result["qualifying_records"], 1)
        self.assertEqual(result["exclusions"]["nonpositive_duration_ms"], 2)
        self.assertEqual(result["exclusions"]["subagent"], 1)
        self.assertEqual(result["exclusions"]["aggregate_or_missing_endpoint"], 1)
        self.assertEqual(result["exclusions"]["nonpositive_output_tokens"], 1)

    def test_missing_schema_is_honestly_unavailable(self):
        broken = self.root / "broken.db"
        with closing(sqlite3.connect(broken)) as connection:
            connection.execute("CREATE TABLE assistant_usage_events (model TEXT)")
            connection.commit()

        with self.assertRaisesRegex(REPORT.ReportUnavailable, "missing required columns"):
            REPORT.build_report(
                broken,
                "2026-09-01T00:00:00Z",
                "2026-09-02T00:00:00Z",
                "%opus%",
            )

    def test_parameterized_model_pattern_does_not_execute_sql(self):
        self.add_usage()

        result = self.report(model_like="%'; DROP TABLE assistant_usage_events; --")

        self.assertEqual(result["qualifying_records"], 0)
        with closing(sqlite3.connect(self.db)) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM assistant_usage_events"
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_provider_changes_and_unknown_attribution(self):
        self.add_usage(
            session_id="provider-change",
            created_at="2026-09-01T00:10:00Z",
            total_nano_aiu=0,
        )
        self.add_usage(
            session_id="provider-change",
            created_at="2026-09-01T00:30:00Z",
            total_nano_aiu=3,
        )
        self.add_usage(
            session_id="missing-history",
            created_at="2026-09-01T00:40:00Z",
            total_nano_aiu=9,
        )
        event_dir = self.session_state / "provider-change"
        event_dir.mkdir(parents=True)
        events = [
            {
                "type": "session.start",
                "timestamp": "2026-09-01T00:00:00Z",
                "data": {"selectedModel": "provider-synthetic/synthetic-opus"},
            },
            {
                "type": "user.message",
                "timestamp": "2026-09-01T00:05:00Z",
                "data": {"content": "PRIVATE PROMPT MUST NOT APPEAR"},
            },
            {
                "type": "session.model_change",
                "timestamp": "2026-09-01T00:20:00Z",
                "data": {
                    "newModel": "synthetic-opus",
                    "previousModel": "provider-synthetic/synthetic-opus",
                },
            },
        ]
        (event_dir / "events.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events), encoding="utf-8"
        )
        with closing(sqlite3.connect(self.data_db)) as connection:
            connection.execute(
                "CREATE TABLE model_providers (id TEXT, name TEXT, type TEXT, settings_json TEXT)"
            )
            connection.execute(
                "INSERT INTO model_providers VALUES (?, ?, ?, ?)",
                ("provider-synthetic", "Synthetic Foundry", "foundry", "SECRET"),
            )
            connection.commit()

        result = self.report(
            provider_attribution=True,
            session_state=self.session_state,
            data_db=self.data_db,
        )
        groups = {group["provider"]: group for group in result["groups"]}
        serialized = json.dumps(result)

        self.assertEqual(groups["Synthetic Foundry (foundry)"]["calls"], 1)
        self.assertEqual(groups["GitHub billed"]["calls"], 1)
        self.assertEqual(groups["unknown"]["calls"], 1)
        self.assertNotIn("provider-synthetic", serialized)
        self.assertNotIn("PRIVATE PROMPT", serialized)
        self.assertNotIn("SECRET", serialized)

    def test_latest_selection_for_different_model_does_not_resurrect_stale_provider(self):
        self.add_usage(
            session_id="different-model",
            created_at="2026-09-01T00:30:00Z",
            duration_ms=60_000,
            total_nano_aiu=0,
        )
        event_dir = self.session_state / "different-model"
        event_dir.mkdir(parents=True)
        events = [
            {
                "type": "session.start",
                "timestamp": "2026-09-01T00:00:00Z",
                "data": {"selectedModel": "provider-synthetic/synthetic-opus"},
            },
            {
                "type": "session.model_change",
                "timestamp": "2026-09-01T00:20:00Z",
                "data": {"newModel": "synthetic-sol"},
            },
        ]
        (event_dir / "events.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events), encoding="utf-8"
        )

        result = self.report(
            provider_attribution=True,
            session_state=self.session_state,
            data_db=self.data_db,
        )

        self.assertEqual(result["groups"][0]["provider"], "unknown")

    def test_model_switch_during_request_is_unknown(self):
        self.add_usage(
            session_id="mid-request-switch",
            created_at="2026-09-01T00:30:00Z",
            duration_ms=600_000,
            total_nano_aiu=0,
        )
        event_dir = self.session_state / "mid-request-switch"
        event_dir.mkdir(parents=True)
        events = [
            {
                "type": "session.start",
                "timestamp": "2026-09-01T00:00:00Z",
                "data": {"selectedModel": "provider-synthetic/synthetic-opus"},
            },
            {
                "type": "session.model_change",
                "timestamp": "2026-09-01T00:25:00Z",
                "data": {"newModel": "provider-other/synthetic-opus"},
            },
        ]
        (event_dir / "events.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events), encoding="utf-8"
        )

        result = self.report(
            provider_attribution=True,
            session_state=self.session_state,
            data_db=self.data_db,
        )

        self.assertEqual(result["groups"][0]["provider"], "unknown")

    def test_malformed_model_metadata_is_unknown_without_crashing(self):
        self.add_usage(
            session_id="malformed-metadata",
            created_at="2026-09-01T00:30:00Z",
            total_nano_aiu=0,
        )
        event_dir = self.session_state / "malformed-metadata"
        event_dir.mkdir(parents=True)
        (event_dir / "events.jsonl").write_text(
            '\n'.join(
                [
                    '{"type":"user.message","data":{"content":"' + ('x' * 10000) + '"}}',
                    '{"type":"session.start","timestamp":"2026-09-01T00:00:00Z","data":{"selectedModel":"provider-synthetic/synthetic-opus"}}',
                    '{"type":"session.model_change","timestamp":"2026-09-01T00:20:00Z","data":',
                ]
            ),
            encoding="utf-8",
        )

        result = self.report(
            provider_attribution=True,
            session_state=self.session_state,
            data_db=self.data_db,
        )

        self.assertEqual(result["groups"][0]["provider"], "unknown")

    def test_session_id_cannot_escape_session_state(self):
        outside = self.root / "events.jsonl"
        outside.write_text(
            json.dumps(
                {
                    "type": "session.start",
                    "timestamp": "2026-09-01T00:00:00Z",
                    "data": {"selectedModel": "provider-synthetic/synthetic-opus"},
                }
            ),
            encoding="utf-8",
        )
        self.add_usage(session_id="..", total_nano_aiu=0)

        result = self.report(
            provider_attribution=True,
            session_state=self.session_state,
            data_db=self.data_db,
        )

        self.assertEqual(result["groups"][0]["provider"], "unknown")

    def test_richer_latency_metrics_use_nearest_rank_and_optional_coverage(self):
        self.add_usage(session_id="a", output_tokens=100, duration_ms=1000, reasoning_effort="low", time_to_first_token_ms=100)
        self.add_usage(session_id="a", output_tokens=300, duration_ms=2000, reasoning_effort="low")
        self.add_usage(session_id="b", output_tokens=5000, duration_ms=10000, reasoning_effort="high", output_ttft_ms=200)

        group = self.report()["groups"][0]

        self.assertEqual(group["request_duration_ms"]["median"], 2000)
        self.assertEqual(group["request_duration_ms"]["p90_nearest_rank"], 10000)
        self.assertAlmostEqual(group["request_duration_ms"]["mean"], 4333.333, places=2)
        self.assertAlmostEqual(group["mean_output_tokens"], 1800)
        self.assertEqual(group["timing_field_coverage"]["time_to_first_token_ms"], 1)
        self.assertEqual(group["timing_field_coverage"]["output_ttft_ms"], 1)
        self.assertEqual(group["reasoning_effort"], {"high": 1, "low": 2})
        self.assertEqual([item["output_tokens"] for item in group["output_length_strata"]], ["1-256", "257-1024", "4097+"])

    def test_missing_optional_cost_and_timing_columns_remain_compatible(self):
        minimal = self.root / "minimal.db"
        with closing(sqlite3.connect(minimal)) as connection:
            connection.execute(
                """
                CREATE TABLE assistant_usage_events (
                    session_id TEXT, model TEXT, output_tokens INTEGER,
                    duration_ms INTEGER, created_at TEXT, agent_id TEXT,
                    api_endpoint TEXT
                )
                """
            )
            connection.execute(
                "INSERT INTO assistant_usage_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("s", "synthetic-opus", 10, 1000, "2026-09-01T00:00:00Z", None, "/responses"),
            )
            connection.commit()

        result = REPORT.build_report(minimal, "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z", "%")

        self.assertEqual(result["qualifying_records"], 1)
        self.assertEqual(result["cost_cohort"]["unknown_cost_records"], 1)
        self.assertEqual(result["groups"][0]["timing_field_coverage"]["output_ttft_ms"], 0)

    def test_cost_cohort_includes_zero_output_and_reconciles_exact_ledger_without_double_count(self):
        details = json.dumps(
            [
                {"tokens": 100, "costPerBatch": 1_000_000_000, "batchSize": 100},
                {"tokens": 20, "costPerBatch": 200_000_000, "batchSize": 20},
            ]
        )
        self.add_usage(
            session_id="productive", output_tokens=20, input_tokens=100,
            cache_read_tokens=60, cache_write_tokens=10, reasoning_tokens=5,
            total_nano_aiu=1_200_000_000, token_details_json=details,
        )
        self.add_usage(
            session_id="zero-output", output_tokens=0, total_nano_aiu=100_000_000,
            token_details_json="{malformed",
        )
        self.add_usage(
            session_id="mismatch", output_tokens=5, total_nano_aiu=1,
            token_details_json=json.dumps({"tokens": 1, "costPerBatch": 2, "batchSize": 1}),
        )

        result = self.report()
        cost = result["cost_cohort"]

        self.assertEqual(result["qualifying_records"], 2)
        self.assertEqual(cost["records"], 3)
        self.assertEqual(cost["zero_output_records"], 1)
        self.assertEqual(cost["recorded_nano_aiu"], 1_300_000_001)
        self.assertAlmostEqual(cost["recorded_ai_credits"], 1.300000001)
        self.assertEqual(
            cost["token_ledger_coverage"],
            {"malformed": 1, "mismatch": 1, "reconciled": 1},
        )

    def test_all_unknown_cost_stays_null_and_table_output_handles_it(self):
        self.add_usage(total_nano_aiu=None)

        result = self.report()
        output = io.StringIO()
        with redirect_stdout(output):
            REPORT.print_table(result)

        self.assertIsNone(result["cost_cohort"]["recorded_nano_aiu"])
        self.assertIsNone(result["cost_cohort"]["recorded_ai_credits"])
        self.assertIsNone(result["cost_cohort"]["documented_usd_equivalent"])
        self.assertEqual(result["cost_cohort"]["cost_coverage"], "unknown")
        self.assertIn("unknown AI credits", output.getvalue())

    def test_partial_token_ledger_is_not_reconciled(self):
        details = json.dumps(
            [
                {"tokenCount": 1, "costPerBatch": 1_000_000_000, "batchSize": 1},
                {"tokenCount": 10, "batchSize": 1},
            ]
        )
        self.add_usage(total_nano_aiu=1_000_000_000, token_details_json=details)

        cost = self.report()["cost_cohort"]

        self.assertEqual(cost["token_ledger_coverage"], {"partial": 1})
        self.assertEqual(
            cost["token_ledger_entries"],
            {"priced": 1, "incomplete_or_malformed": 1},
        )

    def test_unpriced_siblings_in_pricing_list_prevent_reconciliation(self):
        valid = {"tokenCount": 1, "costPerBatch": 1_000_000_000, "batchSize": 1}
        for sibling in (None, {}, "metadata"):
            with self.subTest(sibling=sibling):
                costs, incomplete = REPORT.token_detail_costs([valid, sibling])
                self.assertEqual(costs, [1_000_000_000])
                self.assertEqual(incomplete, 1)
                row = {
                    "token_details_json": json.dumps([valid, sibling]),
                    "total_nano_aiu": 1_000_000_000,
                }
                status, calculated, priced, malformed = REPORT.reconcile_token_details(row)
                self.assertEqual(status, "partial")
                self.assertEqual(calculated, 1_000_000_000)
                self.assertEqual((priced, malformed), (1, 1))

    def test_task_ledger_preserves_mixed_models_and_unions_parallel_intervals(self):
        self.add_usage(session_id="coordinator", model="synthetic-astra", created_at="2026-09-01T00:00:10Z", duration_ms=10000, input_tokens=100, output_tokens=10, total_nano_aiu=1_000_000_000)
        self.add_usage(session_id="builder", model="synthetic-gemini", created_at="2026-09-01T00:00:10Z", duration_ms=10000, agent_id="worker", input_tokens=200, output_tokens=20, total_nano_aiu=2_000_000_000)
        ledger = self.root / "tasks.json"
        ledger.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "analysis_id": "synthetic-comparison-v1",
                    "cutoff": "2026-09-01T01:00:00Z",
                    "acceptance_boundaries": {"deployment": "deployed-and-live-verified"},
                    "tasks": [
                        {
                            "id": "deploy-1", "label": "Synthetic deployment", "type": "deployment",
                            "start": "2026-09-01T00:00:00Z", "end": "2026-09-01T00:01:00Z",
                            "outcome": "deployed-and-live-verified", "scope_complete": True,
                            "followup_matured": True, "first_pass_gates": True, "reopened": False, "rolled_back": False,
                            "evidence": [{"kind": "synthetic", "ref": "fixture"}],
                            "scopes": [
                                {"session_id": "coordinator", "start": "2026-09-01T00:00:00Z", "end": "2026-09-01T00:01:00Z", "role": "coordinator"},
                                {"session_id": "builder", "start": "2026-09-01T00:00:00Z", "end": "2026-09-01T00:01:00Z", "role": "builder"},
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        report = REPORT.build_task_report(self.db, ledger)
        task = report["tasks"][0]

        self.assertEqual(task["inference_resource_ms"], 20000)
        self.assertEqual(task["request_active_wall_ms"], 10000)
        self.assertEqual([item["model"] for item in task["models"]], ["synthetic-astra", "synthetic-gemini"])
        self.assertEqual(report["portfolio"]["credits_per_accepted_task"], 3.0)
        self.assertEqual(report["portfolio"]["first_pass_gate_rate"], 1.0)

    def test_overlapping_cross_task_scopes_are_rejected(self):
        ledger = self.root / "overlap.json"
        ledger.write_text(
            json.dumps(
                {
                    "schema_version": "1.0", "analysis_id": "overlap-test", "cutoff": "2026-09-01T01:00:00Z",
                    "tasks": [
                        {"id": "a", "label": "A", "type": "test", "outcome": "failed", "scope_complete": True, "evidence": [], "scopes": [{"session_id": "s", "start": "2026-09-01T00:00:00Z", "end": "2026-09-01T00:30:00Z"}]},
                        {"id": "b", "label": "B", "type": "test", "outcome": "unfinished", "scope_complete": False, "evidence": [], "scopes": [{"session_id": "s", "start": "2026-09-01T00:20:00Z", "end": "2026-09-01T00:40:00Z"}]},
                    ],
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(REPORT.ReportUnavailable, "overlapping ownership"):
            REPORT.build_task_report(self.db, ledger)

    def test_incomplete_accepted_task_and_malformed_unknown_cost_fail_closed(self):
        self.add_usage(session_id="s", total_nano_aiu=None, token_details_json="{malformed")
        ledger = self.root / "unknown-cost.json"
        ledger.write_text(
            json.dumps(
                {
                    "schema_version": "1.0", "analysis_id": "unknown-cost-test", "cutoff": "2026-09-02T00:00:00Z",
                    "acceptance_boundaries": {"test": "ci-passed"},
                    "tasks": [
                        {"id": "attempt", "label": "Attempt", "type": "test", "outcome": "ci-passed", "scope_complete": False, "evidence": [],
                         "scopes": [{"session_id": "s", "start": "2026-09-01T00:00:00Z", "end": "2026-09-02T00:00:00Z"}]}
                    ],
                }
            ),
            encoding="utf-8",
        )

        report = REPORT.build_task_report(self.db, ledger)

        self.assertIsNone(report["portfolio"]["credits_per_accepted_task"])
        self.assertEqual(report["portfolio"]["accepted_tasks"], 1)
        self.assertEqual(report["portfolio"]["scope_complete_tasks"], 0)
        self.assertEqual(report["tasks"][0]["unknown_cost_records"], 1)
        self.assertIsNone(report["tasks"][0]["recorded_ai_credits"])
        self.assertIsNone(report["tasks"][0]["models"][0]["recorded_ai_credits"])
        self.assertIsNone(report["portfolio"]["recorded_ai_credits_all_attempts"])
        self.assertIsNone(report["portfolio"]["team_model_resources"][0]["recorded_ai_credits"])

    def test_partial_task_model_and_team_costs_are_observed_subtotals(self):
        self.add_usage(session_id="partial", created_at="2026-09-01T00:10:00Z", total_nano_aiu=1_000_000_000)
        self.add_usage(session_id="partial", created_at="2026-09-01T00:20:00Z", total_nano_aiu=None)
        ledger = self.root / "partial-cost.json"
        ledger.write_text(json.dumps({
            "schema_version": "1.0", "analysis_id": "partial-cost-test", "cutoff": "2026-09-01T01:00:00Z",
            "acceptance_boundaries": {"test": "ci-passed"},
            "tasks": [{
                "id": "partial", "label": "Partial cost", "type": "test", "outcome": "ci-passed", "scope_complete": True,
                "evidence": [], "scopes": [{"session_id": "partial", "start": "2026-09-01T00:00:00Z", "end": "2026-09-01T00:30:00Z"}],
            }],
        }), encoding="utf-8")

        report = REPORT.build_task_report(self.db, ledger)
        task = report["tasks"][0]
        task_type = report["portfolio"]["task_types"][0]
        team = report["portfolio"]["team_model_resources"][0]

        self.assertEqual(task["cost_coverage"], "partial-observed-subtotal")
        self.assertEqual(task["models"][0]["cost_coverage"], "partial-observed-subtotal")
        self.assertEqual(task_type["cost_coverage"], "partial-observed-subtotal")
        self.assertEqual(team["cost_coverage"], "partial-observed-subtotal")
        self.assertEqual(task["recorded_ai_credits"], 1.0)
        self.assertEqual(team["recorded_ai_credits"], 1.0)
        self.assertIsNone(task_type["credits_per_accepted_task"])

    def test_heterogeneous_types_suppress_global_efficiency_and_exclude_not_applicable_cost(self):
        self.add_usage(session_id="deploy", created_at="2026-09-01T00:10:00Z", total_nano_aiu=1_000_000_000)
        self.add_usage(session_id="ci", created_at="2026-09-01T00:20:00Z", total_nano_aiu=2_000_000_000)
        self.add_usage(session_id="excluded", created_at="2026-09-01T00:30:00Z", total_nano_aiu=9_000_000_000)
        ledger = self.root / "heterogeneous.json"
        ledger.write_text(json.dumps({
            "schema_version": "1.0", "analysis_id": "heterogeneous-test", "cutoff": "2026-09-01T01:00:00Z",
            "acceptance_boundaries": {"deployment": "deployed", "ci": "ci-passed"},
            "tasks": [
                {"id": "deploy", "label": "Deploy", "type": "deployment", "outcome": "deployed", "scope_complete": True,
                 "evidence": [], "scopes": [{"session_id": "deploy", "start": "2026-09-01T00:00:00Z", "end": "2026-09-01T00:15:00Z"}]},
                {"id": "ci", "label": "CI", "type": "ci", "outcome": "ci-passed", "scope_complete": True,
                 "evidence": [], "scopes": [{"session_id": "ci", "start": "2026-09-01T00:15:00Z", "end": "2026-09-01T00:25:00Z"}]},
                {"id": "excluded", "label": "Unrelated CI", "type": "ci", "outcome": "ci-passed", "scope_complete": True,
                 "acceptance_applicable": False, "evidence": [], "scopes": [{"session_id": "excluded", "start": "2026-09-01T00:25:00Z", "end": "2026-09-01T00:35:00Z"}]},
            ],
        }), encoding="utf-8")

        portfolio = REPORT.build_task_report(self.db, ledger)["portfolio"]
        by_type = {item["type"]: item for item in portfolio["task_types"]}

        self.assertIsNone(portfolio["credits_per_accepted_task"])
        self.assertEqual(by_type["deployment"]["credits_per_accepted_task"], 1.0)
        self.assertEqual(by_type["ci"]["recorded_ai_credits_all_applicable_attempts"], 2.0)
        self.assertEqual(by_type["ci"]["credits_per_accepted_task"], 2.0)
        self.assertEqual(by_type["ci"]["not_applicable_tasks"], 1)

    def test_adjacent_task_scopes_are_half_open_not_overlapping(self):
        ledger = {
            "schema_version": "1.0", "analysis_id": "boundary-test", "cutoff": "2026-09-01T01:00:00Z",
            "tasks": [
                {"id": "failed", "label": "Failed attempt", "type": "test", "outcome": "failed", "scope_complete": True, "evidence": [],
                 "scopes": [{"session_id": "s", "start": "2026-09-01T00:00:00Z", "end": "2026-09-01T00:30:00Z"}]},
                {"id": "unfinished", "label": "Unfinished attempt", "type": "test", "outcome": "unfinished", "scope_complete": True, "evidence": [],
                 "scopes": [{"session_id": "s", "start": "2026-09-01T00:30:00Z", "end": "2026-09-01T01:00:00Z"}]},
            ],
        }

        tasks = REPORT.normalize_task_ledger(ledger)

        self.assertEqual(len(tasks), 2)

    def test_acceptance_boundary_keeps_deployed_separate_from_live_verified(self):
        self.add_usage(session_id="deployed", created_at="2026-09-01T00:10:00Z", total_nano_aiu=1_000_000_000)
        self.add_usage(session_id="live", created_at="2026-09-01T00:20:00Z", total_nano_aiu=1_000_000_000)
        ledger_path = self.root / "acceptance.json"
        ledger = {
            "schema_version": "1.0", "analysis_id": "acceptance-test", "cutoff": "2026-09-01T01:00:00Z",
            "acceptance_boundaries": {"deployment": "deployed-and-live-verified"},
            "tasks": [
                {"id": "deployed", "label": "Deployed but UI unverified", "type": "deployment", "outcome": "deployed", "scope_complete": True,
                 "evidence": [{"kind": "synthetic", "ref": "deployment"}], "scopes": [{"session_id": "deployed", "start": "2026-09-01T00:00:00Z", "end": "2026-09-01T00:15:00Z"}]},
                {"id": "live", "label": "Live verified", "type": "deployment", "outcome": "deployed-and-live-verified", "scope_complete": True,
                 "evidence": [{"kind": "synthetic", "ref": "live"}], "scopes": [{"session_id": "live", "start": "2026-09-01T00:15:00Z", "end": "2026-09-01T00:30:00Z"}]},
            ],
        }
        ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

        live_report = REPORT.build_task_report(self.db, ledger_path)
        live_type = live_report["portfolio"]["task_types"][0]

        self.assertEqual(live_type["accepted_tasks"], 1)
        self.assertEqual(live_type["acceptance_unknown_tasks"], 1)
        self.assertEqual(live_type["boundary_not_reached_tasks"], 0)
        self.assertEqual(live_type["credits_per_accepted_task"], 2.0)
        self.assertIn("not independently verified", live_report["tasks"][1]["evidence_status"])

        ledger["acceptance_boundaries"]["deployment"] = "deployed"
        ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
        deployed_type = REPORT.build_task_report(self.db, ledger_path)["portfolio"]["task_types"][0]
        self.assertEqual(deployed_type["accepted_tasks"], 1)
        self.assertEqual(deployed_type["acceptance_unknown_tasks"], 1)
        self.assertEqual(deployed_type["credits_per_accepted_task"], 2.0)

        ledger["tasks"][1]["observed_milestones"] = ["deployed"]
        ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
        explicit_deployed = REPORT.build_task_report(self.db, ledger_path)["portfolio"]["task_types"][0]
        self.assertEqual(explicit_deployed["accepted_tasks"], 2)
        self.assertEqual(explicit_deployed["acceptance_unknown_tasks"], 0)
        self.assertEqual(explicit_deployed["credits_per_accepted_task"], 1.0)

    def test_merged_does_not_imply_ci_without_explicit_observed_milestone(self):
        self.add_usage(session_id="merged", total_nano_aiu=1_000_000_000)
        ledger_path = self.root / "merged-no-ci.json"
        ledger = {
            "schema_version": "1.0", "analysis_id": "merged-no-ci", "cutoff": "2026-09-02T00:00:00Z",
            "acceptance_boundaries": {"change": "ci-passed"},
            "tasks": [{
                "id": "merged", "label": "Merged without CI evidence", "type": "change", "outcome": "merged", "scope_complete": True,
                "evidence": [], "scopes": [{"session_id": "merged", "start": "2026-09-01T00:00:00Z", "end": "2026-09-02T00:00:00Z"}],
            }],
        }
        ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

        unknown = REPORT.build_task_report(self.db, ledger_path)["portfolio"]["task_types"][0]

        self.assertEqual(unknown["accepted_tasks"], 0)
        self.assertEqual(unknown["acceptance_unknown_tasks"], 1)
        self.assertIsNone(unknown["credits_per_accepted_task"])

        ledger["tasks"][0]["observed_milestones"] = ["ci-passed"]
        ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
        explicit = REPORT.build_task_report(self.db, ledger_path)["portfolio"]["task_types"][0]
        self.assertEqual(explicit["accepted_tasks"], 1)
        self.assertEqual(explicit["acceptance_unknown_tasks"], 0)
        self.assertEqual(explicit["credits_per_accepted_task"], 1.0)

    def test_task_scope_must_be_contained_in_task_window(self):
        ledger = {
            "schema_version": "1.0", "analysis_id": "window-test", "cutoff": "2026-09-01T01:00:00Z",
            "tasks": [{
                "id": "bad", "label": "Bad window", "type": "test", "outcome": "unfinished", "scope_complete": False,
                "start": "2026-09-01T00:10:00Z", "end": "2026-09-01T00:10:01Z", "evidence": [],
                "scopes": [{"session_id": "s", "start": "2026-09-01T00:00:00Z", "end": "2026-09-01T00:05:00Z"}],
            }],
        }

        with self.assertRaisesRegex(REPORT.ReportUnavailable, "scope outside its task window"):
            REPORT.normalize_task_ledger(ledger)

    def test_same_task_overlapping_scopes_are_rejected(self):
        ledger = {
            "schema_version": "1.0", "analysis_id": "same-task-overlap", "cutoff": "2026-09-01T01:00:00Z",
            "tasks": [{
                "id": "ambiguous", "label": "Ambiguous", "type": "test", "outcome": "unfinished", "scope_complete": False,
                "evidence": [], "scopes": [
                    {"session_id": "s", "start": "2026-09-01T00:00:00Z", "end": "2026-09-01T00:30:00Z"},
                    {"session_id": "s", "start": "2026-09-01T00:20:00Z", "end": "2026-09-01T00:40:00Z"},
                ],
            }],
        }

        with self.assertRaisesRegex(REPORT.ReportUnavailable, "overlapping ownership scopes"):
            REPORT.normalize_task_ledger(ledger)

    def test_mature_followup_missing_flags_stays_unknown(self):
        self.add_usage(session_id="followup", total_nano_aiu=1_000_000_000)
        ledger = self.root / "followup.json"
        ledger.write_text(json.dumps({
            "schema_version": "1.0", "analysis_id": "followup-test", "cutoff": "2026-09-02T00:00:00Z",
            "acceptance_boundaries": {"test": "ci-passed"},
            "tasks": [{
                "id": "followup", "label": "Followup", "type": "test", "outcome": "ci-passed", "scope_complete": True,
                "followup_matured": True, "evidence": [], "scopes": [{"session_id": "followup", "start": "2026-09-01T00:00:00Z", "end": "2026-09-02T00:00:00Z"}],
            }],
        }), encoding="utf-8")

        portfolio = REPORT.build_task_report(self.db, ledger)["portfolio"]

        self.assertEqual(portfolio["followup_matured_tasks"], 1)
        self.assertEqual(portfolio["reopened_explicit_samples"], 0)
        self.assertEqual(portfolio["rollback_explicit_samples"], 0)
        self.assertIsNone(portfolio["reopened_rate_matured_only"])
        self.assertIsNone(portfolio["rollback_rate_matured_only"])

    def test_future_followup_is_not_counted_before_cutoff(self):
        self.add_usage(session_id="future", input_tokens=10, total_nano_aiu=1_000_000_000)
        ledger = self.root / "future-followup.json"
        ledger.write_text(json.dumps({
            "schema_version": "1.0", "analysis_id": "future-followup", "cutoff": "2026-09-02T00:00:00Z",
            "acceptance_boundaries": {"test": "ci-passed"},
            "tasks": [{
                "id": "future", "label": "Future", "type": "test", "outcome": "ci-passed", "scope_complete": True,
                "scope_status": "complete", "scope_attested_at": "2026-09-03T00:00:00Z",
                "followup_matured": True, "reopened": True, "rolled_back": False,
                "followup_observed_at": "2026-09-03T00:00:00Z", "evidence": [],
                "scopes": [{"session_id": "future", "start": "2026-09-01T00:00:00Z", "end": "2026-09-02T00:00:00Z"}],
            }],
        }), encoding="utf-8")

        report = REPORT.build_task_report(self.db, ledger)

        self.assertFalse(report["tasks"][0]["followup_matured"])
        self.assertFalse(report["tasks"][0]["scope_complete"])
        self.assertEqual(report["tasks"][0]["scope_status"], "unknown")
        self.assertIsNone(report["tasks"][0]["scope_attested_at"])
        self.assertEqual(report["portfolio"]["followup_matured_tasks"], 0)
        self.assertEqual(report["portfolio"]["reopened_explicit_samples"], 0)
        self.assertIsNone(report["portfolio"]["credits_per_accepted_task"])

    def test_pilot_strata_separate_mode_class_and_boundary(self):
        for session, minute in (("base-change", 5), ("rug-change", 15), ("base-deploy", 25)):
            self.add_usage(
                session_id=session, created_at=f"2026-09-01T00:{minute:02d}:00Z",
                input_tokens=10, output_tokens=5, total_nano_aiu=1_000_000_000,
            )
        tasks = [
            ("base-change", "baseline", "change", "ci-passed", "2026-09-01T00:00:00Z", "2026-09-01T00:10:00Z"),
            ("rug-change", "bounded-rug", "change", "ci-passed", "2026-09-01T00:10:00Z", "2026-09-01T00:20:00Z"),
            ("base-deploy", "baseline", "deployment", "deployed", "2026-09-01T00:20:00Z", "2026-09-01T00:30:00Z"),
        ]
        ledger = self.root / "strata.json"
        ledger.write_text(json.dumps({
            "schema_version": "1.0", "analysis_id": "strata", "cutoff": "2026-09-01T01:00:00Z",
            "acceptance_boundaries": {"change": "ci-passed", "deployment": "deployed"},
            "tasks": [
                {
                    "id": session, "label": session, "type": task_class, "outcome": outcome,
                    "scope_complete": True, "pilot_mode": mode, "evidence": [],
                    "scopes": [{"session_id": session, "start": start, "end": end}],
                }
                for session, mode, task_class, outcome, start, end in tasks
            ],
        }), encoding="utf-8")

        strata = REPORT.build_task_report(self.db, ledger)["portfolio"]["pilot_strata"]

        self.assertEqual(len(strata), 3)
        self.assertEqual(
            {(item["mode"], item["task_class"], item["acceptance_boundary"]) for item in strata},
            {
                ("baseline", "change", "ci-passed"),
                ("bounded-rug", "change", "ci-passed"),
                ("baseline", "deployment", "deployed"),
            },
        )
        self.assertTrue(all(item["tokens_per_accepted_task"] == 15 for item in strata))
        self.assertTrue(all(item["credits_per_accepted_task"] == 1.0 for item in strata))

    def test_task_scope_normalizes_offsets_and_excludes_exact_end(self):
        self.add_usage(session_id="offset", created_at="2026-09-01T00:30:00Z")
        self.add_usage(session_id="offset", created_at="2026-09-01T01:00:00Z", duration_ms=2000)
        ledger = self.root / "offset-task.json"
        ledger.write_text(
            json.dumps(
                {
                    "schema_version": "1.0", "analysis_id": "offset-test", "cutoff": "2026-09-01T02:00:00Z",
                    "tasks": [
                        {"id": "offset-task", "label": "Offset task", "type": "test", "outcome": "ci-passed", "scope_complete": True, "evidence": [],
                         "scopes": [{"session_id": "offset", "start": "2026-08-31T23:30:00-01:00", "end": "2026-09-01T02:00:00+01:00"}]}
                    ],
                }
            ),
            encoding="utf-8",
        )

        report = REPORT.build_task_report(self.db, ledger)

        self.assertEqual(report["tasks"][0]["calls"], 1)

    def test_input_database_is_unchanged(self):
        self.add_usage()
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()

        self.report()

        after = hashlib.sha256(self.db.read_bytes()).hexdigest()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
