#!/usr/bin/env python3
"""Fail-closed model allocation, admission, measurement, and JSON CLI."""
from __future__ import annotations
import argparse, json, os, sys, tempfile
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LUNA_MODEL = "gpt-5.6-luna"
FLASH_MODEL = "gemini-3.8-flash"
SOL_MODEL = "gpt-5.6-sol"
ASTRA_MODEL = "gpt-6-astra"
BUILTIN_SOL_MODEL = "gpt-6-sol"
GROK_MODEL = "grok-4.7"
DEEPSEEK_MODEL = "DeepSeek-V4.1-Flash"
STATE_VERSION = "5.0"
LEGACY_FAMILIES = {"luna": ("Foundry", LUNA_MODEL), "sol": ("Foundry", SOL_MODEL),
                   "astra": ("Foundry", ASTRA_MODEL), "flash": ("GitHub", FLASH_MODEL)}
FAMILIES = {"luna": ("Foundry", LUNA_MODEL), "sol6": ("GitHub", BUILTIN_SOL_MODEL),
            "astra": ("GitHub", ASTRA_MODEL), "grok": ("GitHub", GROK_MODEL),
            "deepseek": ("Foundry", DEEPSEEK_MODEL)}
BUILTIN_FAMILIES = {"sol6", "astra", "grok"}
ROLES = {"coordinator", "builder", "reviewer", "validator"}
class AssignmentBlocked(RuntimeError): pass

def _text(v: Any, name: str) -> str:
    if not isinstance(v, str) or not v.strip(): raise ValueError(f"{name} must be nonempty text")
    return v.strip()

def _timestamp(v: Any = None) -> str:
    if v is None: return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    s = _text(v, "timestamp"); s = s[:-1] + "+00:00" if s.endswith("Z") else s
    try: d = datetime.fromisoformat(s)
    except ValueError as e: raise ValueError("timestamp must be ISO-8601") from e
    if d.tzinfo is None: raise ValueError("timestamp must include timezone")
    return d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _time(v: str): return datetime.fromisoformat(_timestamp(_text(v, "timestamp")).replace("Z", "+00:00"))

def _atomic(path: Path, value: dict[str, Any]):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(value, f, indent=2, sort_keys=True); f.write("\n"); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists(): tmp.unlink()

@contextmanager
def _lock(path: Path):
    lock = path.with_name(path.name + ".lock"); lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+b") as f:
        if os.name == "nt":
            import msvcrt
            f.seek(0, 2)
            if f.tell() == 0: f.write(b"0"); f.flush()
            f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            try: yield
            finally: f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try: yield
            finally: fcntl.flock(f.fileno(), fcntl.LOCK_UN)

def _new_state(): return {"schema_version": STATE_VERSION, "next_slot": 0, "assignments": {}, "events": [], "next_event_id": 1}

ALLOCATION_FIELDS = (
    "assignment_id", "task_id", "task_class", "acceptance_boundary", "required_context",
    "large_context", "path", "explicit_model", "explicit_provider", "explicit_role",
    "selection_reason", "selected_role", "selected_family", "selected_provider",
    "selected_runtime_id", "selected_model", "route_evidence", "eligibility", "created_at",
)
CURRENT_FIELDS = ALLOCATION_FIELDS + ("paid_policy", "intent_evidence", "capacity_evidence")


