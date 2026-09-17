#!/usr/bin/env python3
"""Durable, fail-closed model assignment policy and public JSON CLI."""
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
STATE_VERSION = "2.0"
KNOWN_ROUTES = {("GitHub", FLASH_MODEL), ("Foundry", LUNA_MODEL), ("Foundry", SOL_MODEL)}
class AssignmentBlocked(RuntimeError):
    """Raised when admission or context fit cannot be proven."""

def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip(): raise ValueError(f"{name} must be nonempty text")
    return value.strip()

def _timestamp(value: Any = None) -> str:
    if value is None: return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    text = _text(value, "timestamp")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as error: raise ValueError("timestamp must be ISO-8601") from error
    if parsed.tzinfo is None: raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, sort_keys=True); stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists(): temporary.unlink()

@contextmanager
def _exclusive_lock(path: Path):
    lock = path.with_name(path.name + ".lock"); lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0: stream.write(b"0"); stream.flush()
            stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try: yield
            finally: stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try: yield
            finally: fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

def _load(path: Path) -> dict[str, Any]:
    if not path.exists(): return {"schema_version": STATE_VERSION, "next_slot": 0, "assignments": {}, "events": []}
    try: value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error: raise ValueError(f"invalid assignment state: {error}") from error
    if not isinstance(value, dict) or value.get("schema_version") != STATE_VERSION or not isinstance(value.get("assignments"), dict) or not isinstance(value.get("events", []), list): raise ValueError("invalid assignment state")
    if not isinstance(value.get("next_slot"), int) or value["next_slot"] < 0: raise ValueError("invalid assignment state cursor")
    return value

def default_state_path() -> Path:
    home = Path(os.environ.get("COPILOT_HOME") or (Path.home() / ".copilot"))
    return home / "token-mizer" / "model-assignments.json"

def _context(candidate: dict[str, Any], required: int) -> tuple[bool, str]:
    capacity = candidate.get("context_capacity")
    if not isinstance(capacity, int) or capacity <= 0: return False, "context-capacity-unknown"
    return (capacity >= required, "context-fit-confirmed" if capacity >= required else "context-capacity-insufficient")

def _candidate(candidate: Any, required: int, explicit_model: str | None, large_context: bool) -> tuple[dict[str, Any], bool]:
    if not isinstance(candidate, dict): raise ValueError("candidate must be an object")
    model = _text(candidate.get("model"), "candidate model"); provider = _text(candidate.get("provider"), "candidate provider")
    if (provider, model) not in KNOWN_ROUTES: raise AssignmentBlocked("unverified provider/model route")
    if any(ch == "/" for ch in model) or candidate.get("provider_guid") or candidate.get("route_id") == "": raise AssignmentBlocked("bare or hardcoded provider route is not accepted")
    if provider == "Foundry" and model == FLASH_MODEL: raise AssignmentBlocked("Foundry Flash route is not verified")
    if candidate.get("route_evidence") is not None and not isinstance(candidate.get("route_evidence"), dict): raise ValueError("route_evidence must be an object")
    if candidate.get("route_evidence") and candidate["route_evidence"].get("verified") is not True: raise AssignmentBlocked("route evidence is not verified")
    fit, reason = _context(candidate, required)
    reasons = []
    if candidate.get("available") is not True: reasons.append("unavailable")
    if candidate.get("authorized") is not True: reasons.append("unauthorized")
    if not fit: reasons.append(reason)
    if model == SOL_MODEL and explicit_model != SOL_MODEL: fit = False; reasons.append("explicit-user-override-required")
    if large_context and provider != "GitHub": fit = False; reasons.append("large-context-requires-GitHub")
    item = {"provider": provider, "model": model, "eligible": candidate.get("available") is True and candidate.get("authorized") is True and fit, "reasons": reasons or [reason], "context_capacity": candidate.get("context_capacity", "unknown"), "route_evidence": deepcopy(candidate.get("route_evidence"))}
    return item, item["eligible"]

