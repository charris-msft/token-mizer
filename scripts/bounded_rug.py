#!/usr/bin/env python3
"""Bounded build-verify-repair task records for opt-in Token Mizer pilots."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RECORD_VERSION = "1.1"
REGISTRY_VERSION = "1.0"
LEDGER_VERSION = "1.0"
MODES = {"bounded-rug"}
SCOPE_STATUSES = {"unknown", "partial", "complete"}
STATES = {"task", "build", "verify", "repair", "accepted", "blocked"}
BOUNDARIES = {"ci-passed", "merged", "deployed", "deployed-and-live-verified"}
ROLES = {"coordinator", "builder", "reviewer", "validator"}
TERMINAL_STATES = {"accepted", "blocked"}
MAX_BUILD_ATTEMPTS = 1
MAX_REPAIR_ATTEMPTS = 1


class RecordError(RuntimeError):
    """Raised when a bounded task record or transition is invalid."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise RecordError(f"invalid timestamp: {value}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def require_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RecordError(f"{name} must be nonempty text")
    return value.strip()


def atomic_write(path: Path, value: dict[str, Any]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


@contextmanager
def exclusive_lock(path: Path):
    lock_path = path.expanduser().resolve().with_name(f"{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def default_registry_path() -> Path:
    configured = os.environ.get("TOKEN_MIZER_TASK_REGISTRY")
    if configured:
        return Path(configured).expanduser()
    home = Path(os.environ.get("COPILOT_HOME", Path.home() / ".copilot"))
    return home / "token-mizer" / "task-registry.json"


def read_registry(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    if not path.exists():
        return {"schema_version": REGISTRY_VERSION, "tasks": {}}
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RecordError(f"invalid task registry: {error}") from error
    if (
        not isinstance(registry, dict)
        or registry.get("schema_version") != REGISTRY_VERSION
        or not isinstance(registry.get("tasks"), dict)
    ):
        raise RecordError("invalid task registry schema")
    for task_id, entry in registry["tasks"].items():
        require_text(task_id, "registry task id")
        if not isinstance(entry, dict):
            raise RecordError("invalid task registry entry")
        require_text(entry.get("path"), "registry record path")
        parse_time(entry.get("created_at"))
    return registry


def require_registry_binding(
    registry: dict[str, Any], record_path: Path, task_id: str,
) -> None:
    entry = registry["tasks"].get(task_id)
    if entry is None:
        raise RecordError("task is not registered; refusing an unbound resume")
    expected_path = str(record_path.expanduser().resolve())
    if os.path.normcase(entry["path"]) != os.path.normcase(expected_path):
        raise RecordError(f"task id is already bound to another record: {entry['path']}")


def read_record(path: Path) -> dict[str, Any]:
    try:
        record = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RecordError(f"record not found: {path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise RecordError(f"invalid record: {error}") from error
    validate_record(record)
    return record


def new_record(
    task_id: str,
    label: str,
    task_class: str,
    acceptance_boundary: str,
    mode: str,
    coordinator_session: str,
    revision: str,
    environment: str,
    at: str | None = None,
) -> dict[str, Any]:
    if mode not in MODES:
        raise RecordError(f"mode must be one of: {', '.join(sorted(MODES))}")
    if acceptance_boundary not in BOUNDARIES:
        raise RecordError("unsupported acceptance boundary")
    timestamp = at or utc_now()
    parse_time(timestamp)
    return {
        "record_version": RECORD_VERSION,
        "task": {
            "id": require_text(task_id, "task id"),
            "label": require_text(label, "label"),
            "class": require_text(task_class, "task class"),
            "mode": mode,
            "acceptance_boundary": acceptance_boundary,
        },
        "state": "task",
        "sequence": 0,
        "record_revision": 0,
        "limits": {
            "initial_build_attempts": MAX_BUILD_ATTEMPTS,
            "repair_attempts": MAX_REPAIR_ATTEMPTS,
        },
        "counts": {"build_attempts": 0, "repair_attempts": 0, "verifications": 0},
        "created_at": timestamp,
        "updated_at": timestamp,
        "revision": require_text(revision, "revision"),
        "environment": require_text(environment, "environment"),
        "scopes": [
            {
                "session_id": require_text(coordinator_session, "coordinator session"),
                "role": "coordinator",
                "start": timestamp,
                "end": None,
            }
        ],
        "observed_milestones": [],
        "checks": [],
        "evidence": [],
        "provider_model_observations": [],
        "rug_attachments": [],
        "model_assignments": [],
        "scope_attestation": {"status": "unknown", "at": None},
        "followup": {"matured": False, "reopened": None, "rolled_back": None, "observed_at": None},
        "events": [],
        "proof_notice": "Annotations are validated for structure only; this helper does not independently prove their truth.",
    }


def transition_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "build_attempts": sum(event["to"] == "build" for event in events),
        "repair_attempts": sum(event["to"] == "repair" for event in events),
        "verifications": sum(event["to"] == "verify" for event in events),
    }


def validate_scope_windows(record: dict[str, Any]) -> None:
    task_start = parse_time(record["created_at"])
    task_end = parse_time(record["updated_at"])
    ownership: dict[str, list[tuple[datetime, datetime]]] = {}
    for scope in record["scopes"]:
        start = parse_time(scope["start"])
        end = parse_time(scope["end"]) if scope.get("end") else task_end
        if start < task_start or end > task_end or end <= start:
            raise RecordError("scope must be a positive window inside the task window")
        session_windows = ownership.setdefault(scope["session_id"], [])
        if any(start < existing_end and existing_start < end for existing_start, existing_end in session_windows):
            raise RecordError("overlapping scopes for the same session are not allowed")
        session_windows.append((start, end))


def validate_record(record: Any) -> None:
    if not isinstance(record, dict) or record.get("record_version") != RECORD_VERSION:
        raise RecordError(f"record_version must be {RECORD_VERSION}")
    task = record.get("task")
    if not isinstance(task, dict):
        raise RecordError("record requires a task object")
    require_text(task.get("id"), "task id")
    require_text(task.get("label"), "label")
    require_text(task.get("class"), "task class")
    if task.get("mode") not in MODES or task.get("acceptance_boundary") not in BOUNDARIES:
        raise RecordError("record has unsupported mode or acceptance boundary")
    created_at = parse_time(record.get("created_at"))
    events = record.get("events")
    if not isinstance(events, list):
        raise RecordError("events must be an array")
    event_ids: list[str] = []
    state = "task"
    replay_counts = {"build_attempts": 0, "repair_attempts": 0, "verifications": 0}
    previous_at = created_at
    for sequence, event in enumerate(events, start=1):
        if not isinstance(event, dict):
            raise RecordError("every event must be an object")
        event_id = require_text(event.get("event_id"), "event id")
        event_ids.append(event_id)
        if event.get("sequence") != sequence or event.get("from") != state:
            raise RecordError("event history is not a contiguous state replay")
        target = event.get("to")
        result = require_text(event.get("result"), "event result")
        check = event.get("check")
        evidence = event.get("evidence")
        milestone = event.get("milestone")
        target_revision = event.get("target_revision")
        target_environment = event.get("target_environment")
        if check is not None:
            require_text(check, "event check")
        if evidence is not None:
            require_text(evidence, "event evidence")
        if target_revision is not None:
            require_text(target_revision, "event target revision")
        if target_environment is not None:
            require_text(target_environment, "event target environment")
        if milestone is not None and milestone not in BOUNDARIES:
            raise RecordError("event milestone is not a supported boundary")
        event_at = parse_time(event.get("at"))
        if event_at < previous_at:
            raise RecordError("event timestamps must be monotonic")
        require_transition(
            {"state": state, "counts": replay_counts, "task": task, "events": events[: sequence - 1]},
            target, result, milestone, check, evidence,
            target_revision, target_environment,
        )
        if target == "build":
            replay_counts["build_attempts"] += 1
        elif target == "repair":
            replay_counts["repair_attempts"] += 1
        elif target == "verify":
            replay_counts["verifications"] += 1
        state = target
        previous_at = event_at
    if len(set(event_ids)) != len(event_ids):
        raise RecordError("event_id values must be unique")
    if record.get("state") != state or record.get("sequence") != len(events):
        raise RecordError("persisted state or sequence does not match event history")
    if not isinstance(record.get("record_revision"), int) or record["record_revision"] < record["sequence"]:
        raise RecordError("record_revision must cover every persisted mutation")
    expected_counts = transition_counts(events)
    if record.get("counts") != expected_counts:
        raise RecordError("persisted attempt counts do not match event history")
    limits = record.get("limits")
    if limits != {"initial_build_attempts": MAX_BUILD_ATTEMPTS, "repair_attempts": MAX_REPAIR_ATTEMPTS}:
        raise RecordError("attempt limits cannot be changed or reset")
    if expected_counts["build_attempts"] > MAX_BUILD_ATTEMPTS or expected_counts["repair_attempts"] > MAX_REPAIR_ATTEMPTS:
        raise RecordError("attempt history exceeds bounded limits")
    updated_at = parse_time(record.get("updated_at"))
    if updated_at != previous_at:
        raise RecordError("updated_at must match the latest state transition")
    require_text(record.get("revision"), "revision")
    require_text(record.get("environment"), "environment")
    scopes = record.get("scopes")
    if not isinstance(scopes, list) or not scopes:
        raise RecordError("at least one task scope is required")
    for scope in scopes:
        if not isinstance(scope, dict) or scope.get("role") not in ROLES:
            raise RecordError("invalid task scope")
        require_text(scope.get("session_id"), "scope session_id")
        parse_time(scope.get("start"))
        if scope.get("end") is not None and parse_time(scope["end"]) <= parse_time(scope["start"]):
            raise RecordError("scope end must be after scope start")
    attestation = record.get("scope_attestation")
    if not isinstance(attestation, dict) or attestation.get("status") not in SCOPE_STATUSES:
        raise RecordError("invalid scope completeness attestation")
    if attestation["status"] == "unknown":
        if attestation.get("at") is not None:
            raise RecordError("unknown scope completeness cannot have an attestation time")
    else:
        attested_at = parse_time(attestation.get("at"))
        if attested_at < created_at:
            raise RecordError("scope attestation cannot precede task creation")
    if attestation["status"] == "complete":
        if record.get("state") not in TERMINAL_STATES:
            raise RecordError("scope completeness can be attested only for a terminal task")
        validate_scope_windows(record)
    milestones = record.get("observed_milestones")
    if not isinstance(milestones, list) or any(item not in BOUNDARIES for item in milestones):
        raise RecordError("observed_milestones contains an unsupported boundary")
    if len(set(milestones)) != len(milestones):
        raise RecordError("observed_milestones cannot contain duplicates")
    checks = record.get("checks")
    if not isinstance(checks, list):
        raise RecordError("checks must be an array")
    for check in checks:
        if not isinstance(check, dict):
            raise RecordError("invalid check annotation")
        require_text(check.get("name"), "check name")
        require_text(check.get("result"), "check result")
        parse_time(check.get("at"))
        if check.get("evidence") is not None:
            require_text(check["evidence"], "check evidence")
        if check.get("target_revision") is not None:
            require_text(check["target_revision"], "check target revision")
        if check.get("target_environment") is not None:
            require_text(check["target_environment"], "check target environment")
    evidence_items = record.get("evidence")
    if not isinstance(evidence_items, list):
        raise RecordError("evidence must be an array")
    for evidence in evidence_items:
        if not isinstance(evidence, dict) or evidence.get("kind") != "annotation":
            raise RecordError("invalid evidence annotation")
        require_text(evidence.get("ref"), "evidence reference")
        parse_time(evidence.get("at"))
        if evidence.get("target_revision") is not None:
            require_text(evidence["target_revision"], "evidence target revision")
        if evidence.get("target_environment") is not None:
            require_text(evidence["target_environment"], "evidence target environment")
    expected_checks = [
        {
            "name": event["check"],
            "result": event["result"],
            "at": event["at"],
            "evidence": event.get("evidence"),
            "target_revision": event.get("target_revision"),
            "target_environment": event.get("target_environment"),
        }
        for event in events
        if event.get("check")
    ]
    expected_evidence = [
        {
            "kind": "annotation", "ref": event["evidence"], "at": event["at"],
            "target_revision": event.get("target_revision"),
            "target_environment": event.get("target_environment"),
        }
        for event in events
        if event.get("evidence")
    ]
    expected_milestones = list(
        dict.fromkeys(event["milestone"] for event in events if event.get("milestone"))
    )
    if checks != expected_checks or evidence_items != expected_evidence or milestones != expected_milestones:
        raise RecordError("derived checks, evidence, or milestones do not match event history")
    attachments = record.get("rug_attachments", [])
    if not isinstance(attachments, list): raise RecordError("rug_attachments must be an array")
    for attachment in attachments:
        if not isinstance(attachment, dict): raise RecordError("invalid RUG attachment")
        require_text(attachment.get("attachment_id"), "attachment id"); require_text(attachment.get("kind"), "attachment kind"); require_text(attachment.get("ref"), "attachment ref"); parse_time(attachment.get("at"))
    observations = record.get("provider_model_observations")
    if not isinstance(observations, list):
        raise RecordError("provider_model_observations must be an array")
    assignments = record.get("model_assignments", [])
    if not isinstance(assignments, list):
        raise RecordError("model_assignments must be an array")
    seen_assignments = set()
    for assignment in assignments:
        if not isinstance(assignment, dict):
            raise RecordError("invalid model assignment")
        assignment_id = require_text(assignment.get("assignment_id"), "assignment id")
        if assignment_id in seen_assignments:
            raise RecordError("duplicate model assignment")
        seen_assignments.add(assignment_id)
        for key in ("task_id", "task_class", "acceptance_boundary", "selection_reason", "selected_provider", "selected_model", "outcome"):
            require_text(assignment.get(key), f"assignment {key}")
        if assignment["task_id"] != task["id"]:
            raise RecordError("model assignment task id must match enclosing bounded-RUG task")
        if assignment["task_class"] != task["class"] or assignment["acceptance_boundary"] != task["acceptance_boundary"]:
            raise RecordError("model assignment boundary/class must match enclosing bounded-RUG task")
        if not isinstance(assignment.get("eligibility"), list):
            raise RecordError("assignment eligibility must be an array")
        for key in ("selected_runtime_id", "selected_role", "selected_family"):
            require_text(assignment.get(key), f"assignment {key}")
        created = parse_time(assignment.get("created_at"))
        observed = parse_time(assignment.get("at"))
        snapshot_cutoff = parse_time(assignment.get("snapshot_cutoff"))
        if not created <= observed < snapshot_cutoff:
            raise RecordError("assignment evidence must precede snapshot cutoff")
        for key in ("attempts", "reassignments"):
            value = assignment.get(key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise RecordError(f"assignment {key} must be a nonnegative integer or unknown (null)")
    for observation in observations:
        if not isinstance(observation, dict) or observation.get("role") not in ROLES:
            raise RecordError("invalid provider/model observation")
        require_text(observation.get("provider"), "observation provider")
        require_text(observation.get("model"), "observation model")
        require_text(observation.get("status"), "observation status")
        parse_time(observation.get("at"))
    followup = record.get("followup")
    if not isinstance(followup, dict) or not isinstance(followup.get("matured"), bool):
        raise RecordError("invalid followup annotation")
    for key in ("reopened", "rolled_back"):
        if followup.get(key) not in {True, False, None}:
            raise RecordError(f"followup {key} must be true, false, or unknown")
    if followup["matured"]:
        parse_time(followup.get("observed_at"))
    elif any(followup.get(key) is not None for key in ("reopened", "rolled_back", "observed_at")):
        raise RecordError("unmatured followup cannot claim observed results")


def require_transition(
    record: dict[str, Any], target: str, result: str, milestone: str | None,
    check: str | None, evidence: str | None, target_revision: str | None,
    target_environment: str | None,
) -> None:
    state = record["state"]
    if state in TERMINAL_STATES:
        raise RecordError(f"terminal state {state} cannot transition")
    allowed = {
        "task": {"build", "blocked"},
        "build": {"verify", "blocked"},
        "verify": {"repair", "accepted", "blocked"},
        "repair": {"verify", "blocked"},
    }
    if target not in allowed[state]:
        raise RecordError(f"invalid transition: {state} -> {target}")
    counts = record["counts"]
    if milestone is not None and target != "accepted":
        raise RecordError("milestones can be recorded only by an accepted transition")
    if target == "build" and counts["build_attempts"] >= MAX_BUILD_ATTEMPTS:
        raise RecordError("initial build attempt is already consumed")
    if target == "verify":
        if result != "completed":
            raise RecordError("build or repair -> verify requires result=completed")
        if not target_revision or not target_environment:
            raise RecordError("verification requires an exact target revision and environment")
    if target == "repair":
        if result != "failed":
            raise RecordError("verify -> repair requires result=failed")
        if counts["repair_attempts"] >= MAX_REPAIR_ATTEMPTS:
            raise RecordError("repair attempt is already consumed")
        if not check or not evidence:
            raise RecordError("failed verification requires check and evidence")
    if target in {"repair", "accepted"} or (
        target == "blocked" and state == "verify" and result == "failed"
    ):
        if not target_revision or not target_environment:
            raise RecordError("verification outcome requires an exact target revision and environment")
        preceding = record.get("events", [])[-1] if record.get("events") else None
        if not preceding or preceding.get("to") != "verify":
            raise RecordError("verification outcome requires a preceding verify transition")
        if (
            target_revision != preceding.get("target_revision")
            or target_environment != preceding.get("target_environment")
        ):
            raise RecordError("verification outcome target must match the preceding verify transition")
    if target == "accepted":
        boundary = record["task"]["acceptance_boundary"]
        if result != "passed" or milestone != boundary:
            raise RecordError("acceptance requires a passed verification at the selected boundary")
        if not check or not evidence:
            raise RecordError("acceptance requires a check and evidence reference")
    if target == "blocked":
        infrastructure = result in {"infrastructure-blocked", "budget-blocked", "authorization-blocked", "environment-blocked"}
        exhausted = state == "verify" and result == "failed" and counts["repair_attempts"] >= MAX_REPAIR_ATTEMPTS
        if not infrastructure and not exhausted:
            raise RecordError("blocking requires an infrastructure/budget/auth/environment constraint or exhausted failed verification")
        if not evidence:
            raise RecordError("blocked transition requires evidence")
        if exhausted and not check:
            raise RecordError("exhausted failed verification requires a check")


def apply_transition(
    record: dict[str, Any], target: str, result: str, event_id: str,
    expected_sequence: int, expected_record_revision: int | None = None,
    at: str | None = None, check: str | None = None,
    evidence: str | None = None, milestone: str | None = None,
    target_revision: str | None = None, target_environment: str | None = None,
) -> dict[str, Any]:
    validate_record(record)
    if target not in STATES:
        raise RecordError("unsupported target state")
    require_text(event_id, "event id")
    if event_id in {event["event_id"] for event in record["events"]}:
        raise RecordError("replayed event_id")
    if expected_sequence != record["sequence"]:
        raise RecordError(
            f"stale sequence: expected {record['sequence']}, received {expected_sequence}"
        )
    if expected_record_revision is not None and expected_record_revision != record["record_revision"]:
        raise RecordError(
            f"stale record revision: expected {record['record_revision']}, received {expected_record_revision}"
        )
    require_transition(
        record, target, result, milestone, check, evidence,
        target_revision, target_environment,
    )
    timestamp = at or utc_now()
    if parse_time(timestamp) < parse_time(record["updated_at"]):
        raise RecordError("event timestamp cannot precede the persisted record")
    updated = deepcopy(record)
    event = {
        "sequence": record["sequence"] + 1,
        "event_id": event_id,
        "at": timestamp,
        "from": record["state"],
        "to": target,
        "result": require_text(result, "result"),
        "check": check,
        "evidence": evidence,
        "milestone": milestone,
        "target_revision": target_revision,
        "target_environment": target_environment,
    }
    updated["events"].append(event)
    updated["state"] = target
    updated["sequence"] = event["sequence"]
    updated["record_revision"] += 1
    updated["updated_at"] = timestamp
    updated["counts"] = transition_counts(updated["events"])
    if check:
        updated["checks"].append(
            {
                "name": check, "result": result, "at": timestamp, "evidence": evidence,
                "target_revision": target_revision, "target_environment": target_environment,
            }
        )
    if evidence:
        updated["evidence"].append(
            {
                "kind": "annotation", "ref": evidence, "at": timestamp,
                "target_revision": target_revision, "target_environment": target_environment,
            }
        )
    if milestone and milestone not in updated["observed_milestones"]:
        updated["observed_milestones"].append(milestone)
    validate_record(updated)
    return updated


def add_scope(
    record: dict[str, Any], session_id: str, role: str, start: str,
    end: str | None, expected_record_revision: int | None = None,
) -> dict[str, Any]:
    validate_record(record)
    if expected_record_revision is not None and expected_record_revision != record["record_revision"]:
        raise RecordError("stale record revision")
    if record["state"] in TERMINAL_STATES:
        raise RecordError("cannot add scope to a terminal record")
    if role not in ROLES:
        raise RecordError(f"role must be one of: {', '.join(sorted(ROLES))}")
    parse_time(start)
    if end is not None and parse_time(end) <= parse_time(start):
        raise RecordError("scope end must be after scope start")
    updated = deepcopy(record)
    updated["scopes"].append({"session_id": require_text(session_id, "session id"), "role": role, "start": start, "end": end})
    updated["record_revision"] += 1
    validate_record(updated)
    return updated


def attest_scope(
    record: dict[str, Any], status: str, expected_record_revision: int | None = None,
    at: str | None = None,
) -> dict[str, Any]:
    validate_record(record)
    if expected_record_revision is not None and expected_record_revision != record["record_revision"]:
        raise RecordError("stale record revision")
    if status not in SCOPE_STATUSES - {"unknown"}:
        raise RecordError("scope attestation must be partial or complete")
    current = record["scope_attestation"]["status"]
    if current == "complete" or (current == "partial" and status != "complete"):
        raise RecordError("scope attestation cannot be reset or downgraded")
    timestamp = at or utc_now()
    if parse_time(timestamp) < parse_time(record["created_at"]):
        raise RecordError("scope attestation cannot precede task creation")
    updated = deepcopy(record)
    updated["scope_attestation"] = {"status": status, "at": timestamp}
    updated["record_revision"] += 1
    validate_record(updated)
    return updated


def add_observation(
    record: dict[str, Any], role: str, provider: str, model: str, status: str,
    at: str | None = None, expected_record_revision: int | None = None,
) -> dict[str, Any]:
    validate_record(record)
    if expected_record_revision is not None and expected_record_revision != record["record_revision"]:
        raise RecordError("stale record revision")
    if role not in ROLES:
        raise RecordError("invalid observation role")
    timestamp = at or utc_now()
    parse_time(timestamp)
    updated = deepcopy(record)
    updated["provider_model_observations"].append(
        {"role": role, "provider": require_text(provider, "provider"), "model": require_text(model, "model"), "status": require_text(status, "status"), "at": timestamp}
    )
    updated["record_revision"] += 1
    validate_record(updated)
    return updated


def add_assignment(
    record: dict[str, Any], assignment_export: dict[str, Any], assignment_id: str,
    expected_record_revision: int | None = None,
) -> dict[str, Any]:
    validate_record(record)
    if expected_record_revision is not None and expected_record_revision != record["record_revision"]:
        raise RecordError("stale record revision")
    if record["state"] in TERMINAL_STATES:
        raise RecordError("cannot add assignment to a terminal record")
    from model_assignment import AssignmentBlocked, assignment_from_export
    try:
        candidate = assignment_from_export(assignment_export, assignment_id)
    except (AssignmentBlocked, ValueError, TypeError, KeyError) as error:
        raise RecordError(f"invalid assignment export: {error}") from error
    if candidate.get("task_id") != record["task"]["id"]:
        raise RecordError("model assignment task id must match enclosing bounded-RUG task")
    if candidate.get("task_class") != record["task"]["class"] or candidate.get("acceptance_boundary") != record["task"]["acceptance_boundary"]:
        raise RecordError("model assignment boundary/class must match enclosing bounded-RUG task")
    validate_record({**record, "model_assignments": record.get("model_assignments", []) + [candidate]})
    if any(item["assignment_id"] == candidate["assignment_id"] for item in record.get("model_assignments", [])):
        raise RecordError("assignment identity already recorded")
    updated = deepcopy(record)
    updated.setdefault("model_assignments", []).append(candidate)
    updated["record_revision"] += 1
    validate_record(updated)
    return updated


def record_followup(
    record: dict[str, Any], reopened: bool | None, rolled_back: bool | None,
    expected_record_revision: int | None = None, at: str | None = None,
) -> dict[str, Any]:
    validate_record(record)
    if expected_record_revision is not None and expected_record_revision != record["record_revision"]:
        raise RecordError("stale record revision")
    if record["state"] not in TERMINAL_STATES:
        raise RecordError("followup can be recorded only after a terminal outcome")
    timestamp = at or utc_now()
    if parse_time(timestamp) < parse_time(record["updated_at"]):
        raise RecordError("followup timestamp cannot precede the terminal outcome")
    updated = deepcopy(record)
    updated["followup"] = {
        "matured": True,
        "reopened": reopened,
        "rolled_back": rolled_back,
        "observed_at": timestamp,
    }
    updated["record_revision"] += 1
    validate_record(updated)
    return updated


def attach_rug(record: dict[str, Any], attachment_id: str, kind: str, ref: str, at: str | None = None, expected_record_revision: int | None = None, task_id: str | None = None, cutoff: str | None = None) -> dict[str, Any]:
    validate_record(record)
    if expected_record_revision is not None and expected_record_revision != record["record_revision"]: raise RecordError("stale record revision")
    if task_id is not None and require_text(task_id, "task id") != record["task"]["id"]: raise RecordError("RUG task binding mismatch")
    timestamp = at or utc_now(); parse_time(timestamp); aid = require_text(attachment_id, "attachment id")
    attachment_cutoff = parse_time(cutoff).isoformat() if cutoff is not None else None
    if any(x.get("attachment_id") == aid for x in record.get("rug_attachments", [])): raise RecordError("duplicate RUG attachment")
    updated = deepcopy(record); updated.setdefault("rug_attachments", []).append({"attachment_id": aid, "kind": require_text(kind, "attachment kind"), "ref": require_text(ref, "attachment ref"), "task_id": record["task"]["id"], "at": timestamp, "cutoff": attachment_cutoff}); updated["record_revision"] += 1; validate_record(updated); return updated


def parse_optional_bool(value: str) -> bool | None:
    return {"true": True, "false": False, "unknown": None}[value]


def export_ledger(record: dict[str, Any], analysis_id: str, cutoff: str) -> dict[str, Any]:
    validate_record(record)
    cutoff_time = parse_time(cutoff)
    start = parse_time(record["created_at"])
    end = parse_time(record["updated_at"])
    if end <= start:
        raise RecordError("record needs at least one later transition before ledger export")
    if cutoff_time < end:
        raise RecordError("ledger cutoff cannot precede the record end")
    scopes = []
    for scope in record["scopes"]:
        scope_end = parse_time(scope["end"]) if scope.get("end") else end
        if scope_end > end:
            raise RecordError("scope end cannot exceed task end")
        scopes.append({**scope, "end": scope_end.isoformat()})
    outcome = {
        "accepted": record["task"]["acceptance_boundary"],
        "blocked": "blocked",
    }.get(record["state"], "unfinished")
    accepted_event = record["events"][-1] if record["state"] == "accepted" else None
    scope_status = record["scope_attestation"]["status"]
    scope_attested_at = record["scope_attestation"]["at"]
    if scope_attested_at is not None and parse_time(scope_attested_at) > cutoff_time:
        scope_status = "unknown"
        scope_attested_at = None
    if scope_status == "complete":
        validate_scope_windows(record)
    attachments = [a for a in record.get("rug_attachments", []) if parse_time(a["at"]) < cutoff_time]
    task: dict[str, Any] = {
        "id": record["task"]["id"],
        "label": record["task"]["label"],
        "type": record["task"]["class"],
        "start": start.isoformat(),
        "end": end.isoformat(),
        "outcome": outcome,
        "observed_milestones": record["observed_milestones"],
        "scope_complete": scope_status == "complete",
        "scope_status": scope_status,
        "scope_attested_at": scope_attested_at,
        "revision": accepted_event.get("target_revision") if accepted_event else record["revision"],
        "environment": accepted_event.get("target_environment") if accepted_event else record["environment"],
        "evidence": record["evidence"],
        "scopes": scopes,
        "pilot_mode": record["task"]["mode"],
        "bounded_rug": {
            "record_version": RECORD_VERSION,
            "acceptance_boundary": record["task"]["acceptance_boundary"],
            "state": record["state"],
            "build_attempts": record["counts"]["build_attempts"],
            "repair_attempts": record["counts"]["repair_attempts"],
            "verifications": record["counts"]["verifications"],
            "provider_model_observations": [o for o in record["provider_model_observations"] if parse_time(o["at"]) < cutoff_time],
            "rug_attachments": attachments,
            "model_assignments": [m for m in record.get("model_assignments", []) if parse_time(m["snapshot_cutoff"]) <= cutoff_time],
            "assignment_evidence_coverage": {
                "included": sum(parse_time(m["snapshot_cutoff"]) <= cutoff_time for m in record.get("model_assignments", [])),
                "excluded_newer_snapshots": sum(parse_time(m["snapshot_cutoff"]) > cutoff_time for m in record.get("model_assignments", [])),
                "notice": "Excluded or missing evidence is unknown, not zero attempts. Attach an allocator as-of export to retain earlier evidence.",
            },
            "scope_status": scope_status,
            "verified_target": {
                "revision": accepted_event.get("target_revision"),
                "environment": accepted_event.get("target_environment"),
                "check": accepted_event.get("check"),
                "evidence": accepted_event.get("evidence"),
                "at": accepted_event.get("at"),
            } if accepted_event else None,
            "proof_notice": record["proof_notice"],
        },
    }
    followup = record["followup"]
    if followup["matured"] and parse_time(followup["observed_at"]) <= cutoff_time:
        task.update(
            {
                "followup_matured": True,
                "reopened": followup["reopened"],
                "rolled_back": followup["rolled_back"],
                "followup_observed_at": followup["observed_at"],
            }
        )
    return {
        "schema_version": LEDGER_VERSION,
        "analysis_id": require_text(analysis_id, "analysis id"),
        "cutoff": cutoff_time.isoformat(),
        "acceptance_boundaries": {record["task"]["class"]: record["task"]["acceptance_boundary"]},
        "pilot": {
            "mechanics": "synthetic or annotated evidence only; real-world pilot results remain pending until observed",
            "comparison_rule": "compare the same task class and acceptance boundary; do not pool unlike tasks or claim causality",
        },
        "tasks": [task],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry", type=Path, default=default_registry_path(),
        help="private task identity registry (defaults under COPILOT_HOME)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    init = subparsers.add_parser("init")
    init.add_argument("--file", type=Path, required=True)
    init.add_argument("--task-id", required=True)
    init.add_argument("--label", required=True)
    init.add_argument("--task-class", required=True)
    init.add_argument("--acceptance-boundary", choices=sorted(BOUNDARIES), required=True)
    init.add_argument("--mode", choices=sorted(MODES), default="bounded-rug")
    init.add_argument("--coordinator-session", required=True)
    init.add_argument("--revision", required=True)
    init.add_argument("--environment", required=True)
    init.add_argument("--at")

    transition = subparsers.add_parser("transition")
    transition.add_argument("--file", type=Path, required=True)
    transition.add_argument("--to", choices=sorted(STATES - {"task"}), required=True)
    transition.add_argument("--result", required=True)
    transition.add_argument("--event-id", required=True)
    transition.add_argument("--expected-sequence", type=int, required=True)
    transition.add_argument("--expected-record-revision", type=int, required=True)
    transition.add_argument("--at")
    transition.add_argument("--check")
    transition.add_argument("--evidence")
    transition.add_argument("--milestone", choices=sorted(BOUNDARIES))
    transition.add_argument("--target-revision")
    transition.add_argument("--target-environment")

    scope = subparsers.add_parser("add-scope")
    scope.add_argument("--file", type=Path, required=True)
    scope.add_argument("--session-id", required=True)
    scope.add_argument("--role", choices=sorted(ROLES), required=True)
    scope.add_argument("--start", required=True)
    scope.add_argument("--end")
    scope.add_argument("--expected-record-revision", type=int, required=True)

    attest = subparsers.add_parser("attest-scope")
    attest.add_argument("--file", type=Path, required=True)
    attest.add_argument("--status", choices=("partial", "complete"), required=True)
    attest.add_argument("--at")
    attest.add_argument("--expected-record-revision", type=int, required=True)

    observe = subparsers.add_parser("observe-model")
    observe.add_argument("--file", type=Path, required=True)
    observe.add_argument("--role", choices=sorted(ROLES), required=True)
    observe.add_argument("--provider", required=True)
    observe.add_argument("--model", required=True)
    observe.add_argument("--status", required=True)
    observe.add_argument("--at")
    observe.add_argument("--expected-record-revision", type=int, required=True)

    assignment = subparsers.add_parser("add-assignment")
    assignment.add_argument("--file", type=Path, required=True)
    assignment.add_argument("--assignment-export", type=Path, required=True)
    assignment.add_argument("--assignment-id", required=True)
    assignment.add_argument("--expected-record-revision", type=int, required=True)

    rug = subparsers.add_parser("attach-rug")
    rug.add_argument("--file", type=Path, required=True); rug.add_argument("--task-id", required=True); rug.add_argument("--attachment-id", required=True); rug.add_argument("--kind", required=True); rug.add_argument("--ref", required=True); rug.add_argument("--cutoff"); rug.add_argument("--at"); rug.add_argument("--expected-record-revision", type=int, required=True)

    followup = subparsers.add_parser("record-followup")
    followup.add_argument("--file", type=Path, required=True)
    followup.add_argument("--reopened", choices=("true", "false", "unknown"), required=True)
    followup.add_argument("--rolled-back", choices=("true", "false", "unknown"), required=True)
    followup.add_argument("--expected-record-revision", type=int, required=True)
    followup.add_argument("--at")

    show = subparsers.add_parser("show")
    show.add_argument("--file", type=Path, required=True)

    export = subparsers.add_parser("export-ledger")
    export.add_argument("--file", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--analysis-id", required=True)
    export.add_argument("--cutoff", required=True)
    return parser


def read_bound_record(
    registry: dict[str, Any], path: Path,
) -> dict[str, Any]:
    record = read_record(path)
    require_registry_binding(registry, path, record["task"]["id"])
    return record


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        registry_path = args.registry.expanduser().resolve()
        record_path = args.file.expanduser().resolve()
        with exclusive_lock(registry_path):
            registry = read_registry(registry_path)
            with exclusive_lock(record_path):
                if args.command == "init":
                    if args.task_id in registry["tasks"]:
                        existing = registry["tasks"][args.task_id]["path"]
                        raise RecordError(f"task id is already registered at {existing}")
                    if record_path.exists():
                        raise RecordError("refusing to reset an existing record")
                    record = new_record(
                        args.task_id, args.label, args.task_class,
                        args.acceptance_boundary, args.mode,
                        args.coordinator_session, args.revision,
                        args.environment, args.at,
                    )
                    atomic_write(record_path, record)
                    registry["tasks"][args.task_id] = {
                        "path": str(record_path),
                        "created_at": record["created_at"],
                    }
                    atomic_write(registry_path, registry)
                else:
                    record = read_bound_record(registry, record_path)
                    if args.command == "transition":
                        record = apply_transition(
                            record, args.to, args.result, args.event_id,
                            args.expected_sequence, args.expected_record_revision,
                            args.at, args.check, args.evidence, args.milestone,
                            args.target_revision, args.target_environment,
                        )
                        atomic_write(record_path, record)
                    elif args.command == "add-scope":
                        record = add_scope(
                            record, args.session_id, args.role, args.start,
                            args.end, args.expected_record_revision,
                        )
                        atomic_write(record_path, record)
                    elif args.command == "attest-scope":
                        record = attest_scope(
                            record, args.status, args.expected_record_revision, args.at,
                        )
                        atomic_write(record_path, record)
                    elif args.command == "observe-model":
                        record = add_observation(
                            record, args.role, args.provider, args.model,
                            args.status, args.at, args.expected_record_revision,
                        )
                        atomic_write(record_path, record)
                    elif args.command == "add-assignment":
                        assignment_export = json.loads(args.assignment_export.read_text(encoding="utf-8-sig"))
                        record = add_assignment(record, assignment_export, args.assignment_id, args.expected_record_revision)
                        atomic_write(record_path, record)
                    elif args.command == "attach-rug":
                        record = attach_rug(record, args.attachment_id, args.kind, args.ref, args.at, args.expected_record_revision, args.task_id, args.cutoff)
                        atomic_write(record_path, record)
                    elif args.command == "record-followup":
                        record = record_followup(
                            record, parse_optional_bool(args.reopened),
                            parse_optional_bool(args.rolled_back),
                            args.expected_record_revision, args.at,
                        )
                        atomic_write(record_path, record)
                    elif args.command == "export-ledger":
                        output_path = args.output.expanduser().resolve()
                        if output_path in {record_path, registry_path}:
                            raise RecordError("ledger output cannot replace the record or registry")
                        ledger = export_ledger(record, args.analysis_id, args.cutoff)
                        atomic_write(output_path, ledger)
                        print(json.dumps(ledger, indent=2, sort_keys=True))
                        return 0
        print(json.dumps(record, indent=2, sort_keys=True))
        return 0
    except (RecordError, OSError, ValueError, TypeError, KeyError) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
