#!/usr/bin/env python3
"""Read-only Token Mizer throughput reporting from Copilot session data."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
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
    "total_nano_aiu",
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


def validate_usage_schema(connection: sqlite3.Connection) -> None:
    columns = table_columns(connection, USAGE_TABLE)
    if not columns:
        raise ReportUnavailable(f"missing table: {USAGE_TABLE}")
    missing = sorted(REQUIRED_COLUMNS - columns)
    if missing:
        raise ReportUnavailable(f"missing required columns: {', '.join(missing)}")


def fetch_usage_rows(
    connection: sqlite3.Connection,
    start: datetime,
    end: datetime,
    model_like: str,
) -> list[sqlite3.Row]:
    """Fetch a date-bounded candidate set with parameterized SQL."""
    if end <= start:
        raise ReportUnavailable("end cutoff must be after start cutoff")
    validate_usage_schema(connection)
    return list(
        connection.execute(
            f"""
            SELECT session_id, model, output_tokens, duration_ms, created_at,
                   agent_id, api_endpoint, total_nano_aiu
            FROM {USAGE_TABLE}
            WHERE substr(created_at, 1, 10) BETWEEN ? AND ?
              AND model LIKE ?
            """,
            (start.date().isoformat(), end.date().isoformat(), model_like),
        )
    )


def qualify_rows(
    rows: Iterable[sqlite3.Row], start: datetime, end: datetime
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    exclusions: defaultdict[str, int] = defaultdict(int)
    qualified: list[dict[str, Any]] = []
    for row in rows:
        try:
            created_at = parse_timestamp(row["created_at"])
        except (TypeError, ValueError):
            exclusions["invalid_timestamp"] += 1
            continue
        if created_at < start or created_at >= end:
            exclusions["outside_exact_window"] += 1
            continue
        if row["agent_id"] is not None:
            exclusions["subagent"] += 1
            continue
        if row["api_endpoint"] is None:
            exclusions["aggregate_or_missing_endpoint"] += 1
            continue
        output_tokens = row["output_tokens"]
        duration_ms = row["duration_ms"]
        if not isinstance(output_tokens, (int, float)) or output_tokens <= 0:
            exclusions["nonpositive_output_tokens"] += 1
            continue
        if not isinstance(duration_ms, (int, float)) or duration_ms <= 0:
            exclusions["nonpositive_duration_ms"] += 1
            continue
        qualified.append(
            {
                "session_id": str(row["session_id"]),
                "model": str(row["model"]),
                "output_tokens": float(output_tokens),
                "duration_ms": float(duration_ms),
                "created_at": created_at,
                "total_nano_aiu": float(row["total_nano_aiu"] or 0),
            }
        )
    return qualified, dict(sorted(exclusions.items()))


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


def load_session_selections(events_path: Path) -> list[tuple[datetime, str]]:
    selections: list[tuple[datetime, str]] = []
    if not events_path.is_file():
        return selections
    try:
        with events_path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                event_type = event.get("type")
                data = event.get("data") or {}
                if event_type == "session.start":
                    value = data.get("selectedModel")
                elif event_type == "session.model_change":
                    value = data.get("newModel")
                else:
                    continue
                model = selected_model(value)
                timestamp_value = event.get("timestamp") or data.get("timestamp")
                if not model or not timestamp_value:
                    continue
                try:
                    selections.append((parse_timestamp(timestamp_value), model))
                except (TypeError, ValueError):
                    continue
    except OSError:
        return []
    return sorted(selections, key=lambda item: item[0])


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
        events = load_session_selections(events_path) if events_path else []
        for row in session_rows:
            matching = [
                model
                for timestamp, model in events
                if timestamp <= row["created_at"]
                and model_basename(model) == model_basename(row["model"])
            ]
            selection = matching[-1] if matching else None
            if selection is None:
                row["provider"] = "unknown"
            elif "/" not in selection:
                row["provider"] = (
                    "GitHub billed" if row["total_nano_aiu"] > 0 else "unknown"
                )
            else:
                provider_id = selection.rsplit("/", 1)[0]
                provider = providers.get(provider_id)
                row["provider"] = (
                    f"{provider[0]} ({provider[1]})" if provider else "unknown"
                )


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
        results.append(
            {
                "model": model,
                "provider": provider,
                "calls": len(group),
                "sessions": len({row["session_id"] for row in group}),
                "output_tokens": int(total_output),
                "duration_ms": int(total_duration),
                "mean_per_call_output_tpm": sum(per_call_tpm) / len(per_call_tpm),
                "pooled_overall_output_tpm": 60000.0 * total_output / total_duration,
                "mean_per_call_output_tok_s": sum(per_call_tpm) / len(per_call_tpm) / 60.0,
                "pooled_overall_output_tok_s": total_output / (total_duration / 1000.0),
            }
        )
    return results


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
        "definitions": {
            "mean_per_call_output_tpm": "AVG(60000 * output_tokens / duration_ms)",
            "pooled_overall_output_tpm": "60000 * SUM(output_tokens) / SUM(duration_ms)",
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
    parser.add_argument("--format", choices=("table", "json"), default="table")
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
