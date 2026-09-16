import hashlib
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
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
    ):
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute(
                """
                INSERT INTO assistant_usage_events (
                    id, session_id, model, output_tokens, duration_ms, created_at,
                    agent_id, api_endpoint, total_nano_aiu
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"id-{session_id}-{created_at}-{duration_ms}", session_id, model,
                    output_tokens, duration_ms, created_at, agent_id, api_endpoint,
                    total_nano_aiu,
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

    def test_input_database_is_unchanged(self):
        self.add_usage()
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()

        self.report()

        after = hashlib.sha256(self.db.read_bytes()).hexdigest()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
