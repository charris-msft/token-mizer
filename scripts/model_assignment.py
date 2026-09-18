#!/usr/bin/env python3
"""Fail-closed model allocation, admission, measurement, and JSON CLI."""
from __future__ import annotations
import argparse, json, os, re, sys, tempfile
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LUNA_MODEL = "gpt-5.6-luna"
FLASH_MODEL = "gemini-3.8-flash"
SOL_MODEL = "gpt-5.6-sol"
STATE_VERSION = "4.0"
FAMILIES = {"luna": ("Foundry", LUNA_MODEL), "sol": ("Foundry", SOL_MODEL), "flash": ("GitHub", FLASH_MODEL)}
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

def _time(v: str): return datetime.fromisoformat(_timestamp(v).replace("Z", "+00:00"))

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

def _load(path: Path):
    if not path.exists(): return _new_state()
    try: d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e: raise ValueError(f"invalid assignment state: {e}")
    if not isinstance(d, dict) or d.get("schema_version") != STATE_VERSION or not isinstance(d.get("assignments"), dict) or not isinstance(d.get("events"), list): raise ValueError("invalid assignment state")
    seen = set(); previous = None
    for event in d["events"]:
        if not isinstance(event, dict) or not isinstance(event.get("event_id"), str): raise ValueError("invalid event")
        if event["event_id"] in seen: raise ValueError("event_id collision")
        stamp = event.get("at", event.get("timestamp")); current = _time(stamp) if stamp else None
        if current is None: raise ValueError("event timestamp required")
        if previous is not None and current < previous: raise ValueError("event timestamps must be monotonic")
        seen.add(event["event_id"]); previous = current
    return d

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
    if provider == "GitHub":
        if runtime != model: raise AssignmentBlocked("GitHub runtime_id must be the bare host runtime identity")
    else:
        if "/" not in runtime or runtime.startswith("/") or runtime.endswith("/") or "\\" in runtime: raise AssignmentBlocked("Foundry runtime_id must be connection-qualified")
        if not runtime.endswith("/" + model): raise AssignmentBlocked("runtime_id does not match model")
    if any(x in runtime.lower() for x in ("guid", "provider_guid", "connection_id")) or re.fullmatch(r"[0-9a-f-]{32,}", runtime, re.I): raise AssignmentBlocked("real or spoofed provider identity is not publishable")
    return runtime

def _candidate(raw: Any, required: int, explicit_model: str|None, explicit_provider: str|None, explicit_role: str|None, large: bool, path: str):
    if not isinstance(raw, dict): raise ValueError("candidate must be an object")
    role = _text(raw.get("role"), "candidate role"); family = _text(raw.get("family"), "candidate family").lower(); provider = _text(raw.get("provider"), "candidate provider")
    if role not in ROLES: raise AssignmentBlocked("unknown candidate role")
    if family not in FAMILIES: raise AssignmentBlocked("unknown candidate family")
    if explicit_role and role != explicit_role: raise AssignmentBlocked("explicit role conflict")
    expected_provider, model = FAMILIES[family]
    if provider != expected_provider: raise AssignmentBlocked("family/provider correspondence is invalid")
    runtime = _runtime(raw, provider, family, model)
    evidence = raw.get("route_evidence")
    if not isinstance(evidence, dict) or evidence.get("verified") is not True or evidence.get("source") not in {"host", "local"}: raise AssignmentBlocked("verified host or local route evidence is required")
    if evidence.get("runtime_id") != runtime or evidence.get("provider", provider) != provider or evidence.get("family", family) != family: raise AssignmentBlocked("route evidence does not bind runtime identity")
    cap = raw.get("context_capacity"); fit = isinstance(cap, int) and not isinstance(cap, bool) and cap >= required
    reasons = []
    if raw.get("available") is not True: reasons.append("unavailable")
    if raw.get("authorized") is not True: reasons.append("unauthorized")
    if not fit: reasons.append("context-fit-unknown-or-insufficient")
    if family == "sol" and explicit_model != runtime: reasons.append("explicit-user-override-required"); fit = False
    if large and provider == "Foundry": reasons.append("large-context-excludes-Foundry"); fit = False
    if path in {"coordinator", "astra"} and large and provider == "Foundry": fit = False
    if explicit_model is not None and runtime != explicit_model: reasons.append("explicit-runtime-conflict"); fit = False
    if explicit_provider is not None and provider != explicit_provider: reasons.append("explicit-provider-conflict"); fit = False
    return {"role": role, "family": family, "provider": provider, "runtime_id": runtime, "route_evidence": deepcopy(evidence), "context_capacity": cap if isinstance(cap, int) else "unknown", "eligible": raw.get("available") is True and raw.get("authorized") is True and fit, "reasons": reasons or ["context-fit-confirmed"]}

