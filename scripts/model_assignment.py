#!/usr/bin/env python3
"""Durable, context-first worker/coordinator model assignment policy."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any

LUNA_MODEL = "2169cfb1-61ef-4891-acc4-0f1a589b58c2/gpt-5.6-luna"
FLASH_MODEL = "gemini-3.8-flash"
SOL_MODEL = "2169cfb1-61ef-4891-acc4-0f1a589b58c2/gpt-5.6-sol"
STATE_VERSION = "1.0"


class AssignmentBlocked(RuntimeError):
    """Raised when no authorized, context-fitting route is available."""


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    return value.strip()


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
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
def _exclusive_lock(path: Path):
    lock_path = path.with_name(f"{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
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


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": STATE_VERSION, "next_slot": 0, "assignments": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != STATE_VERSION or not isinstance(value.get("assignments"), dict):
        raise ValueError("invalid assignment state")
    if not isinstance(value.get("next_slot"), int) or value["next_slot"] < 0:
        raise ValueError("invalid assignment state cursor")
    return value


def _context_eligibility(candidate: dict[str, Any], required: int) -> tuple[bool, str]:
    capacity = candidate.get("context_capacity")
    if not isinstance(capacity, int) or capacity <= 0:
        return False, "context-capacity-unknown"
    if capacity < required:
        return False, "context-capacity-insufficient"
    return True, "context-fit-confirmed"


def select_assignment(
    state_path: str | Path,
    *,
    assignment_id: str,
    task_id: str,
    task_class: str,
    acceptance_boundary: str,
    required_context: int,
    candidates: list[dict[str, Any]],
    explicit_model: str | None = None,
    large_context: bool = False,
) -> dict[str, Any]:
    """Select once per assignment identity and persist the decision atomically.

    Candidate metadata must include provider, model, available, authorized, and
    context_capacity. Unknown capacity is never treated as a fit. Sol is only
    eligible when explicitly requested by the caller.
    """
    assignment_id = _text(assignment_id, "assignment id")
    task_id = _text(task_id, "task id")
    task_class = _text(task_class, "task class")
    acceptance_boundary = _text(acceptance_boundary, "acceptance boundary")
    if not isinstance(required_context, int) or required_context <= 0:
        raise ValueError("required_context must be a positive estimate")
    if not isinstance(candidates, list) or not candidates:
        raise AssignmentBlocked("no model candidates supplied")
    path = Path(state_path).expanduser().resolve()
    with _exclusive_lock(path):
        state = _load(path)
        prior = state["assignments"].get(assignment_id)
        if prior is not None:
            return deepcopy(prior)

        eligibility: list[dict[str, Any]] = []
        fit: list[dict[str, Any]] = []
        for raw in candidates:
            candidate = deepcopy(raw)
            model = _text(candidate.get("model"), "candidate model")
            provider = _text(candidate.get("provider"), "candidate provider")
            available = candidate.get("available") is True
            authorized = candidate.get("authorized") is True
            ok, context_reason = _context_eligibility(candidate, required_context)
            reasons = []
            if model == SOL_MODEL and explicit_model != SOL_MODEL:
                ok = False
                context_reason = "explicit-user-override-required"
            if not available:
                reasons.append("unavailable")
            if not authorized:
                reasons.append("unauthorized")
            if not ok:
                reasons.append(context_reason)
            eligible = available and authorized and ok
            item = {
                "provider": provider,
                "model": model,
                "eligible": eligible,
                "reasons": reasons or [context_reason],
                "context_capacity": candidate.get("context_capacity", "unknown"),
            }
            eligibility.append(item)
            if eligible:
                fit.append(candidate)

        if explicit_model:
            chosen = next((candidate for candidate in fit if candidate.get("model") == explicit_model), None)
            if chosen is None:
                raise AssignmentBlocked("explicit model is unavailable, unauthorized, or does not fit context")
            reason = "explicit-user-model"
        else:
            if large_context and not any(candidate.get("model") == FLASH_MODEL for candidate in fit):
                raise AssignmentBlocked("large-context task requires an authorized, context-fitting GitHub model; Luna is not a safe fallback")
            if not fit:
                raise AssignmentBlocked("no authorized, available model has confirmed sufficient context capacity")
            flash = [candidate for candidate in fit if candidate.get("model") == FLASH_MODEL]
            luna = [candidate for candidate in fit if candidate.get("model") == LUNA_MODEL]
            if flash and luna:
                chosen = (flash + luna)[state["next_slot"] % 2]
                state["next_slot"] += 1
                reason = "alternating-context-fitting-pool"
            else:
                chosen = fit[0]
                reason = "only-context-fitting-authorized-candidate"

        result = {
            "assignment_id": assignment_id,
            "task_id": task_id,
            "task_class": task_class,
            "acceptance_boundary": acceptance_boundary,
            "required_context": required_context,
            "large_context": large_context,
            "selection_reason": reason,
            "selected_provider": _text(chosen.get("provider"), "selected provider"),
            "selected_model": _text(chosen.get("model"), "selected model"),
            "eligibility": eligibility,
            "attempts": 0,
            "reassignments": 0,
            "outcome": "unknown",
        }
        state["assignments"][assignment_id] = result
        _atomic_write(path, state)
        return deepcopy(result)


def record_outcome(
    state_path: str | Path, assignment_id: str, *, outcome: str, actual_provider: str | None = None,
    actual_model: str | None = None, attempts: int = 1, reassignments: int = 0,
) -> dict[str, Any]:
    """Record observed outcome without changing the durable selection."""
    path = Path(state_path).expanduser().resolve()
    with _exclusive_lock(path):
        state = _load(path)
        if assignment_id not in state["assignments"]:
            raise AssignmentBlocked("assignment identity is not registered")
        updated = deepcopy(state["assignments"][assignment_id])
        updated["outcome"] = _text(outcome, "outcome")
        updated["attempts"] = attempts
        updated["reassignments"] = reassignments
        updated["actual_provider"] = actual_provider or "unknown"
        updated["actual_model"] = actual_model or "unknown"
        state["assignments"][assignment_id] = updated
        _atomic_write(path, state)
        return deepcopy(updated)