def _identity(record: dict[str, Any]) -> tuple[str, str, str, str, int, bool]:
    return tuple(record[k] for k in ("task_id", "task_class", "acceptance_boundary", "required_context", "large_context"))

def select_assignment(state_path: str | Path, *, assignment_id: str, task_id: str, task_class: str, acceptance_boundary: str, required_context: int, candidates: list[dict[str, Any]], explicit_model: str | None = None, explicit_provider: str | None = None, large_context: bool = False, path: str = "direct", now: str | None = None, cutoff: str | None = None) -> dict[str, Any]:
    assignment_id, task_id, task_class, acceptance_boundary = (_text(v, n) for v, n in ((assignment_id,"assignment id"),(task_id,"task id"),(task_class,"task class"),(acceptance_boundary,"acceptance boundary")))
    if not isinstance(required_context, int) or required_context <= 0: raise ValueError("required_context must be a positive estimate")
    if not isinstance(candidates, list) or not candidates: raise AssignmentBlocked("no model candidates supplied")
    if explicit_model is not None: explicit_model = _text(explicit_model, "explicit model")
    if explicit_provider is not None: explicit_provider = _text(explicit_provider, "explicit provider")
    if path not in {"direct", "coordinator", "astra"}: raise ValueError("invalid assignment path")
    timestamp = _timestamp(now); _timestamp(cutoff) if cutoff else None
    file = Path(state_path).expanduser().resolve()
    with _exclusive_lock(file):
        state = _load(file); prior = state["assignments"].get(assignment_id)
        if prior is not None:
            if _identity(prior) != (task_id, task_class, acceptance_boundary, required_context, large_context):
                raise AssignmentBlocked("assignment identity mismatch; historical assignment preserved")
            eligible = []
            for raw in candidates:
                item, ok = _candidate(raw, required_context, explicit_model, large_context)
                if ok and item["provider"] == prior["selected_provider"] and item["model"] == prior["selected_model"]: eligible.append(item)
            if not eligible:
                state["events"].append({"type":"reuse-blocked","assignment_id":assignment_id,"identity":dict(task_id=task_id, task_class=task_class, acceptance_boundary=acceptance_boundary),"at":timestamp,"reason":"current-admission-or-context-failed"}); _atomic_write(file, state)
                raise AssignmentBlocked("resume blocked: current admission, authorization, or context no longer fits; historical assignment preserved")
            return deepcopy(prior)
        eligibility=[]; fit=[]
        for raw in candidates:
            item, ok = _candidate(raw, required_context, explicit_model, large_context); eligibility.append(item)
            if ok: fit.append(raw)
        if explicit_model:
            chosen = next((c for c in fit if c["model"] == explicit_model and (explicit_provider is None or c["provider"] == explicit_provider)), None)
            if chosen is None: raise AssignmentBlocked("explicit model is unavailable, unauthorized, unverified, or does not fit context/provider requirements")
            reason="explicit-user-model"
        else:
            if not fit:
                if large_context:
                    raise AssignmentBlocked("large-context task requires an authorized, context-fitting GitHub model; no safe fallback")
                raise AssignmentBlocked("no authorized, available, verified model has confirmed sufficient context capacity")
            flash=[c for c in fit if c["model"]==FLASH_MODEL]; luna=[c for c in fit if c["model"]==LUNA_MODEL]
            if flash and luna: chosen=(flash+luna)[state["next_slot"]%2]; state["next_slot"]+=1; reason="alternating-context-fitting-pool"
            else: chosen=fit[0]; reason="only-context-fitting-authorized-candidate"
        result={"assignment_id":assignment_id,"task_id":task_id,"task_class":task_class,"acceptance_boundary":acceptance_boundary,"required_context":required_context,"large_context":large_context,"path":path,"selection_reason":reason,"selected_provider":_text(chosen.get("provider"),"selected provider"),"selected_model":_text(chosen.get("model"),"selected model"),"eligibility":eligibility,"attempts":0,"reassignments":0,"outcome":"unknown","created_at":timestamp}
        state["assignments"][assignment_id]=result; state["events"].append({"type":"selected","assignment_id":assignment_id,"at":timestamp,"identity":dict(task_id=task_id,task_class=task_class,acceptance_boundary=acceptance_boundary)}); _atomic_write(file,state); return deepcopy(result)