def _counter(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("attempts and reassignments must be strict nonnegative integers")
    return value


def _outcome_payload(outcome, actual_provider, actual_runtime_id, actual_model, attempts,
                     reassignments, evidence, semantics, target_revision, target_environment):
    outcome = _text(outcome, "outcome")
    if not isinstance(evidence, list) or any(not isinstance(x, str) or not x.strip() for x in evidence):
        raise ValueError("evidence must be a list of nonempty strings")
    for value, name in ((actual_provider, "actual provider"), (actual_runtime_id, "actual runtime"),
                        (actual_model, "actual model"), (target_revision, "target revision"),
                        (target_environment, "target environment")):
        if value is not None: _text(value, name)
    if semantics != "cumulative": raise ValueError("only cumulative counters are supported")
    if actual_runtime_id not in {None, "unknown"} and actual_provider != "GitHub" and "/" not in actual_runtime_id:
        raise AssignmentBlocked("actual Foundry runtime_id must be qualified")
    verified = outcome.lower() in {"accepted", "verified", "passed", "success"} and bool(evidence) and target_revision not in {None, "unknown"} and target_environment not in {None, "unknown"}
    return {"outcome": outcome, "actual_provider": actual_provider or "unknown",
            "actual_runtime_id": actual_runtime_id or "unknown", "actual_model": actual_model or "unknown",
            "attempts": None if attempts is None else _counter(attempts),
            "reassignments": None if reassignments is None else _counter(reassignments),
            "evidence": deepcopy(evidence), "semantics": semantics, "target_revision": target_revision,
            "target_environment": target_environment, "verification": "verified" if verified else "unverified"}


def _project(allocation, events):
    result = {key: deepcopy(allocation[key]) for key in
              (CURRENT_FIELDS if "paid_policy" in allocation else ALLOCATION_FIELDS)}
    result.update(attempts=None, reassignments=None, outcome="unknown", verification="unverified",
                  actual_provider="unknown", actual_runtime_id="unknown", actual_model="unknown",
                  evidence=[], target_revision=None, target_environment=None, admission_status="unknown")
    for event in events:
        if event["assignment_id"] != result["assignment_id"]: continue
        result["at"] = event["at"]
        if event["type"] == "outcome":
            payload = _outcome_payload(*(event.get(k) for k in (
                "outcome", "actual_provider", "actual_runtime_id", "actual_model", "attempts",
                "reassignments", "evidence", "semantics", "target_revision", "target_environment")))
            for key in ("attempts", "reassignments"):
                if payload[key] is None:
                    payload[key] = result[key]
                elif result[key] is not None and payload[key] < result[key]:
                    raise AssignmentBlocked("cumulative counters must be nondecreasing")
            result.update(payload)
        elif event["type"] == "admitted":
            result.update(admission_status="admitted", admitted_at=event["at"], admission_evidence=deepcopy(event.get("evidence")))
        elif event["type"] in {"admission-blocked", "reuse-blocked"}:
            result.update(admission_status="blocked", admitted_at=None, admission_evidence=None)
    return result


def _validate_state(state):
    if not isinstance(state, dict) or state.get("schema_version") not in {STATE_VERSION, "4.0"} or not isinstance(state.get("assignments"), dict) or not isinstance(state.get("events"), list):
        raise ValueError("invalid assignment state")
    legacy = state["schema_version"] == "4.0"
    fields = ALLOCATION_FIELDS if legacy else CURRENT_FIELDS
    _counter(state.get("next_slot")); _counter(state.get("next_event_id"))
    seen = set(); allocated = set(); previous = None
    for aid, allocation in state["assignments"].items():
        if not isinstance(allocation, dict) or any(k not in allocation for k in fields) or allocation["assignment_id"] != aid:
            raise ValueError("invalid allocation metadata")
        _time(allocation["created_at"])
        _request({key: allocation[key] for key in ("assignment_id", "task_id", "task_class", "acceptance_boundary", "required_context", "large_context", "path", "explicit_model", "explicit_provider", "explicit_role")} | {"candidates": allocation["eligibility"], "paid_policy": allocation.get("paid_policy"), "intent_evidence": allocation.get("intent_evidence")}, legacy=legacy)
        for key in ("selected_role", "selected_family", "selected_provider", "selected_runtime_id", "selected_model", "selection_reason"):
            _text(allocation[key], key)
        route = _candidate({"role": allocation["selected_role"], "family": allocation["selected_family"],
                            "provider": allocation["selected_provider"], "runtime_id": allocation["selected_runtime_id"],
                            "route_evidence": allocation["route_evidence"],
                            "capacity_evidence": allocation.get("capacity_evidence")}, allocation["required_context"],
                           allocation["explicit_model"], allocation["explicit_provider"], allocation["explicit_role"],
                           allocation["large_context"], allocation["path"], allocation.get("paid_policy"),
                           allocation.get("intent_evidence"), allocation["task_class"], legacy=legacy)
        if allocation["selected_model"] != (LEGACY_FAMILIES if legacy else FAMILIES)[route["family"]][1]: raise ValueError("invalid selected model")
        if allocation["large_context"] and route["provider"] == "Foundry": raise ValueError("large-context excludes Foundry")
        if legacy and route["family"] in {"sol", "astra"} and allocation["explicit_model"] != route["runtime_id"]:
            raise ValueError(f"{route['family'].capitalize()} requires explicit runtime")
        if allocation["explicit_model"] not in {None, route["runtime_id"]} or allocation["explicit_provider"] not in {None, route["provider"]}:
            raise ValueError("selected route conflicts with explicit request")
        normalized = []
        for item in allocation["eligibility"]:
            if not isinstance(item, dict) or not isinstance(item.get("eligible"), bool): raise ValueError("invalid eligibility")
            normalized.append({**item, "available": item["eligible"], "authorized": item["eligible"]})
        pool = _pool(normalized, allocation["required_context"], allocation["explicit_model"], allocation["explicit_provider"], allocation["explicit_role"], allocation["large_context"], allocation["path"], allocation.get("paid_policy"), allocation.get("intent_evidence"), allocation["task_class"], legacy=legacy)
        selected = _match(allocation, pool)
        if (not selected or not selected["eligible"] or
            selected["route_evidence"] != allocation["route_evidence"] or
            (not legacy and selected["capacity_evidence"] != allocation["capacity_evidence"])):
            raise ValueError("selected route must match eligible allocation evidence")
    for event in state["events"]:
        if not isinstance(event, dict): raise ValueError("invalid event")
        event_id = _text(event.get("event_id"), "event id")
        if event_id in seen: raise ValueError("event_id collision")
        current = _time(event.get("at"))
        if previous is not None and current < previous: raise ValueError("event timestamps must be monotonic")
        aid = event.get("assignment_id")
        if aid not in state["assignments"]: raise ValueError("event requires registered assignment")
        if event.get("type") == "allocated":
            if aid in allocated or current != _time(state["assignments"][aid]["created_at"]): raise ValueError("invalid allocation event")
            allocated.add(aid)
        elif aid not in allocated or event.get("type") not in {"outcome", "admitted", "admission-blocked", "reuse-blocked"}:
            raise ValueError("invalid assignment event")
        allocation = state["assignments"][aid]
        if event["type"] == "allocated" and event.get("identity") != {key: allocation[key] for key in ("task_id", "task_class", "acceptance_boundary", "required_context", "large_context")}:
            raise ValueError("allocation event identity mismatch")
        if event["type"] == "outcome":
            payload = _outcome_payload(*(event.get(k) for k in (
                "outcome", "actual_provider", "actual_runtime_id", "actual_model", "attempts",
                "reassignments", "evidence", "semantics", "target_revision", "target_environment")))
            if any(key not in event or event[key] != value for key, value in payload.items()):
                raise ValueError("outcome event differs from validated evidence")
        if event["type"] == "admitted":
            if event.get("runtime_id") != allocation["selected_runtime_id"]: raise ValueError("admission runtime mismatch")
            evidence = event.get("evidence")
            if not isinstance(evidence, dict) or evidence.get("verified") is not True or evidence.get("source") not in {"host", "local"} or evidence.get("runtime_id") != allocation["selected_runtime_id"]:
                raise ValueError("admission evidence required")
        if event["type"] in {"admission-blocked", "reuse-blocked"}: _text(event.get("reason"), "block reason")
        seen.add(event_id); previous = current
    if allocated != set(state["assignments"]): raise ValueError("allocation event required")
    for allocation in state["assignments"].values(): _project(allocation, state["events"])


def _save(path, state):
    _validate_state(state)
    _atomic(path, state)


def _load(path: Path):
    if not path.exists(): return _new_state()
    try: state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e: raise ValueError(f"invalid assignment state: {e}") from e
    _validate_state(state)
    # Older v4 files may contain mutable snapshots. Never trust those fields.
    fields = ALLOCATION_FIELDS if state["schema_version"] == "4.0" else CURRENT_FIELDS
    state["assignments"] = {aid: {key: value[key] for key in fields} for aid, value in state["assignments"].items()}
    return state

def _event(state: dict[str, Any], typ: str, aid: str, at: str, event_id: str | None = None, **extra):
    event_id = event_id or f"e-{state.get('next_event_id', 1)}"
    if any(e.get("event_id") == event_id for e in state["events"]): raise AssignmentBlocked("event_id already exists")
    try: n = int(event_id[2:]) if event_id.startswith("e-") else state.get("next_event_id", 1)
    except ValueError: n = state.get("next_event_id", 1)
    state["next_event_id"] = max(state.get("next_event_id", 1), n + 1)
    state["events"].append({"event_id": event_id, "type": typ, "assignment_id": aid, "at": at, **deepcopy(extra)})
    return event_id

def _runtime(raw: dict[str, Any], provider: str, family: str, model: str) -> str:
    runtime = _text(raw.get("runtime_id"), "candidate runtime_id")
    if runtime != model or provider != "GitHub":
        if "/" not in runtime or runtime.startswith("/") or runtime.endswith("/") or "\\" in runtime:
            raise AssignmentBlocked("runtime_id must be connection-qualified (except bare GitHub runtime)")
        if not runtime.endswith("/" + model): raise AssignmentBlocked("runtime_id does not match model")
    return runtime

def _candidate(raw: Any, required: int, explicit_model: str|None, explicit_provider: str|None, explicit_role: str|None, large: bool, path: str,
               paid_policy=None, intent_evidence=None, task_class=None, *, legacy=False):
    if not isinstance(raw, dict): raise ValueError("candidate must be an object")
    role = _text(raw.get("role"), "candidate role"); family = _text(raw.get("family"), "candidate family").lower(); provider = _text(raw.get("provider"), "candidate provider")
    if role not in ROLES: raise AssignmentBlocked("unknown candidate role")
    families = LEGACY_FAMILIES if legacy else FAMILIES
    if family not in families: raise AssignmentBlocked("unknown or retired candidate family")
    if explicit_role and role != explicit_role: raise AssignmentBlocked("explicit role conflict")
    expected_provider, model = families[family]
    if provider != expected_provider: raise AssignmentBlocked("family/provider correspondence is invalid")
    runtime = _runtime(raw, provider, family, model)
    evidence = raw.get("route_evidence")
    if not isinstance(evidence, dict) or evidence.get("verified") is not True or evidence.get("source") not in {"host", "local"}: raise AssignmentBlocked("verified host or local route evidence is required")
    if evidence.get("runtime_id") != runtime or evidence.get("provider", provider) != provider or evidence.get("family", family) != family: raise AssignmentBlocked("route evidence does not bind runtime identity")
    if not legacy and provider == "GitHub" and (
        evidence.get("provider") != provider or evidence.get("family") != family or
        evidence.get("source") != "host"
    ):
        raise AssignmentBlocked("fresh route needs host-backed provider and family evidence for built-in models")
    cap = raw.get("context_capacity"); fit = isinstance(cap, int) and not isinstance(cap, bool) and cap >= required
    capacity_evidence = raw.get("capacity_evidence")
    reasons = []
    if raw.get("available") is not True: reasons.append("unavailable")
    if raw.get("authorized") is not True: reasons.append("unauthorized")
    if not fit: reasons.append("context-fit-unknown-or-insufficient")
    if legacy:
        if family == "sol" and explicit_model != runtime: reasons.append("explicit-user-override-required"); fit = False
        if family == "astra" and explicit_model != runtime: reasons.append("explicit-policy-route-required"); fit = False
    else:
        if family in BUILTIN_FAMILIES and paid_policy != {"builtin_uncapped_opt_in": True, "source": "local"}:
            reasons.append("explicit-local-builtin-opt-in-required"); fit = False
        kind = intent_evidence.get("kind") if isinstance(intent_evidence, dict) else None
        source = intent_evidence.get("source") if isinstance(intent_evidence, dict) else None
        reference = intent_evidence.get("evidence_reference") if isinstance(intent_evidence, dict) else None
        if family == "grok" and not (kind == "urgent" and source == "user" and isinstance(reference, str) and reference.strip()):
            reasons.append("explicit-urgent-user-evidence-required"); fit = False
        if family == "astra" and not (kind in {"hard-diagnosis", "failed-first-fix"} and source in {"user", "host"} and isinstance(reference, str) and reference.strip()):
            reasons.append("hard-diagnosis-or-failed-first-fix-evidence-required"); fit = False
        if family == "deepseek" and not (
            path == "deepseek-pilot" and role == "builder" and task_class == "deployment-repair"
            and kind == "deployment-repair-pilot" and source == "user"
            and isinstance(reference, str) and reference.strip()
            and all(intent_evidence.get(k) is True for k in ("reproduction", "ci", "live_verification"))
            and type(intent_evidence.get("bounded_attempts")) is int and intent_evidence["bounded_attempts"] == 1
        ):
            reasons.append("bounded-deployment-repair-pilot-required"); fit = False
        if family == "deepseek" and not (
            isinstance(capacity_evidence, dict) and capacity_evidence.get("source") == "host"
            and capacity_evidence.get("verified") is True
            and capacity_evidence.get("runtime_id") == runtime
            and capacity_evidence.get("context_capacity") == cap
        ):
            reasons.append("host-verified-pilot-capacity-required"); fit = False
        if path == "deepseek-pilot" and family != "deepseek":
            reasons.append("pilot-route-conflict"); fit = False
    if large and provider == "Foundry": reasons.append("large-context-excludes-Foundry"); fit = False
    if path in {"coordinator", "astra"} and large and provider == "Foundry": fit = False
    if explicit_model is not None and runtime != explicit_model: reasons.append("explicit-runtime-conflict"); fit = False
    if explicit_provider is not None and provider != explicit_provider: reasons.append("explicit-provider-conflict"); fit = False
    return {"role": role, "family": family, "provider": provider, "runtime_id": runtime, "route_evidence": deepcopy(evidence), "capacity_evidence": deepcopy(capacity_evidence), "context_capacity": cap if isinstance(cap, int) else "unknown", "eligible": raw.get("available") is True and raw.get("authorized") is True and fit, "reasons": reasons or ["context-fit-confirmed"]}

def _identity(a): return (a["task_id"], a["task_class"], a["acceptance_boundary"], a["required_context"], a["large_context"])

def _request(kwargs, *, legacy=False):
    allowed = {"assignment_id", "task_id", "task_class", "acceptance_boundary", "required_context", "candidates", "explicit_model", "explicit_provider", "explicit_role", "large_context", "path", "now", "paid_policy", "intent_evidence"}
    if set(kwargs) - allowed: raise ValueError("unknown request fields: " + ", ".join(sorted(set(kwargs) - allowed)))
    aid, tid, tc, ab = (_text(kwargs[k], k.replace("_", " ")) for k in ("assignment_id", "task_id", "task_class", "acceptance_boundary"))
    rc = kwargs.get("required_context")
    if not isinstance(rc, int) or isinstance(rc, bool) or rc <= 0: raise ValueError("required_context must be a positive estimate")
    candidates = kwargs.get("candidates")
    if not isinstance(candidates, list) or not candidates: raise AssignmentBlocked("no model candidates supplied")
    explicit_model = _text(kwargs["explicit_model"], "explicit model") if kwargs.get("explicit_model") is not None else None
    explicit_provider = _text(kwargs["explicit_provider"], "explicit provider") if kwargs.get("explicit_provider") is not None else None
    explicit_role = _text(kwargs["explicit_role"], "explicit role") if kwargs.get("explicit_role") is not None else None
    path = kwargs.get("path", "direct"); large = kwargs.get("large_context", False)
    if not isinstance(large, bool): raise ValueError("large_context must be boolean")
    if path not in ({"direct", "coordinator", "astra"} if legacy else {"direct", "coordinator", "astra", "deepseek-pilot"}): raise ValueError("invalid assignment path")
    policy, intent = kwargs.get("paid_policy"), kwargs.get("intent_evidence")
    if policy is not None and (not isinstance(policy, dict) or set(policy) != {"builtin_uncapped_opt_in", "source"} or type(policy["builtin_uncapped_opt_in"]) is not bool or policy["source"] != "local"):
        raise ValueError("invalid paid_policy; explicit local opt-in required for uncapped built-in use")
    if intent is not None and not isinstance(intent, dict): raise ValueError("intent_evidence must be an object")
    return aid, tid, tc, ab, rc, candidates, explicit_model, explicit_provider, explicit_role, large, path, policy, intent

def _pool(candidates, rc, em, ep, er, large, path, policy=None, intent=None, task_class=None, *, legacy=False):
    items = [_candidate(c, rc, em, ep, er, large, path, policy, intent, task_class, legacy=legacy) for c in candidates]
    routes = [(x["family"], x["provider"], x["runtime_id"]) for x in items]
    if len(set(routes)) != len(routes): raise AssignmentBlocked("duplicate or conflicting pool routes are rejected")
    if len({x["role"] for x in items}) != 1: raise AssignmentBlocked("pool candidates must perform the same task role")
    return items


def _resume_check(allocation, identity, em, ep, er, path):
    if _identity(allocation) != identity or allocation["path"] != path:
        raise AssignmentBlocked("assignment identity mismatch; checkpoint required")
    for key, value in (("explicit_model", em), ("explicit_provider", ep), ("explicit_role", er)):
        if value is not None and allocation.get(key) != value:
            raise AssignmentBlocked("changed explicit request conflicts with immutable assignment")


def _match(allocation, items):
    return next((x for x in items if all(x[key] == allocation["selected_" + key]
                for key in ("runtime_id", "role", "provider", "family"))), None)


def select_assignment(state_path: str|Path, **kwargs):
    aid, tid, tc, ab, rc, candidates, em, ep, er, large, path, policy, intent = _request(kwargs)
    file = Path(state_path).expanduser().resolve()
    with _lock(file):
        state = _load(file); at = _timestamp(kwargs.get("now")); prior = state["assignments"].get(aid)
        if state["schema_version"] == "4.0":
            raise AssignmentBlocked("v4 assignments are historical; fresh selection requires a new v5 state file")
        if prior:
            _resume_check(prior, (tid, tc, ab, rc, large), em, ep, er, path)
            if policy != prior["paid_policy"] or intent != prior["intent_evidence"]:
                raise AssignmentBlocked("changed policy or intent conflicts with immutable assignment")
            current = _pool(candidates, rc, prior["explicit_model"], prior["explicit_provider"], prior["explicit_role"], large, path, policy, intent, tc)
            match = _match(prior, current)
            if not match or not match["eligible"]:
                _event(state, "reuse-blocked", aid, at, reason="current admission failed"); _save(file, state)
                raise AssignmentBlocked("resume blocked; historical assignment preserved")
            return _project(prior, state["events"])
        if path == "deepseek-pilot" and any(a["task_id"] == tid for a in state["assignments"].values()):
            raise AssignmentBlocked("DeepSeek pilot task already has an immutable assignment; a second attempt is forbidden")
        items = _pool(candidates, rc, em, ep, er, large, path, policy, intent, tc)
        eligible = [x for x in items if x["eligible"]]
        if not eligible:
            detail = ("large-context-requires-GitHub" if large else
                      "pilot requires host-verified known capacity, authorization and a bounded repair contract"
                      if path == "deepseek-pilot" else
                      "no authorized, available, verified model has confirmed sufficient context capacity")
            raise AssignmentBlocked(detail)
        if em is not None:
            chosen = next((x for x in eligible if x["runtime_id"] == em), None)
            reason = "explicit-verified-route"
        else:
            kind = intent.get("kind") if isinstance(intent, dict) else None
            preference = ("deepseek",) if path == "deepseek-pilot" else (
                ("grok", "sol6", "luna") if kind == "urgent" else
                ("astra", "sol6", "luna") if kind in {"hard-diagnosis", "failed-first-fix"} else
                ("sol6", "luna"))
            chosen = next((x for family in preference for x in eligible if x["family"] == family), None)
            reason = "context-fit-policy-preference"
        if not chosen: raise AssignmentBlocked("explicit runtime, role, or provider is unavailable, unauthorized, unverified, or does not fit")
        result = {"assignment_id": aid, "task_id": tid, "task_class": tc, "acceptance_boundary": ab, "required_context": rc, "large_context": large, "path": path, "explicit_model": em, "explicit_provider": ep, "explicit_role": er, "paid_policy": deepcopy(policy), "intent_evidence": deepcopy(intent), "capacity_evidence": deepcopy(chosen["capacity_evidence"]), "selection_reason": reason, "selected_role": chosen["role"], "selected_family": chosen["family"], "selected_provider": chosen["provider"], "selected_runtime_id": chosen["runtime_id"], "selected_model": FAMILIES[chosen["family"]][1], "route_evidence": deepcopy(chosen["route_evidence"]), "eligibility": items, "created_at": at}
        state["assignments"][aid] = result
        _event(state, "allocated", aid, at, identity={"task_id": tid, "task_class": tc, "acceptance_boundary": ab, "required_context": rc, "large_context": large})
        _save(file, state)
        return _project(result, state["events"])

def admit_assignment(state_path: str|Path, **kwargs):
    aid, tid, tc, ab, rc, candidates, em, ep, er, large, path, policy, intent = _request(kwargs)
    file = Path(state_path).expanduser().resolve()
    with _lock(file):
        state = _load(file); at = _timestamp(kwargs.get("now")); allocation = state["assignments"].get(aid)
        if state["schema_version"] == "4.0": raise AssignmentBlocked("v4 assignments cannot be freshly admitted")
        if not allocation: raise AssignmentBlocked("assignment must be allocated before admission")
        _resume_check(allocation, (tid, tc, ab, rc, large), em, ep, er, path)
        if policy != allocation["paid_policy"] or intent != allocation["intent_evidence"]:
            raise AssignmentBlocked("changed policy or intent conflicts with immutable assignment")
        if allocation["selected_family"] == "deepseek" and any(
            event["type"] == "admitted" and event["assignment_id"] == aid for event in state["events"]
        ):
            raise AssignmentBlocked("DeepSeek pilot permits only one fresh implementation admission")
        items = _pool(candidates, rc, allocation["explicit_model"], allocation["explicit_provider"], allocation["explicit_role"], large, path, policy, intent, tc)
        match = _match(allocation, items)
        if not match or not match["eligible"]:
            _event(state, "admission-blocked", aid, at, reason="current availability/authorization/context or route mismatch")
            _save(file, state)
            raise AssignmentBlocked("admission blocked; immutable allocation preserved")
        _event(state, "admitted", aid, at, runtime_id=allocation["selected_runtime_id"], evidence=match["route_evidence"])
        _save(file, state)
        result = _project(allocation, state["events"])
        # Only a fresh admission returns a handoff. This is not an execution or spending lease.
        result["handoff"] = {key: result[key] for key in (
            "assignment_id", "task_id", "selected_role", "selected_family", "selected_provider", "selected_runtime_id")}
        return result

def record_outcome(state_path: str|Path, assignment_id: str, *, outcome: Any, event_id: str,
                   actual_provider: str|None=None, actual_runtime_id: str|None=None,
                   actual_model: str|None=None, attempts: int|None=None, reassignments: int|None=None,
                   timestamp: str|None=None, evidence: list[str]|None=None, task_id: str|None=None,
                   cumulative: bool=False, target_revision: str|None=None, target_environment: str|None=None):
    aid = _text(assignment_id, "assignment id"); event_id = _text(event_id, "event id")
    if cumulative is not True: raise ValueError("cumulative must be true; counters are totals, not deltas")
    payload = _outcome_payload(outcome, actual_provider, actual_runtime_id, actual_model,
                               attempts, reassignments, [] if evidence is None else evidence,
                               "cumulative", target_revision, target_environment)
    file = Path(state_path).expanduser().resolve()
    with _lock(file):
        state = _load(file); allocation = state["assignments"].get(aid)
        if not allocation: raise AssignmentBlocked("assignment identity is not registered")
        if task_id is not None and task_id != allocation["task_id"]: raise AssignmentBlocked("outcome task identity mismatch")
        prior = next((e for e in state["events"] if e["event_id"] == event_id), None)
        if prior:
            incoming = {"event_id": event_id, "type": "outcome", "assignment_id": aid, **payload}
            if {k: v for k, v in prior.items() if k != "at"} != incoming or (timestamp is not None and _time(timestamp) != _time(prior["at"])):
                raise AssignmentBlocked("conflicting replay for event_id")
            replay_events = state["events"][:state["events"].index(prior) + 1]
            return _project(allocation, replay_events)
        at = _timestamp(timestamp)
        _event(state, "outcome", aid, at, event_id=event_id, **payload)
        _save(file, state)
        return _project(allocation, state["events"])

def export_state(state_path: str|Path, cutoff: str|None=None):
    state = _load(Path(state_path).expanduser().resolve()); boundary = _time(cutoff) if cutoff else None
    events = [e for e in state["events"] if boundary is None or _time(e.get("at", e.get("timestamp"))) < boundary]
    result = deepcopy(state); result["events"] = events
    result["assignments"] = {k: _project(v, events) for k, v in state["assignments"].items() if boundary is None or _time(v["created_at"]) < boundary}
    result.pop("next_slot", None); result.pop("next_event_id", None)
    result["export"] = {"metadata": {"cutoff": _timestamp(cutoff) if cutoff else None, "timezone": "UTC"}, "cursor": {"event_count": len(events), "historical": boundary is not None}}
    return result

def assignment_from_export(data, assignment_id):
    """Validate an as-of export, then derive a typed snapshot from its events."""
    if not isinstance(data, dict) or not isinstance(data.get("export"), dict): raise ValueError("assignment export required")
    metadata = data["export"].get("metadata")
    if not isinstance(metadata, dict): raise ValueError("export metadata required")
    cutoff = _timestamp(_text(metadata.get("cutoff"), "export cutoff"))
    state = {**data, "next_slot": 0, "next_event_id": 1}
    _validate_state(state)
    if any(_time(event["at"]) >= _time(cutoff) for event in state["events"]):
        raise ValueError("event is not before export cutoff")
    for aid, allocation in state["assignments"].items():
        if _project(allocation, state["events"]) != allocation: raise ValueError("export snapshot differs from retained events")
    if assignment_id not in state["assignments"]: raise ValueError("assignment not present at cutoff")
    return {**deepcopy(state["assignments"][assignment_id]), "snapshot_cutoff": cutoff}


def _input(a):
    data = json.loads(a.file.read_text(encoding="utf-8-sig")) if a.file else json.loads(sys.stdin.read() or "{}")
    if not isinstance(data, dict): raise ValueError("request must be a JSON object")
    return data


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["select", "admit", "record", "export"])
    p.add_argument("--state", type=Path, default=Path(os.environ.get("COPILOT_HOME") or Path.home()/".copilot")/"token-mizer"/"model-assignments.json")
    p.add_argument("--file", type=Path); a = p.parse_args(argv)
    try:
        d = _input(a)
        actions = {"select": select_assignment, "admit": admit_assignment, "record": record_outcome, "export": export_state}
        result = actions[a.action](a.state, **d)
        print(json.dumps(result, indent=2, sort_keys=True)); return 0
    except (AssignmentBlocked, ValueError, OSError, TypeError, KeyError) as e:
        print(json.dumps({"error": type(e).__name__, "message": str(e)}), file=sys.stderr); return 2
if __name__ == "__main__": raise SystemExit(main())
