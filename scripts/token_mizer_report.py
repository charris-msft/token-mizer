#!/usr/bin/env python3
"""Read-only Token Mizer throughput, cost, and annotated task reporting."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from math import ceil, isfinite
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable
from urllib.parse import quote

USAGE_TABLE = "assistant_usage_events"
REQUIRED_COLUMNS = {
    "session_id",
    "model",
    "output_tokens",
    "duration_ms",
    "created_at",
    "agent_id",
    "api_endpoint",
}
OPTIONAL_COLUMNS = (
    "input_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "total_nano_aiu",
    "request_multiplier",
    "time_to_first_token_ms",
    "output_ttft_ms",
    "inter_token_latency_ms",
    "reasoning_effort",
    "token_details_json",
)
NANO_AIU_PER_CREDIT = 1_000_000_000
USD_PER_AI_CREDIT = Fraction(1, 100)
TASK_LEDGER_SCHEMA_VERSION = "1.0"
TASK_OUTCOMES = {
    "merged",
    "ci-passed",
    "deployed",
    "deployed-and-live-verified",
    "failed",
    "blocked",
    "unfinished",
}
ACCEPTANCE_BOUNDARIES = {
    "ci-passed",
    "merged",
    "deployed",
    "deployed-and-live-verified",
}


class ReportUnavailable(RuntimeError):
    """Raised when structured reporting data is unavailable or incompatible."""


def parse_timestamp(value: str) -> datetime:
    """Parse SQLite or ISO timestamps and normalize them to UTC."""
    text = str(value).strip()
    if not text:
        raise ValueError("empty timestamp")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def readonly_connect(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ReportUnavailable(f"database not found: {resolved}")
    encoded = quote(resolved.as_posix(), safe="/:")
    try:
        connection = sqlite3.connect(f"file:{encoded}?mode=ro", uri=True)
    except sqlite3.Error as error:
        raise ReportUnavailable(f"cannot open database read-only: {error}") from error
    connection.row_factory = sqlite3.Row
    return connection


def table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}


def validate_usage_schema(connection: sqlite3.Connection) -> set[str]:
    columns = table_columns(connection, USAGE_TABLE)
    if not columns:
        raise ReportUnavailable(f"missing table: {USAGE_TABLE}")
    missing = sorted(REQUIRED_COLUMNS - columns)
    if missing:
        raise ReportUnavailable(f"missing required columns: {', '.join(missing)}")
    return columns


def fetch_usage_rows(
    connection: sqlite3.Connection,
    start: datetime,
    end: datetime,
    model_like: str,
) -> list[sqlite3.Row]:
    """Fetch a date-bounded candidate set with parameterized SQL."""
    if end <= start:
        raise ReportUnavailable("end cutoff must be after start cutoff")
    available = validate_usage_schema(connection)
    coarse_start = (start - timedelta(days=1)).date().isoformat()
    coarse_end = (end + timedelta(days=1)).date().isoformat()
    optional_select = ", ".join(
        column if column in available else f"NULL AS {column}"
        for column in OPTIONAL_COLUMNS
    )
    id_select = "id" if "id" in available else "rowid AS id"
    return list(
        connection.execute(
            f"""
            SELECT {id_select}, session_id, model, output_tokens, duration_ms, created_at,
                   agent_id, api_endpoint, {optional_select}
            FROM {USAGE_TABLE}
            WHERE substr(created_at, 1, 10) BETWEEN ? AND ?
              AND model LIKE ?
            """,
            (coarse_start, coarse_end, model_like),
        )
    )


def normalize_rows(
    rows: Iterable[sqlite3.Row], start: datetime, end: datetime
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    exclusions: defaultdict[str, int] = defaultdict(int)
    normalized: list[dict[str, Any]] = []
    for row in rows:
        try:
            created_at = parse_timestamp(row["created_at"])
        except (TypeError, ValueError):
            exclusions["invalid_timestamp"] += 1
            continue
        if created_at < start or created_at >= end:
            exclusions["outside_exact_window"] += 1
            continue
        item = {key: row[key] for key in row.keys()}
        item["session_id"] = str(item["session_id"])
        item["model"] = str(item["model"])
        item["created_at"] = created_at
        normalized.append(item)
    return normalized, dict(sorted(exclusions.items()))


def qualify_rows(
    rows: Iterable[sqlite3.Row], start: datetime, end: datetime
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    normalized, exclusions = normalize_rows(rows, start, end)
    counts: defaultdict[str, int] = defaultdict(int, exclusions)
    qualified: list[dict[str, Any]] = []
    for row in normalized:
        if row["agent_id"] is not None:
            counts["subagent"] += 1
            continue
        if row["api_endpoint"] is None:
            counts["aggregate_or_missing_endpoint"] += 1
            continue
        output_tokens = row["output_tokens"]
        duration_ms = row["duration_ms"]
        if not isinstance(output_tokens, (int, float)) or output_tokens <= 0:
            counts["nonpositive_output_tokens"] += 1
            continue
        if not isinstance(duration_ms, (int, float)) or duration_ms <= 0:
            counts["nonpositive_duration_ms"] += 1
            continue
        qualified.append(row)
    return qualified, dict(sorted(counts.items()))


def model_basename(model: str) -> str:
    return model.rsplit("/", 1)[-1].casefold()


def selected_model(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("id", "model", "modelId", "value"):
            candidate = value.get(key)
            if isinstance(candidate, str):
                return candidate
    return None


def load_session_selections(
    events_path: Path,
) -> tuple[list[tuple[datetime, str]], list[datetime | None]]:
    selections: list[tuple[datetime, str]] = []
    invalid_events: list[datetime | None] = []
    if not events_path.is_file():
        return selections, invalid_events
    try:
        with events_path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if "session.start" not in line and "session.model_change" not in line:
                    continue
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    invalid_events.append(None)
                    continue
                if not isinstance(event, dict):
                    invalid_events.append(None)
                    continue
                event_type = event.get("type")
                if event_type not in {"session.start", "session.model_change"}:
                    continue
                data = event.get("data")
                if not isinstance(data, dict):
                    invalid_events.append(None)
                    continue
                value = (
                    data.get("selectedModel")
                    if event_type == "session.start"
                    else data.get("newModel")
                )
                model = selected_model(value)
                timestamp_value = event.get("timestamp") or data.get("timestamp")
                try:
                    timestamp = parse_timestamp(timestamp_value)
                except (TypeError, ValueError):
                    invalid_events.append(None)
                    continue
                if not model:
                    invalid_events.append(timestamp)
                    continue
                selections.append((timestamp, model))
    except OSError:
        return [], [None]
    return (
        sorted(selections, key=lambda item: item[0]),
        sorted(invalid_events, key=lambda item: item or datetime.min.replace(tzinfo=timezone.utc)),
    )


def load_provider_catalog(data_db: Path | None) -> dict[str, tuple[str, str]]:
    if data_db is None or not data_db.expanduser().is_file():
        return {}
    with closing(readonly_connect(data_db)) as connection:
        columns = table_columns(connection, "model_providers")
        if not {"id", "name", "type"}.issubset(columns):
            return {}
        return {
            str(row["id"]): (str(row["name"]), str(row["type"]))
            for row in connection.execute("SELECT id, name, type FROM model_providers")
        }


def session_events_path(session_state: Path, session_id: str) -> Path | None:
    if not session_id or session_id in {".", ".."} or "/" in session_id or "\\" in session_id:
        return None
    root = session_state.expanduser().resolve()
    candidate = (root / session_id / "events.jsonl").resolve()
    if root not in candidate.parents:
        return None
    return candidate


def attribute_providers(
    rows: list[dict[str, Any]],
    session_state: Path,
    providers: dict[str, tuple[str, str]],
) -> None:
    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_session[row["session_id"]].append(row)

    for session_id, session_rows in by_session.items():
        events_path = session_events_path(session_state, session_id)
        events, invalid_events = (
            load_session_selections(events_path) if events_path else ([], [None])
        )
        for row in session_rows:
            completion = row["created_at"]
            try:
                request_start = completion - timedelta(milliseconds=row["duration_ms"])
            except (OverflowError, TypeError, ValueError):
                row["provider"] = "unknown"
                continue
            metadata_uncertain = any(
                timestamp is None or timestamp <= completion
                for timestamp in invalid_events
            )
            changed_during_request = any(
                request_start < timestamp <= completion for timestamp, _ in events
            )
            preceding = [
                model for timestamp, model in events if timestamp <= request_start
            ]
            selection = preceding[-1] if preceding else None
            if (
                metadata_uncertain
                or changed_during_request
                or selection is None
                or model_basename(selection) != model_basename(row["model"])
            ):
                row["provider"] = "unknown"
            elif "/" not in selection:
                row["provider"] = (
                    "GitHub billed"
                    if isinstance(row.get("total_nano_aiu"), (int, float))
                    and row["total_nano_aiu"] > 0
                    else "unknown"
                )
            else:
                provider_id = selection.rsplit("/", 1)[0]
                provider = providers.get(provider_id)
                row["provider"] = (
                    f"{provider[0]} ({provider[1]})" if provider else "unknown"
                )


def nearest_rank(values: Iterable[float], percentile: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    return ordered[max(0, ceil(percentile * len(ordered)) - 1)]


def output_stratum(output_tokens: float) -> str:
    if output_tokens <= 256:
        return "1-256"
    if output_tokens <= 1024:
        return "257-1024"
    if output_tokens <= 4096:
        return "1025-4096"
    return "4097+"


def numeric_fraction(value: Any) -> Fraction | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Fraction(str(value))
    except (ValueError, ZeroDivisionError):
        return None


def token_detail_costs(value: Any) -> tuple[list[Fraction], int]:
    costs: list[Fraction] = []
    incomplete_entries = 0
    if isinstance(value, list):
        results = [token_detail_costs(item) for item in value]
        pricing_list = any(item_costs or item_incomplete for item_costs, item_incomplete in results)
        for item_costs, item_incomplete in results:
            costs.extend(item_costs)
            incomplete_entries += item_incomplete
            if pricing_list and not item_costs and item_incomplete == 0:
                incomplete_entries += 1
        return costs, incomplete_entries
    if not isinstance(value, dict):
        return costs, incomplete_entries

    normalized = {str(key).casefold().replace("_", ""): item for key, item in value.items()}
    token_keys = ("tokencount", "tokens", "count", "quantity")
    pricing_keys = {"costperbatch", "batchsize", *token_keys}
    if pricing_keys.intersection(normalized):
        cost = numeric_fraction(normalized.get("costperbatch"))
        batch = numeric_fraction(normalized.get("batchsize"))
        tokens = next(
            (numeric_fraction(normalized[key]) for key in token_keys if key in normalized),
            None,
        )
        if (
            cost is not None
            and batch is not None
            and tokens is not None
            and cost >= 0
            and tokens >= 0
            and batch > 0
        ):
            costs.append(tokens * cost / batch)
        else:
            incomplete_entries += 1
        return costs, incomplete_entries

    for item in value.values():
        item_costs, item_incomplete = token_detail_costs(item)
        costs.extend(item_costs)
        incomplete_entries += item_incomplete
    return costs, incomplete_entries


def reconcile_token_details(
    row: dict[str, Any],
) -> tuple[str, Fraction | None, int, int]:
    raw = row.get("token_details_json")
    if raw in (None, ""):
        return "absent", None, 0, 0
    try:
        details = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return "malformed", None, 0, 1
    costs, incomplete_entries = token_detail_costs(details)
    if not costs:
        return (
            "incomplete" if incomplete_entries else "unpriced",
            None,
            0,
            incomplete_entries,
        )
    calculated = sum(costs, Fraction())
    if incomplete_entries:
        return "partial", calculated, len(costs), incomplete_entries
    recorded = numeric_fraction(row.get("total_nano_aiu"))
    if recorded is None:
        return "calculated_without_recorded_cost", calculated, len(costs), 0
    return (
        "reconciled" if calculated == recorded else "mismatch",
        calculated,
        len(costs),
        0,
    )


def coverage_status(total_count: int, known_count: int) -> str:
    if total_count == 0 or known_count == 0:
        return "unknown"
    return "complete" if known_count == total_count else "partial-observed-subtotal"


def cost_status(records: list[dict[str, Any]], known_count: int) -> str:
    return coverage_status(len(records), known_count)


def valid_duration_ms(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and isfinite(float(value))
        and value > 0
    )


def duration_coverage(records: list[dict[str, Any]]) -> dict[str, int | str]:
    known = sum(valid_duration_ms(row.get("duration_ms")) for row in records)
    return {
        "known": known,
        "unknown_or_invalid": len(records) - known,
        "total": len(records),
        "status": coverage_status(len(records), known),
    }


def cost_summary(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    records = [
        row
        for row in rows
        if row.get("agent_id") is None and row.get("api_endpoint") is not None
    ]
    known = [row for row in records if numeric_fraction(row.get("total_nano_aiu")) is not None]
    total_nano = sum(
        (numeric_fraction(row.get("total_nano_aiu")) or Fraction() for row in known),
        Fraction(),
    )
    reconciliations = [reconcile_token_details(row) for row in records]
    coverage = Counter(item[0] for item in reconciliations)
    priced_entries = sum(item[2] for item in reconciliations)
    incomplete_entries = sum(item[3] for item in reconciliations)
    credits = total_nano / NANO_AIU_PER_CREDIT if known else None
    usd = credits * USD_PER_AI_CREDIT if credits is not None else None
    return {
        "records": len(records),
        "zero_output_records": sum(
            1 for row in records if not isinstance(row.get("output_tokens"), (int, float)) or row["output_tokens"] <= 0
        ),
        "known_cost_records": len(known),
        "unknown_cost_records": len(records) - len(known),
        "cost_coverage": cost_status(records, len(known)),
        "recorded_nano_aiu": (
            int(total_nano) if total_nano.denominator == 1 else str(total_nano)
        ) if known else None,
        "recorded_ai_credits": float(credits) if credits is not None else None,
        "documented_usd_equivalent": float(usd) if usd is not None else None,
        "token_ledger_coverage": dict(sorted(coverage.items())),
        "token_ledger_entries": {
            "priced": priced_entries,
            "incomplete_or_malformed": incomplete_entries,
        },
        "semantics": "Recorded ledger cost only. Partial coverage is an observed subtotal, not a total. One billion nano-AIU equals one AI credit; GitHub documents one AI credit as $0.01 USD. This is not an invoice or a Foundry price.",
    }


def load_task_ledger(path: Path) -> dict[str, Any]:
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ReportUnavailable(f"task ledger not found: {path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise ReportUnavailable(f"invalid task ledger: {error}") from error
    if not isinstance(ledger, dict) or ledger.get("schema_version") != TASK_LEDGER_SCHEMA_VERSION:
        raise ReportUnavailable(
            f"task ledger schema_version must be {TASK_LEDGER_SCHEMA_VERSION}"
        )
    if not isinstance(ledger.get("analysis_id"), str) or not ledger["analysis_id"]:
        raise ReportUnavailable("task ledger requires a nonempty analysis_id")
    if not isinstance(ledger.get("tasks"), list) or not ledger["tasks"]:
        raise ReportUnavailable("task ledger must contain a nonempty tasks array")
    return ledger


def normalize_task_ledger(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        ledger_cutoff = parse_timestamp(ledger["cutoff"])
    except (KeyError, TypeError, ValueError) as error:
        raise ReportUnavailable("task ledger requires a valid UTC cutoff") from error
    acceptance_boundaries = ledger.get("acceptance_boundaries", {})
    if not isinstance(acceptance_boundaries, dict) or any(
        not isinstance(task_type, str)
        or not task_type
        or boundary not in ACCEPTANCE_BOUNDARIES
        for task_type, boundary in acceptance_boundaries.items()
    ):
        raise ReportUnavailable(
            "task ledger acceptance_boundaries must map task types to ci-passed, merged, deployed, or deployed-and-live-verified"
        )
    normalized: list[dict[str, Any]] = []
    ids: set[str] = set()
    for raw in ledger["tasks"]:
        if not isinstance(raw, dict):
            raise ReportUnavailable("each task ledger entry must be an object")
        task_id = raw.get("id")
        if not isinstance(task_id, str) or not task_id or task_id in ids:
            raise ReportUnavailable("task IDs must be nonempty and unique")
        ids.add(task_id)
        if not isinstance(raw.get("label"), str) or not raw["label"]:
            raise ReportUnavailable(f"task {task_id} requires a nonempty label")
        if not isinstance(raw.get("type"), str) or not raw["type"]:
            raise ReportUnavailable(f"task {task_id} requires a nonempty type")
        if not isinstance(raw.get("scope_complete"), bool):
            raise ReportUnavailable(f"task {task_id} requires boolean scope_complete")
        scope_status = raw.get("scope_status", "complete" if raw["scope_complete"] else "unknown")
        if scope_status not in {"unknown", "partial", "complete"}:
            raise ReportUnavailable(f"task {task_id} has invalid scope_status")
        if raw["scope_complete"] != (scope_status == "complete"):
            raise ReportUnavailable(f"task {task_id} scope_complete conflicts with scope_status")
        scope_attested_at = raw.get("scope_attested_at")
        if scope_attested_at is not None:
            try:
                if parse_timestamp(scope_attested_at) > ledger_cutoff:
                    scope_status = "unknown"
                    scope_attested_at = None
            except (TypeError, ValueError) as error:
                raise ReportUnavailable(f"task {task_id} has invalid scope_attested_at") from error
        if "acceptance_applicable" in raw and not isinstance(raw["acceptance_applicable"], bool):
            raise ReportUnavailable(f"task {task_id} acceptance_applicable must be boolean")
        if not isinstance(raw.get("evidence"), list):
            raise ReportUnavailable(f"task {task_id} requires an evidence array")
        outcome = raw.get("outcome")
        if outcome not in TASK_OUTCOMES:
            raise ReportUnavailable(f"task {task_id} has unsupported outcome: {outcome}")
        observed_milestones = raw.get("observed_milestones", [])
        if (
            not isinstance(observed_milestones, list)
            or any(item not in ACCEPTANCE_BOUNDARIES for item in observed_milestones)
            or len(set(observed_milestones)) != len(observed_milestones)
        ):
            raise ReportUnavailable(
                f"task {task_id} observed_milestones must be a unique array of supported acceptance boundaries"
            )
        if outcome in ACCEPTANCE_BOUNDARIES and outcome not in observed_milestones:
            observed_milestones = [*observed_milestones, outcome]
        scopes = raw.get("scopes")
        if not isinstance(scopes, list) or not scopes:
            raise ReportUnavailable(f"task {task_id} requires at least one ownership scope")
        normalized_scopes: list[dict[str, Any]] = []
        for scope in scopes:
            if not isinstance(scope, dict) or not isinstance(scope.get("session_id"), str):
                raise ReportUnavailable(f"task {task_id} has an invalid scope")
            try:
                scope_start = parse_timestamp(scope["start"])
                scope_end = parse_timestamp(scope.get("end", ledger_cutoff.isoformat()))
            except (KeyError, TypeError, ValueError) as error:
                raise ReportUnavailable(f"task {task_id} has an invalid scope cutoff") from error
            if scope_end <= scope_start or scope_end > ledger_cutoff:
                raise ReportUnavailable(f"task {task_id} has a scope outside its cutoff")
            normalized_scopes.append(
                {
                    "session_id": scope["session_id"],
                    "start": scope_start,
                    "end": scope_end,
                    "role": scope.get("role", "unspecified"),
                }
            )
        try:
            task_start = parse_timestamp(
                raw.get("start", min(s["start"] for s in normalized_scopes).isoformat())
            )
            task_end = parse_timestamp(
                raw.get("end", max(s["end"] for s in normalized_scopes).isoformat())
            )
        except (TypeError, ValueError) as error:
            raise ReportUnavailable(f"task {task_id} has an invalid task window") from error
        if task_end <= task_start or task_end > ledger_cutoff:
            raise ReportUnavailable(f"task {task_id} has an invalid task window")
        if any(
            scope["start"] < task_start or scope["end"] > task_end
            for scope in normalized_scopes
        ):
            raise ReportUnavailable(
                f"task {task_id} has an ownership scope outside its task window"
            )
        task_ownership: defaultdict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
        for scope in normalized_scopes:
            for existing_start, existing_end in task_ownership[scope["session_id"]]:
                if scope["start"] < existing_end and existing_start < scope["end"]:
                    raise ReportUnavailable(
                        f"task {task_id} has overlapping ownership scopes for session "
                        f"{scope['session_id']}"
                    )
            task_ownership[scope["session_id"]].append((scope["start"], scope["end"]))
        followup_matured = raw.get("followup_matured") is True
        followup_observed_at = raw.get("followup_observed_at")
        if followup_matured and followup_observed_at is not None:
            try:
                followup_matured = parse_timestamp(followup_observed_at) <= ledger_cutoff
            except (TypeError, ValueError) as error:
                raise ReportUnavailable(f"task {task_id} has invalid followup_observed_at") from error
        bounded = raw.get("bounded_rug")
        acceptance_evidence_valid = True
        if isinstance(bounded, dict) and bounded.get("state") == "accepted":
            verified = bounded.get("verified_target")
            acceptance_evidence_valid = (
                isinstance(verified, dict)
                and isinstance(verified.get("revision"), str)
                and bool(verified["revision"])
                and isinstance(verified.get("environment"), str)
                and bool(verified["environment"])
                and isinstance(verified.get("check"), str)
                and bool(verified["check"])
                and isinstance(verified.get("evidence"), str)
                and bool(verified["evidence"])
                and raw.get("revision") == verified.get("revision")
                and raw.get("environment") == verified.get("environment")
                and any(
                    isinstance(item, dict)
                    and item.get("ref") == verified.get("evidence")
                    and item.get("target_revision") == verified.get("revision")
                    and item.get("target_environment") == verified.get("environment")
                    for item in raw["evidence"]
                )
            )
        normalized.append(
            {
                **raw,
                "id": task_id,
                "outcome": outcome,
                "observed_milestones": observed_milestones,
                "start": task_start,
                "end": task_end,
                "scopes": normalized_scopes,
                "scope_complete": scope_status == "complete",
                "scope_status": scope_status,
                "scope_attested_at": scope_attested_at,
                "acceptance_applicable": raw.get("acceptance_applicable", True) is True,
                "acceptance_evidence_valid": acceptance_evidence_valid,
                "followup_matured": followup_matured,
                "reopened": raw.get("reopened") if followup_matured else None,
                "rolled_back": raw.get("rolled_back") if followup_matured else None,
            }
        )

    ownership: defaultdict[str, list[tuple[datetime, datetime, str]]] = defaultdict(list)
    for task in normalized:
        for scope in task["scopes"]:
            for existing_start, existing_end, existing_task in ownership[scope["session_id"]]:
                if existing_task != task["id"] and scope["start"] < existing_end and existing_start < scope["end"]:
                    raise ReportUnavailable(
                        f"overlapping ownership scopes for session {scope['session_id']}: "
                        f"{existing_task} and {task['id']}"
                    )
            ownership[scope["session_id"]].append((scope["start"], scope["end"], task["id"]))
    return normalized


def union_duration_ms(intervals: Iterable[tuple[datetime, datetime]]) -> float:
    ordered = sorted((start, end) for start, end in intervals if end > start)
    if not ordered:
        return 0.0
    total = 0.0
    current_start, current_end = ordered[0]
    for start, end in ordered[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
        else:
            total += (current_end - current_start).total_seconds() * 1000.0
            current_start, current_end = start, end
    return total + (current_end - current_start).total_seconds() * 1000.0


def task_rows_for_scopes(
    rows: Iterable[dict[str, Any]], scopes: list[dict[str, Any]]
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    matched: dict[Any, tuple[dict[str, Any], dict[str, Any]]] = {}
    for row in rows:
        if row.get("api_endpoint") is None:
            continue
        for scope in scopes:
            if row["session_id"] == scope["session_id"] and scope["start"] <= row["created_at"] < scope["end"]:
                matched[row.get("id", id(row))] = (row, scope)
                break
    return list(matched.values())


def summarize_task(task: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    matched = task_rows_for_scopes(rows, task["scopes"])
    leaf_rows = [row for row, _ in matched]
    durations = [
        float(row["duration_ms"])
        for row in leaf_rows
        if valid_duration_ms(row.get("duration_ms"))
    ]
    intervals: list[tuple[datetime, datetime]] = []
    for row, scope in matched:
        duration = row.get("duration_ms")
        if not valid_duration_ms(duration):
            continue
        request_end = row["created_at"]
        request_start = request_end - timedelta(milliseconds=float(duration))
        intervals.append((max(request_start, scope["start"]), min(request_end, scope["end"])))

    by_model: list[dict[str, Any]] = []
    for model in sorted({row["model"] for row in leaf_rows}):
        model_rows = [row for row in leaf_rows if row["model"] == model]
        known_costs = [
            numeric_fraction(row.get("total_nano_aiu"))
            for row in model_rows
            if numeric_fraction(row.get("total_nano_aiu")) is not None
        ]
        model_nano = sum((cost for cost in known_costs if cost is not None), Fraction())
        model_credits = model_nano / NANO_AIU_PER_CREDIT if known_costs else None
        by_model.append(
            {
                "model": model,
                "calls": len(model_rows),
                "sessions": len({row["session_id"] for row in model_rows}),
                "input_tokens": sum(
                    int(row["input_tokens"])
                    for row in model_rows
                    if isinstance(row.get("input_tokens"), (int, float))
                ),
                "output_tokens": sum(
                    int(row["output_tokens"])
                    for row in model_rows
                    if isinstance(row.get("output_tokens"), (int, float))
                ),
                "inference_resource_ms": sum(
                    float(row["duration_ms"])
                    for row in model_rows
                    if valid_duration_ms(row.get("duration_ms"))
                ),
                "duration_coverage": duration_coverage(model_rows),
                "known_cost_records": len(known_costs),
                "unknown_cost_records": len(model_rows) - len(known_costs),
                "cost_coverage": cost_status(model_rows, len(known_costs)),
                "recorded_ai_credits": (
                    float(model_credits) if model_credits is not None else None
                ),
            }
        )

    by_role: list[dict[str, Any]] = []
    for role in sorted({scope["role"] for _, scope in matched}):
        role_rows = [row for row, scope in matched if scope["role"] == role]
        role_costs = [
            numeric_fraction(row.get("total_nano_aiu"))
            for row in role_rows
            if numeric_fraction(row.get("total_nano_aiu")) is not None
        ]
        role_nano = sum((cost for cost in role_costs if cost is not None), Fraction())
        role_credits = role_nano / NANO_AIU_PER_CREDIT if role_costs else None
        by_role.append(
            {
                "role": role,
                "calls": len(role_rows),
                "input_tokens": sum(
                    int(row["input_tokens"])
                    for row in role_rows
                    if isinstance(row.get("input_tokens"), (int, float))
                ),
                "output_tokens": sum(
                    int(row["output_tokens"])
                    for row in role_rows
                    if isinstance(row.get("output_tokens"), (int, float))
                ),
                "model_active_ms": sum(
                    float(row["duration_ms"])
                    for row in role_rows
                    if valid_duration_ms(row.get("duration_ms"))
                ),
                "duration_coverage": duration_coverage(role_rows),
                "known_cost_records": len(role_costs),
                "unknown_cost_records": len(role_rows) - len(role_costs),
                "cost_coverage": cost_status(role_rows, len(role_costs)),
                "recorded_ai_credits": float(role_credits) if role_credits is not None else None,
            }
        )

    known_costs = [
        numeric_fraction(row.get("total_nano_aiu"))
        for row in leaf_rows
        if numeric_fraction(row.get("total_nano_aiu")) is not None
    ]
    input_tokens = [
        int(row["input_tokens"])
        for row in leaf_rows
        if isinstance(row.get("input_tokens"), (int, float))
    ]
    output_tokens = [
        int(row["output_tokens"])
        for row in leaf_rows
        if isinstance(row.get("output_tokens"), (int, float))
    ]
    total_nano = sum((cost for cost in known_costs if cost is not None), Fraction())
    task_credits = total_nano / NANO_AIU_PER_CREDIT if known_costs else None
    return {
        "id": task["id"],
        "label": task.get("label", task["id"]),
        "type": task.get("type", "unspecified"),
        "outcome": task["outcome"],
        "observed_milestones": task["observed_milestones"],
        "scope_complete": task["scope_complete"],
        "scope_status": task["scope_status"],
        "scope_attested_at": task.get("scope_attested_at"),
        "acceptance_applicable": task["acceptance_applicable"],
        "acceptance_evidence_valid": task["acceptance_evidence_valid"],
        "revision": task.get("revision"),
        "environment": task.get("environment"),
        "pilot_mode": task.get("pilot_mode", "unspecified"),
        "bounded_rug": task.get("bounded_rug"),
        "annotation_window": {
            "start": task["start"].isoformat(),
            "end": task["end"].isoformat(),
            "elapsed_ms": (task["end"] - task["start"]).total_seconds() * 1000.0,
        },
        "evidence_references": task.get("evidence", []),
        "evidence_status": "user-supplied annotations; references and outcomes were not independently verified",
        "calls": len(leaf_rows),
        "sessions": len({row["session_id"] for row in leaf_rows}),
        "input_tokens": sum(input_tokens),
        "output_tokens": sum(output_tokens),
        "input_token_records": len(input_tokens),
        "output_token_records": len(output_tokens),
        "token_coverage": {
            "input": "unknown" if not input_tokens else ("complete" if len(input_tokens) == len(leaf_rows) else "partial"),
            "output": "unknown" if not output_tokens else ("complete" if len(output_tokens) == len(leaf_rows) else "partial"),
        },
        "inference_resource_ms": sum(durations),
        "duration_coverage": duration_coverage(leaf_rows),
        "request_active_wall_ms": union_duration_ms(intervals),
        "request_duration_ms": {
            "median": median(durations) if durations else None,
            "p90_nearest_rank": nearest_rank(durations, 0.90),
        },
        "known_cost_records": len(known_costs),
        "unknown_cost_records": len(leaf_rows) - len(known_costs),
        "cost_coverage": cost_status(leaf_rows, len(known_costs)),
        "recorded_ai_credits": (
            float(task_credits) if task_credits is not None else None
        ),
        "documented_usd_equivalent": (
            float(task_credits * USD_PER_AI_CREDIT)
            if task_credits is not None
            else None
        ),
        "models": by_model,
        "roles": by_role,
        "first_pass_gates": task.get("first_pass_gates"),
        "reopened": task.get("reopened") if task.get("followup_matured") is True else None,
        "rolled_back": task.get("rolled_back") if task.get("followup_matured") is True else None,
        "followup_matured": task.get("followup_matured") is True,
    }


def task_reached_boundary(task: dict[str, Any], boundary: str | None) -> bool:
    if (
        boundary is None
        or task["outcome"] in {"failed", "blocked", "unfinished"}
        or task.get("acceptance_evidence_valid") is False
    ):
        return False
    return task["outcome"] == boundary or boundary in task["observed_milestones"]


def build_task_report(db_path: Path, ledger_path: Path) -> dict[str, Any]:
    ledger = load_task_ledger(ledger_path)
    tasks = normalize_task_ledger(ledger)
    start = min(scope["start"] for task in tasks for scope in task["scopes"])
    end = max(scope["end"] for task in tasks for scope in task["scopes"])
    with closing(readonly_connect(db_path)) as connection:
        candidates = fetch_usage_rows(connection, start, end, "%")
    rows, exclusions = normalize_rows(candidates, start, end)
    summaries = [summarize_task(task, rows) for task in tasks]
    acceptance_boundaries = ledger.get("acceptance_boundaries", {})
    known_task_credits = [
        item["recorded_ai_credits"]
        for item in summaries
        if item["recorded_ai_credits"] is not None
    ]
    total_credits = sum(known_task_credits) if known_task_credits else None
    first_pass = [item["first_pass_gates"] for item in summaries if isinstance(item["first_pass_gates"], bool)]
    matured = [item for item in summaries if item["followup_matured"]]
    reopened_samples = [item["reopened"] for item in matured if isinstance(item["reopened"], bool)]
    rollback_samples = [item["rolled_back"] for item in matured if isinstance(item["rolled_back"], bool)]
    elapsed = [item["annotation_window"]["elapsed_ms"] for item in summaries]
    live_elapsed = [
        item["annotation_window"]["elapsed_ms"]
        for item in summaries
        if item["outcome"] == "deployed-and-live-verified"
    ]
    task_types: list[dict[str, Any]] = []
    for task_type in sorted({item["type"] for item in summaries}):
        members = [item for item in summaries if item["type"] == task_type]
        applicable = [item for item in members if item["acceptance_applicable"]]
        boundary = acceptance_boundaries.get(task_type)
        member_accepted = sum(task_reached_boundary(item, boundary) for item in applicable)
        acceptance_unknown = len(applicable) - member_accepted
        member_complete = all(
            item["scope_complete"] and item["calls"] > 0 for item in applicable
        )
        member_cost_known = all(item["unknown_cost_records"] == 0 for item in applicable)
        member_known_credits = [
            item["recorded_ai_credits"]
            for item in applicable
            if item["recorded_ai_credits"] is not None
        ]
        member_calls = sum(item["calls"] for item in applicable)
        member_known_cost_records = sum(item["known_cost_records"] for item in applicable)
        member_cost_coverage = (
            "unknown"
            if member_calls == 0 or member_known_cost_records == 0
            else "complete"
            if member_known_cost_records == member_calls
            else "partial-observed-subtotal"
        )
        member_credits = sum(member_known_credits) if member_known_credits else None
        member_cost_per = (
            member_credits / member_accepted
            if boundary
            and member_accepted
            and applicable
            and member_complete
            and member_cost_known
            and member_credits is not None
            else None
        )
        member_elapsed = [item["annotation_window"]["elapsed_ms"] for item in members]
        task_types.append(
            {
                "type": task_type,
                "acceptance_boundary": boundary,
                "tasks": len(members),
                "attempted_tasks": len(applicable),
                "not_applicable_tasks": len(members) - len(applicable),
                "accepted_tasks": member_accepted,
                "boundary_not_reached_tasks": 0,
                "failed_tasks": sum(item["outcome"] == "failed" for item in applicable),
                "blocked_tasks": sum(item["outcome"] == "blocked" for item in applicable),
                "unfinished_tasks": sum(item["outcome"] == "unfinished" for item in applicable),
                "acceptance_unknown_tasks": acceptance_unknown,
                "elapsed_ms_median": median(member_elapsed),
                "elapsed_ms_p90_nearest_rank": nearest_rank(member_elapsed, 0.90),
                "recorded_ai_credits_all_applicable_attempts": member_credits,
                "known_cost_records": member_known_cost_records,
                "unknown_cost_records": member_calls - member_known_cost_records,
                "cost_coverage": member_cost_coverage,
                "credits_per_accepted_task": member_cost_per,
                "credits_per_accepted_task_unavailable_reason": None
                if member_cost_per is not None
                else "requires an explicit boundary, at least one accepted task, complete nonempty applicable scopes, and known recorded cost for every owned leaf call",
            }
        )
    portfolio_cost_per = task_types[0]["credits_per_accepted_task"] if len(task_types) == 1 else None
    accepted = sum(item["accepted_tasks"] for item in task_types)
    pilot_modes = []
    for mode in sorted({item["pilot_mode"] for item in summaries}):
        members = [item for item in summaries if item["pilot_mode"] == mode]
        calls = sum(item["calls"] for item in members)
        known_cost_records = sum(item["known_cost_records"] for item in members)
        known_credits = [
            item["recorded_ai_credits"]
            for item in members
            if item["recorded_ai_credits"] is not None
        ]
        elapsed_ms = [item["annotation_window"]["elapsed_ms"] for item in members]
        known_duration_records = sum(
            item["duration_coverage"]["known"] for item in members
        )
        matured_members = [item for item in members if item["followup_matured"]]
        reopened_members = [item["reopened"] for item in matured_members if isinstance(item["reopened"], bool)]
        rollback_members = [item["rolled_back"] for item in matured_members if isinstance(item["rolled_back"], bool)]
        role_names = sorted(
            {role["role"] for item in members for role in item.get("roles", [])}
        )
        roles = []
        for role_name in role_names:
            role_members = [
                role
                for item in members
                for role in item.get("roles", [])
                if role["role"] == role_name
            ]
            role_calls = sum(role["calls"] for role in role_members)
            role_known_durations = sum(
                role["duration_coverage"]["known"] for role in role_members
            )
            role_known_costs = sum(role["known_cost_records"] for role in role_members)
            role_credits = [
                role["recorded_ai_credits"]
                for role in role_members
                if role["recorded_ai_credits"] is not None
            ]
            roles.append(
                {
                    "role": role_name,
                    "calls": role_calls,
                    "input_tokens": sum(role["input_tokens"] for role in role_members),
                    "output_tokens": sum(role["output_tokens"] for role in role_members),
                    "model_active_ms": sum(role["model_active_ms"] for role in role_members),
                    "duration_coverage": {
                        "known": role_known_durations,
                        "unknown_or_invalid": role_calls - role_known_durations,
                        "total": role_calls,
                        "status": coverage_status(role_calls, role_known_durations),
                    },
                    "known_cost_records": role_known_costs,
                    "unknown_cost_records": role_calls - role_known_costs,
                    "cost_coverage": cost_status([{}] * role_calls, role_known_costs),
                    "recorded_ai_credits": sum(role_credits) if role_credits else None,
                }
            )
        pilot_modes.append(
            {
                "mode": mode,
                "tasks": len(members),
                "task_classes": sorted({item["type"] for item in members}),
                "acceptance_boundaries": sorted(
                    {
                        acceptance_boundaries[item["type"]]
                        for item in members
                        if item["type"] in acceptance_boundaries
                    }
                ),
                "accepted_tasks": sum(
                    task_reached_boundary(item, acceptance_boundaries.get(item["type"]))
                    for item in members
                    if item["acceptance_applicable"]
                ),
                "calls": calls,
                "input_tokens": sum(item["input_tokens"] for item in members),
                "output_tokens": sum(item["output_tokens"] for item in members),
                "token_record_coverage": {
                    "input": {
                        "known": sum(item["input_token_records"] for item in members),
                        "total": calls,
                    },
                    "output": {
                        "known": sum(item["output_token_records"] for item in members),
                        "total": calls,
                    },
                },
                "known_cost_records": known_cost_records,
                "unknown_cost_records": calls - known_cost_records,
                "cost_coverage": cost_status([{}] * calls, known_cost_records),
                "recorded_ai_credits": sum(known_credits) if known_credits else None,
                "annotated_elapsed_ms": {
                    "sum": sum(elapsed_ms),
                    "median": median(elapsed_ms) if elapsed_ms else None,
                    "p90_nearest_rank": nearest_rank(elapsed_ms, 0.90),
                },
                "model_active_ms": sum(item["inference_resource_ms"] for item in members),
                "duration_coverage": {
                    "known": known_duration_records,
                    "unknown_or_invalid": calls - known_duration_records,
                    "total": calls,
                    "status": coverage_status(calls, known_duration_records),
                },
                "request_active_wall_ms": sum(item["request_active_wall_ms"] for item in members),
                "roles": roles,
                "build_attempts": sum(
                    (item.get("bounded_rug") or {}).get("build_attempts", 0) for item in members
                ),
                "repair_attempts": sum(
                    (item.get("bounded_rug") or {}).get("repair_attempts", 0) for item in members
                ),
                "verifications": sum(
                    (item.get("bounded_rug") or {}).get("verifications", 0) for item in members
                ),
                "followup_matured_tasks": len(matured_members),
                "reopened_explicit_samples": len(reopened_members),
                "reopened_tasks": sum(reopened_members),
                "rollback_explicit_samples": len(rollback_members),
                "rolled_back_tasks": sum(rollback_members),
                "comparison_notice": "Compare only identical task classes and acceptance boundaries; observational results do not establish causality.",
            }
        )
    pilot_strata = []
    strata_keys = sorted(
        {
            (item["pilot_mode"], item["type"], acceptance_boundaries.get(item["type"]))
            for item in summaries
        },
        key=lambda value: (value[0], value[1], value[2] or ""),
    )
    for mode, task_class, boundary in strata_keys:
        members = [
            item for item in summaries
            if item["pilot_mode"] == mode and item["type"] == task_class
        ]
        applicable = [item for item in members if item["acceptance_applicable"]]
        accepted_count = sum(task_reached_boundary(item, boundary) for item in applicable)
        calls = sum(item["calls"] for item in applicable)
        input_known = sum(item["input_token_records"] for item in applicable)
        output_known = sum(item["output_token_records"] for item in applicable)
        known_costs = sum(item["known_cost_records"] for item in applicable)
        known_durations = sum(
            item["duration_coverage"]["known"] for item in applicable
        )
        scopes_complete = bool(applicable) and all(
            item["scope_complete"] and item["calls"] > 0 for item in applicable
        )
        token_coverage_complete = calls > 0 and input_known == calls and output_known == calls
        duration_coverage_complete = calls > 0 and known_durations == calls
        cost_coverage_complete = calls > 0 and known_costs == calls
        tokens = sum(item["input_tokens"] + item["output_tokens"] for item in applicable)
        credits_values = [
            item["recorded_ai_credits"]
            for item in applicable
            if item["recorded_ai_credits"] is not None
        ]
        credits = sum(credits_values) if credits_values else None
        elapsed_total = sum(item["annotation_window"]["elapsed_ms"] for item in applicable)
        active_total = sum(item["inference_resource_ms"] for item in applicable)
        ratios_available = accepted_count > 0 and scopes_complete
        base_ratio_reason = (
            None
            if ratios_available
            else "requires at least one accepted task and explicit complete nonempty scopes"
        )
        token_ratio_reason = base_ratio_reason or (
            None if token_coverage_complete else "requires complete input and output token coverage"
        )
        model_active_ratio_reason = base_ratio_reason or (
            None if duration_coverage_complete else "requires finite positive duration coverage for every owned call"
        )
        credit_ratio_reason = base_ratio_reason or (
            None if cost_coverage_complete else "requires complete relevant cost coverage"
        )
        matured_members = [item for item in applicable if item["followup_matured"]]
        pilot_strata.append(
            {
                "mode": mode,
                "task_class": task_class,
                "acceptance_boundary": boundary,
                "tasks": len(members),
                "attempted_tasks": len(applicable),
                "accepted_tasks": accepted_count,
                "failed_tasks": sum(item["outcome"] == "failed" for item in applicable),
                "blocked_tasks": sum(item["outcome"] == "blocked" for item in applicable),
                "unfinished_tasks": sum(item["outcome"] == "unfinished" for item in applicable),
                "calls": calls,
                "input_tokens": sum(item["input_tokens"] for item in applicable),
                "output_tokens": sum(item["output_tokens"] for item in applicable),
                "token_record_coverage": {
                    "input": {"known": input_known, "total": calls},
                    "output": {"known": output_known, "total": calls},
                },
                "known_cost_records": known_costs,
                "unknown_cost_records": calls - known_costs,
                "cost_coverage": cost_status([{}] * calls, known_costs),
                "recorded_ai_credits_all_attempts": credits,
                "annotated_elapsed_ms_all_attempts": elapsed_total,
                "model_active_ms_all_attempts": active_total,
                "duration_coverage": {
                    "known": known_durations,
                    "unknown_or_invalid": calls - known_durations,
                    "total": calls,
                    "status": coverage_status(calls, known_durations),
                },
                "build_attempts": sum((item.get("bounded_rug") or {}).get("build_attempts", 0) for item in applicable),
                "repair_attempts": sum((item.get("bounded_rug") or {}).get("repair_attempts", 0) for item in applicable),
                "verifications": sum((item.get("bounded_rug") or {}).get("verifications", 0) for item in applicable),
                "followup_matured_tasks": len(matured_members),
                "reopened_explicit_samples": sum(isinstance(item["reopened"], bool) for item in matured_members),
                "reopened_tasks": sum(item["reopened"] is True for item in matured_members),
                "tokens_per_accepted_task": tokens / accepted_count
                if token_ratio_reason is None else None,
                "tokens_ratio_unavailable_reason": token_ratio_reason,
                "elapsed_ms_per_accepted_task": elapsed_total / accepted_count
                if base_ratio_reason is None else None,
                "elapsed_ratio_unavailable_reason": base_ratio_reason,
                "model_active_ms_per_accepted_task": active_total / accepted_count
                if model_active_ratio_reason is None else None,
                "model_active_ratio_unavailable_reason": model_active_ratio_reason,
                "credits_per_accepted_task": credits / accepted_count
                if credit_ratio_reason is None and credits is not None else None,
                "credits_ratio_unavailable_reason": credit_ratio_reason,
                "ratio_unavailable_reason": None
                if all(
                    reason is None
                    for reason in (token_ratio_reason, base_ratio_reason, model_active_ratio_reason, credit_ratio_reason)
                )
                else "see metric-specific unavailable reasons",
                "comparison_notice": "Compare only the same task class and acceptance boundary; observational results do not establish causality or savings.",
            }
        )
    team_models: defaultdict[str, dict[str, float | int]] = defaultdict(
        lambda: {
            "calls": 0,
            "inference_resource_ms": 0.0,
            "known_cost_records": 0,
            "unknown_cost_records": 0,
            "recorded_ai_credits_observed": 0.0,
        }
    )
    for item in summaries:
        for model in item["models"]:
            values = team_models[model["model"]]
            values["calls"] += model["calls"]
            values["inference_resource_ms"] += model["inference_resource_ms"]
            values["known_cost_records"] += model["known_cost_records"]
            values["unknown_cost_records"] += model["unknown_cost_records"]
            if model["recorded_ai_credits"] is not None:
                values["recorded_ai_credits_observed"] += model["recorded_ai_credits"]
    team_resources: list[dict[str, Any]] = []
    for model, values in sorted(team_models.items()):
        known_count = int(values["known_cost_records"])
        unknown_count = int(values["unknown_cost_records"])
        team_resources.append(
            {
                "model": model,
                "calls": int(values["calls"]),
                "inference_resource_ms": values["inference_resource_ms"],
                "known_cost_records": known_count,
                "unknown_cost_records": unknown_count,
                "cost_coverage": (
                    "unknown"
                    if known_count == 0
                    else "complete"
                    if unknown_count == 0
                    else "partial-observed-subtotal"
                ),
                "recorded_ai_credits": (
                    values["recorded_ai_credits_observed"] if known_count else None
                ),
            }
        )
    return {
        "schema_version": TASK_LEDGER_SCHEMA_VERSION,
        "analysis_id": ledger.get("analysis_id"),
        "cutoff": parse_timestamp(ledger["cutoff"]).isoformat(),
        "source": "assistant_usage_events (read-only) plus user-supplied task ledger",
        "provenance": "annotation-driven calculation; task boundaries, outcomes, and evidence references are not independently verified",
        "candidate_records": len(candidates),
        "excluded_records": exclusions,
        "tasks": summaries,
        "portfolio": {
            "tasks": len(summaries),
            "attempted_tasks": sum(item["acceptance_applicable"] for item in summaries),
            "not_applicable_tasks": sum(not item["acceptance_applicable"] for item in summaries),
            "accepted_tasks": accepted,
            "acceptance_unknown_tasks": sum(item["acceptance_unknown_tasks"] for item in task_types),
            "boundary_not_reached_tasks": sum(item["boundary_not_reached_tasks"] for item in task_types),
            "failed_tasks": sum(item["outcome"] == "failed" for item in summaries),
            "blocked_tasks": sum(item["outcome"] == "blocked" for item in summaries),
            "unfinished_tasks": sum(item["outcome"] == "unfinished" for item in summaries),
            "scope_complete_tasks": sum(item["scope_complete"] for item in summaries),
            "tasks_with_evidence_references": sum(bool(item["evidence_references"]) for item in summaries),
            "task_elapsed_ms": {
                "median": median(elapsed),
                "p90_nearest_rank": nearest_rank(elapsed, 0.90),
            },
            "verified_live_elapsed_ms": {
                "samples": len(live_elapsed),
                "median": median(live_elapsed) if live_elapsed else None,
                "p90_nearest_rank": nearest_rank(live_elapsed, 0.90),
            },
            "tool_time_ms": None,
            "tool_time_unavailable_reason": "assistant_usage_events does not contain task-scoped tool duration; supply a separate evidence source rather than infer it by subtraction",
            "recorded_ai_credits_all_attempts": total_credits,
            "recorded_cost_coverage": (
                "unknown"
                if not known_task_credits
                else "complete"
                if all(item["unknown_cost_records"] == 0 for item in summaries)
                else "partial-observed-subtotal"
            ),
            "credits_per_accepted_task": portfolio_cost_per,
            "credits_per_accepted_task_unavailable_reason": None
            if portfolio_cost_per is not None
            else "portfolio efficiency is only reported for one task type with its explicit acceptance boundary and complete cost/scope coverage; use task_types otherwise",
            "first_pass_gate_rate": sum(first_pass) / len(first_pass) if first_pass else None,
            "first_pass_gate_samples": len(first_pass),
            "followup_matured_tasks": len(matured),
            "reopened_rate_matured_only": sum(reopened_samples) / len(reopened_samples) if reopened_samples else None,
            "reopened_explicit_samples": len(reopened_samples),
            "rollback_rate_matured_only": sum(rollback_samples) / len(rollback_samples) if rollback_samples else None,
            "rollback_explicit_samples": len(rollback_samples),
            "task_types": task_types,
            "pilot_modes": pilot_modes,
            "pilot_strata": pilot_strata,
            "team_model_resources": team_resources,
        },
        "definitions": {
            "task_elapsed": "User-annotated task start to end or cutoff; not inferred from session lifetime.",
            "inference_resource_ms": "Observed subtotal of finite positive matching request durations, including concurrent workers; pilot-mode summaries label the same quantity model_active_ms and expose duration coverage.",
            "request_active_wall_ms": "Union of matching request intervals; excludes unobserved human idle and CI/tool waiting.",
            "cost": "Recorded leaf-call nano-AIU only; endpoint-null aggregate rows are excluded, partial coverage is an observed subtotal, and all-unknown cost stays null.",
            "acceptance": "Each comparable task type requires an explicit acceptance boundary. Earlier successful states do not satisfy a stricter boundary, and evidence remains user-supplied annotation.",
        },
    }


def aggregate(rows: list[dict[str, Any]], provider_mode: bool) -> list[dict[str, Any]]:
    groups: defaultdict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        provider = row.get("provider", "not requested") if provider_mode else "not requested"
        groups[(row["model"], provider)].append(row)

    results: list[dict[str, Any]] = []
    for (model, provider), group in sorted(groups.items()):
        total_output = sum(row["output_tokens"] for row in group)
        total_duration = sum(row["duration_ms"] for row in group)
        per_call_tpm = [
            60000.0 * row["output_tokens"] / row["duration_ms"] for row in group
        ]
        session_counts = Counter(row["session_id"] for row in group)
        timing_fields = (
            "time_to_first_token_ms",
            "output_ttft_ms",
            "inter_token_latency_ms",
        )
        reasoning = Counter(
            str(row["reasoning_effort"])
            for row in group
            if row.get("reasoning_effort") not in (None, "")
        )
        strata: list[dict[str, Any]] = []
        for name in ("1-256", "257-1024", "1025-4096", "4097+"):
            members = [row for row in group if output_stratum(row["output_tokens"]) == name]
            if members:
                strata.append(
                    {
                        "output_tokens": name,
                        "calls": len(members),
                        "mean_duration_ms": mean(row["duration_ms"] for row in members),
                        "pooled_output_tpm": 60000.0
                        * sum(row["output_tokens"] for row in members)
                        / sum(row["duration_ms"] for row in members),
                    }
                )
        results.append(
            {
                "model": model,
                "provider": provider,
                "calls": len(group),
                "sessions": len(session_counts),
                "calls_per_session": {
                    "minimum": min(session_counts.values()),
                    "median": median(session_counts.values()),
                    "maximum": max(session_counts.values()),
                },
                "observed_at": {
                    "first": min(row["created_at"] for row in group).isoformat(),
                    "last": max(row["created_at"] for row in group).isoformat(),
                },
                "output_tokens": int(total_output),
                "mean_output_tokens": mean(row["output_tokens"] for row in group),
                "duration_ms": int(total_duration),
                "request_duration_ms": {
                    "mean": mean(row["duration_ms"] for row in group),
                    "median": median(row["duration_ms"] for row in group),
                    "p90_nearest_rank": nearest_rank(
                        (row["duration_ms"] for row in group), 0.90
                    ),
                },
                "timing_field_coverage": {
                    field: sum(1 for row in group if row.get(field) is not None)
                    for field in timing_fields
                },
                "reasoning_effort": dict(sorted(reasoning.items())),
                "output_length_strata": strata,
                "mean_per_call_output_tpm": sum(per_call_tpm) / len(per_call_tpm),
                "pooled_overall_output_tpm": 60000.0 * total_output / total_duration,
                "mean_per_call_output_tok_s": sum(per_call_tpm) / len(per_call_tpm) / 60.0,
                "pooled_overall_output_tok_s": total_output / (total_duration / 1000.0),
            }
        )
    return results


def ingest_rug_ledger(path: Path, start: str, end: str) -> dict[str, Any]:
    """Ingest bounded-RUG export without inventing provider matches."""
    try: data=json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error: raise ReportUnavailable(f"invalid RUG ledger: {error}") from error
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list): raise ReportUnavailable("RUG ledger requires tasks")
    lo, hi=parse_timestamp(start), parse_timestamp(end); matched=[]; assignments=[]
    for task in data["tasks"]:
        if not isinstance(task, dict): continue
        rug=task.get("bounded_rug") or {}
        for item in rug.get("provider_model_observations", []):
            if not isinstance(item, dict): continue
            try: at=parse_timestamp(item.get("at"))
            except (TypeError, ValueError): continue
            if lo <= at < hi and item.get("provider") and item.get("model") and item.get("status"):
                matched.append({"task_id":task.get("id"), **{k:item[k] for k in ("role","provider","model","status","at")}})
        for item in rug.get("model_assignments", []):
            if not isinstance(item, dict) or not item.get("at"): continue
            try: at=parse_timestamp(item["at"])
            except (TypeError, ValueError): continue
            if lo <= at < hi and item.get("assignment_id") and item.get("selected_runtime_id"):
                assignments.append({"task_id": task.get("id"), "assignment_id": item["assignment_id"], "selected_role": item.get("selected_role"), "selected_provider": item.get("selected_provider"), "selected_runtime_id": item["selected_runtime_id"], "evidence": item.get("route_evidence"), "at": at.isoformat()})
    return {"source":"bounded-rug-ledger","window":{"start_inclusive":lo.isoformat(),"end_exclusive":hi.isoformat()},"matched_observations":matched,"matched_count":len(matched),"assignment_evidence":{"matched":assignments,"matched_count":len(assignments),"notice":"Observational assignment evidence only; no causal, authorization, budget, or savings claim."},"comparison_notice":"Only explicitly matched RUG evidence is reported; absent matches remain unknown."}


def build_report(
    db_path: Path,
    start_text: str,
    end_text: str,
    model_like: str,
    provider_attribution: bool = False,
    session_state: Path | None = None,
    data_db: Path | None = None,
) -> dict[str, Any]:
    try:
        start = parse_timestamp(start_text)
        end = parse_timestamp(end_text)
    except (TypeError, ValueError) as error:
        raise ReportUnavailable(f"invalid cutoff: {error}") from error

    with closing(readonly_connect(db_path)) as connection:
        candidates = fetch_usage_rows(connection, start, end, model_like)
    qualified, exclusions = qualify_rows(candidates, start, end)
    normalized, _ = normalize_rows(candidates, start, end)

    if provider_attribution:
        if session_state is None:
            raise ReportUnavailable("provider attribution requires a session-state path")
        providers = load_provider_catalog(data_db)
        attribute_providers(qualified, session_state.expanduser(), providers)

    return {
        "source": "assistant_usage_events (read-only)",
        "window": {"start_inclusive": start.isoformat(), "end_exclusive": end.isoformat()},
        "cohort": {
            "model_like": model_like,
            "main_agent_only": True,
            "api_records_only": True,
            "positive_output_and_duration": True,
        },
        "candidate_records": len(candidates),
        "qualifying_records": len(qualified),
        "exclusions": exclusions,
        "provider_attribution": "metadata" if provider_attribution else "not requested",
        "groups": aggregate(qualified, provider_attribution),
        "cost_cohort": cost_summary(normalized),
        "definitions": {
            "mean_per_call_output_tpm": "AVG(60000 * output_tokens / duration_ms)",
            "pooled_overall_output_tpm": "60000 * SUM(output_tokens) / SUM(duration_ms)",
            "request_duration": "Recorded request duration; not pure decode speed and not task duration.",
            "cost_cohort": "Endpoint-present main-agent leaf calls, including zero-output calls. It is separate from the positive-output throughput cohort.",
            "token_accounting": "input_tokens already includes cache-read/write tokens and output_tokens already includes billed output; cache and reasoning fields are not added again.",
            "scope": "Observed output throughput over recorded request duration; not configured TPM quota, total prompt-token processing, or pure decode speed.",
        },
    }


def table_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def print_table(report: dict[str, Any]) -> None:
    print(
        f"Window: {report['window']['start_inclusive']} to "
        f"{report['window']['end_exclusive']} (end exclusive)"
    )
    print(
        f"Source: {report['source']}; qualifying calls: {report['qualifying_records']}; "
        f"excluded: {sum(report['exclusions'].values())}"
    )
    print(
        "Model | Provider | Calls | Sessions | Mean per-call output TPM | "
        "Pooled overall output TPM | Mean tok/s | Pooled tok/s"
    )
    print("---|---|---:|---:|---:|---:|---:|---:")
    for group in report["groups"]:
        print(
            f"{table_cell(group['model'])} | {table_cell(group['provider'])} | {group['calls']} | "
            f"{group['sessions']} | {group['mean_per_call_output_tpm']:.1f} | "
            f"{group['pooled_overall_output_tpm']:.1f} | "
            f"{group['mean_per_call_output_tok_s']:.1f} | "
            f"{group['pooled_overall_output_tok_s']:.1f}"
        )
    if report["exclusions"]:
        exclusions = ", ".join(
            f"{name}={count}" for name, count in report["exclusions"].items()
        )
        print(f"Exclusions: {exclusions}")
    cost = report["cost_cohort"]
    recorded_credits = cost["recorded_ai_credits"]
    credit_text = "unknown" if recorded_credits is None else f"{recorded_credits:.6f}"
    print(
        f"Recorded cost cohort: {cost['records']} calls, {credit_text} AI credits, "
        f"coverage={cost['cost_coverage']}, unknown cost records={cost['unknown_cost_records']}"
    )
    if "task_analysis" in report:
        portfolio = report["task_analysis"]["portfolio"]
        credits_per = portfolio["credits_per_accepted_task"]
        value = "unavailable" if credits_per is None else f"{credits_per:.6f}"
        print(
            f"Task ledger: {portfolio['tasks']} tasks, {portfolio['accepted_tasks']} accepted, "
            f"credits per accepted task={value}"
        )
    print(report["definitions"]["scope"])


def default_copilot_home() -> Path:
    return Path(os.environ.get("COPILOT_HOME", Path.home() / ".copilot"))


def main(argv: list[str] | None = None) -> int:
    home = default_copilot_home()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="inclusive ISO-8601 cutoff")
    parser.add_argument("--end", required=True, help="exclusive ISO-8601 cutoff")
    parser.add_argument("--model-like", required=True, help="SQLite LIKE pattern")
    parser.add_argument("--db", type=Path, default=home / "session-store.db")
    parser.add_argument(
        "--provider-attribution",
        choices=("none", "metadata"),
        default="none",
        help="metadata reads selected sessions' model-selection events only",
    )
    parser.add_argument("--session-state", type=Path, default=home / "session-state")
    parser.add_argument("--data-db", type=Path, default=home / "data.db")
    parser.add_argument(
        "--task-ledger",
        type=Path,
        help="optional versioned task annotation JSON for task-level accounting",
    )
    parser.add_argument("--format", choices=("table", "json"), default="table")
    parser.add_argument("--rug-ledger", type=Path, help="optional bounded-RUG export for explicit evidence ingestion")
    args = parser.parse_args(argv)

    try:
        report = build_report(
            args.db,
            args.start,
            args.end,
            args.model_like,
            provider_attribution=args.provider_attribution == "metadata",
            session_state=args.session_state,
            data_db=args.data_db,
        )
        if args.task_ledger:
            report["task_analysis"] = build_task_report(args.db, args.task_ledger)
        if args.rug_ledger:
            report["rug_ingestion"] = ingest_rug_ledger(args.rug_ledger, args.start, args.end)
    except ReportUnavailable as error:
        print(f"UNAVAILABLE: {error}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_table(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