def admit_assignment(state_path: str | Path, **kwargs: Any) -> dict[str, Any]:
    return select_assignment(state_path, **kwargs)

def record_outcome(state_path: str | Path, assignment_id: str, *, outcome: str, actual_provider: str | None = None, actual_model: str | None = None, attempts: int = 1, reassignments: int = 0, timestamp: str | None = None, evidence: list[str] | None = None, cutoff: str | None = None, task_id: str | None = None) -> dict[str, Any]:
    assignment_id = _text(assignment_id,"assignment id"); outcome = _text(outcome,"outcome"); at=_timestamp(timestamp); _timestamp(cutoff) if cutoff else None
    if not isinstance(attempts,int) or attempts < 0 or not isinstance(reassignments,int) or reassignments < 0: raise ValueError("attempts and reassignments must be nonnegative integers")
    if evidence is not None and (not isinstance(evidence,list) or not all(isinstance(x,str) and x.strip() for x in evidence)): raise ValueError("evidence must be a list of nonempty strings")
    if actual_provider is not None: actual_provider = _text(actual_provider, "actual provider")
    if actual_model is not None: actual_model = _text(actual_model, "actual model")
    file=Path(state_path).expanduser().resolve()
    with _exclusive_lock(file):
        state=_load(file); selected=state["assignments"].get(assignment_id)
        if selected is None: raise AssignmentBlocked("assignment identity is not registered")
        if task_id is not None and task_id != selected["task_id"]: raise AssignmentBlocked("outcome task identity mismatch")
        event={"type":"outcome","assignment_id":assignment_id,"outcome":outcome,"actual_provider":actual_provider or "unknown","actual_model":actual_model or "unknown","attempts":attempts,"reassignments":reassignments,"timestamp":at,"evidence":list(evidence or [])}
        state["events"].append(event); _atomic_write(file,state)
        observed=deepcopy(selected); observed.update({k:v for k,v in event.items() if k not in {"type","assignment_id","timestamp"}}); observed["timestamp"]=at; return observed

def export_state(state_path: str | Path, cutoff: str | None = None) -> dict[str, Any]:
    state=_load(Path(state_path).expanduser().resolve())
    if cutoff:
        boundary=_timestamp(cutoff); state["events"]=[e for e in state["events"] if e.get("at",e.get("timestamp","")) < boundary]
    return state

def _json_input(args: argparse.Namespace) -> dict[str, Any]:
    if args.file: return json.loads(Path(args.file).read_text(encoding="utf-8"))
    raw=sys.stdin.read(); return json.loads(raw) if raw.strip() else {}

def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("action", choices=["select","admit","record","export"]); parser.add_argument("--state", type=Path, default=default_state_path()); parser.add_argument("--file", type=Path, help="JSON request file; otherwise read JSON stdin"); args=parser.parse_args(argv)
    try:
        data=_json_input(args)
        if args.action in {"select","admit"}: result=select_assignment(args.state, **data)
        elif args.action == "record": result=record_outcome(args.state, **data)
        else: result=export_state(args.state, **data)
        json.dump(result,sys.stdout,indent=2,sort_keys=True); sys.stdout.write("\n"); return 0
    except (AssignmentBlocked, ValueError, OSError, json.JSONDecodeError) as error:
        json.dump({"error": type(error).__name__, "message": str(error)}, sys.stderr); sys.stderr.write("\n"); return 2

if __name__ == "__main__": raise SystemExit(main())