def _identity(a): return (a["task_id"], a["task_class"], a["acceptance_boundary"], a["required_context"], a["large_context"])

def _request(kwargs):
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
    if path not in {"direct", "coordinator", "astra"}: raise ValueError("invalid assignment path")
    return aid, tid, tc, ab, rc, candidates, explicit_model, explicit_provider, explicit_role, large, path

def select_assignment(state_path: str|Path, **kwargs):
    aid, tid, tc, ab, rc, candidates, em, ep, er, large, path = _request(kwargs); at = _timestamp(kwargs.get("now")); file = Path(state_path).expanduser().resolve()
    with _lock(file):
        state = _load(file); prior = state["assignments"].get(aid)
        if prior:
            if _identity(prior) != (tid, tc, ab, rc, large): raise AssignmentBlocked("assignment identity mismatch; checkpoint required")
            if em is not None and prior.get("explicit_model") != em: raise AssignmentBlocked("changed explicit runtime conflict; checkpoint required")
            if ep is not None and prior.get("explicit_provider") != ep: raise AssignmentBlocked("changed explicit provider conflict; checkpoint required")
            current = [_candidate(c, rc, prior.get("explicit_model"), prior.get("explicit_provider"), prior.get("explicit_role"), large, path) for c in candidates]
            match = next((x for x in current if x["runtime_id"] == prior["selected_runtime_id"] and x["role"] == prior["selected_role"] and x["provider"] == prior["selected_provider"] and x["family"] == prior["selected_family"]), None)
            if not match or not match["eligible"]:
                _event(state, "reuse-blocked", aid, at, reason="current admission failed"); _atomic(file, state)
                raise AssignmentBlocked("resume blocked; historical assignment preserved")
            return deepcopy(prior)
        items = [_candidate(c, rc, em, ep, er, large, path) for c in candidates]
        if len({x["role"] for x in items}) != len(items): raise AssignmentBlocked("duplicate or conflicting pool roles are rejected")
        eligible = [x for x in items if x["eligible"]]
        if not eligible:
            detail = "large-context-requires-GitHub" if large else "no authorized, available, verified model has confirmed sufficient context capacity"
            raise AssignmentBlocked(detail)
        if em is not None: chosen = next((x for x in eligible if x["runtime_id"] == em), None); reason = "explicit-user-model"
        elif er is not None: chosen = next((x for x in eligible if x["role"] == er), None); reason = "explicit-role"
        else:
            flash = [x for x in eligible if x["family"] == "flash"]; luna = [x for x in eligible if x["family"] == "luna"]
            pool = flash + luna; chosen = pool[state.get("next_slot", 0) % len(pool)] if pool else None
            if flash and luna: state["next_slot"] += 1; reason = "alternating-context-fitting-pool"
            else: reason = "only-context-fitting-authorized-candidate"
        if not chosen: raise AssignmentBlocked("explicit runtime, role, or provider is unavailable, unauthorized, unverified, or does not fit")
        result = {"assignment_id": aid, "task_id": tid, "task_class": tc, "acceptance_boundary": ab, "required_context": rc, "large_context": large, "path": path, "explicit_model": em, "explicit_provider": ep, "explicit_role": er, "selection_reason": reason, "selected_role": chosen["role"], "selected_family": chosen["family"], "selected_provider": chosen["provider"], "selected_runtime_id": chosen["runtime_id"], "selected_model": FAMILIES[chosen["family"]][1], "route_evidence": deepcopy(chosen["route_evidence"]), "eligibility": items, "admitted": False, "attempts": 0, "reassignments": 0, "outcome": "unknown", "created_at": at}
        state["assignments"][aid] = result; _event(state, "allocated", aid, at, identity={"task_id": tid, "task_class": tc, "acceptance_boundary": ab, "required_context": rc, "large_context": large}); _atomic(file, state); return deepcopy(result)

def admit_assignment(state_path: str|Path, **kwargs):
    aid, tid, tc, ab, rc, candidates, em, ep, er, large, path = _request(kwargs); at = _timestamp(kwargs.get("now")); file = Path(state_path).expanduser().resolve()
    with _lock(file):
        state = _load(file); a = state["assignments"].get(aid)
        if not a: raise AssignmentBlocked("assignment must be allocated before admission")
        if _identity(a) != (tid, tc, ab, rc, large): raise AssignmentBlocked("context growth or identity conflict blocks admission; checkpoint required")
        for key, val in (("explicit_model", em), ("explicit_provider", ep), ("explicit_role", er)):
            if val is not None and a.get(key) != val: raise AssignmentBlocked("changed explicit request conflicts with immutable assignment")
        items = [_candidate(c, rc, a.get("explicit_model"), a.get("explicit_provider"), a.get("explicit_role"), large, path) for c in candidates]
        match = next((x for x in items if x["runtime_id"] == a["selected_runtime_id"] and x["role"] == a["selected_role"] and x["provider"] == a["selected_provider"] and x["family"] == a["selected_family"]), None)
        if not match or not match["eligible"]: _event(state, "admission-blocked", aid, at, reason="current availability/authorization/context or route mismatch"); _atomic(file, state); raise AssignmentBlocked("admission blocked; immutable allocation preserved")
        a["admitted"] = True; a["admitted_at"] = at; a["admission_evidence"] = deepcopy(match["route_evidence"]); _event(state, "admitted", aid, at, runtime_id=a["selected_runtime_id"]); _atomic(file, state); return deepcopy(a)

def spawn_handoff(state_path: str|Path, assignment_id: str, *, task_id: str, handoff_id: str, timestamp: str|None = None):
    aid = _text(assignment_id, "assignment id"); tid = _text(task_id, "task id"); hid = _text(handoff_id, "handoff id"); at = _timestamp(timestamp); file = Path(state_path).expanduser().resolve()
    with _lock(file):
        state = _load(file); a = state["assignments"].get(aid)
        if not a or a["task_id"] != tid or not a.get("admitted"): raise AssignmentBlocked("spawn requires admitted assignment and matching task")
        h = {"handoff_id": hid, "assignment_id": aid, "task_id": tid, "provider": a["selected_provider"], "runtime_id": a["selected_runtime_id"], "family": a["selected_family"], "at": at}
        _event(state, "spawn-handoff", aid, at, handoff_id=hid, runtime_id=h["runtime_id"]); _atomic(file, state); return h

def _named_evidence(outcome: str, evidence: list[str], target_revision: str|None, target_environment: str|None):
    return outcome.lower() in {"accepted", "verified", "passed", "success"} and bool(evidence) and all(e.strip() for e in evidence) and bool(target_revision) and bool(target_environment)

def record_outcome(state_path: str|Path, assignment_id: str, *, outcome: Any, actual_provider: str|None=None, actual_runtime_id: str|None=None, actual_model: str|None=None, attempts: int=0, reassignments: int=0, timestamp: str|None=None, evidence: list[str]|None=None, cutoff: str|None=None, task_id: str|None=None, delta: bool=False, cumulative: bool=False, event_id: str|None=None, target_revision: str|None=None, target_environment: str|None=None):
    aid = _text(assignment_id, "assignment id"); outcome = _text(outcome, "outcome"); at = _timestamp(timestamp); cutoff and _timestamp(cutoff)
    if delta == cumulative: raise ValueError("exactly one of delta or cumulative must be true")
    for v in (attempts, reassignments):
        if isinstance(v, bool) or not isinstance(v, int) or v < 0: raise ValueError("attempts and reassignments must be strict nonnegative integers")
    evidence = list(evidence or [])
    if not all(isinstance(x, str) and x.strip() for x in evidence): raise ValueError("evidence must be a list of nonempty strings")
    file = Path(state_path).expanduser().resolve()
    with _lock(file):
        state = _load(file); selected = state["assignments"].get(aid)
        if not selected: raise AssignmentBlocked("assignment identity is not registered")
        if task_id and task_id != selected["task_id"]: raise AssignmentBlocked("outcome task identity mismatch")
        if event_id:
            prior = next((e for e in state["events"] if e.get("event_id") == event_id), None)
            if prior:
                comparable = {k: v for k, v in prior.items() if k != "at"}; incoming = {"event_id": event_id, "type": "outcome", "assignment_id": aid, "outcome": outcome, "actual_provider": actual_provider or "unknown", "actual_runtime_id": actual_runtime_id or "unknown", "actual_model": actual_model or "unknown", "attempts": attempts, "reassignments": reassignments, "evidence": evidence, "semantics": "delta" if delta else "cumulative", "target_revision": target_revision, "target_environment": target_environment, "verification": "verified" if _named_evidence(outcome, evidence, target_revision, target_environment) else "unverified"}
                if {k: v for k, v in comparable.items() if k != "event_id"} == {k: v for k, v in incoming.items() if k != "event_id"}: return deepcopy(selected)
                raise AssignmentBlocked("conflicting replay for event_id")
        if actual_runtime_id is not None and "/" not in actual_runtime_id and actual_provider != "GitHub": raise AssignmentBlocked("actual Foundry runtime_id must be qualified")
        snapshot = {"attempts": attempts, "reassignments": reassignments, "outcome": outcome, "actual_provider": actual_provider or "unknown", "actual_runtime_id": actual_runtime_id or "unknown", "actual_model": actual_model or "unknown", "evidence": evidence, "semantics": "delta" if delta else "cumulative", "target_revision": target_revision, "target_environment": target_environment, "verification": "verified" if _named_evidence(outcome, evidence, target_revision, target_environment) else "unverified"}
        if delta: selected["attempts"] += attempts; selected["reassignments"] += reassignments
        else:
            if attempts < selected["attempts"] or reassignments < selected["reassignments"]: raise AssignmentBlocked("cumulative counters must be nondecreasing")
            selected["attempts"] = attempts; selected["reassignments"] = reassignments
        selected.update(snapshot); _event(state, "outcome", aid, at, event_id=event_id, **snapshot); _atomic(file, state); return deepcopy(selected)

def export_state(state_path: str|Path, cutoff: str|None=None):
    state = _load(Path(state_path).expanduser().resolve()); boundary = _time(cutoff) if cutoff else None
    events = [e for e in state["events"] if boundary is None or _time(e.get("at", e.get("timestamp"))) < boundary]
    result = deepcopy(state); result["events"] = events
    result["assignments"] = {k: deepcopy(v) for k, v in state["assignments"].items() if boundary is None or _time(v["created_at"]) < boundary}
    result["export"] = {"metadata": {"cutoff": _timestamp(cutoff) if cutoff else None, "timezone": "UTC"}, "cursor": {"event_count": len(events), "historical": False}}
    return result

def _input(a): return json.loads(a.file.read_text(encoding="utf-8")) if a.file else json.loads(sys.stdin.read() or "{}")
def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__); p.add_argument("action", choices=["select", "admit", "spawn", "record", "export"]); p.add_argument("--state", type=Path, default=Path(os.environ.get("COPILOT_HOME", Path.home()/".copilot"))/"token-mizer"/"model-assignments.json"); p.add_argument("--file", type=Path); a = p.parse_args(argv)
    try:
        d = _input(a)
        result = select_assignment(a.state, **d) if a.action == "select" else admit_assignment(a.state, **d) if a.action == "admit" else spawn_handoff(a.state, **d) if a.action == "spawn" else record_outcome(a.state, **d) if a.action == "record" else export_state(a.state, **d)
        print(json.dumps(result, indent=2, sort_keys=True)); return 0
    except (AssignmentBlocked, ValueError, OSError, json.JSONDecodeError) as e:
        print(json.dumps({"error": type(e).__name__, "message": str(e)}), file=sys.stderr); return 2
if __name__ == "__main__": raise SystemExit(main())
