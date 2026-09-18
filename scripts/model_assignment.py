#!/usr/bin/env python3
"""Fail-closed model assignment contract and public JSON CLI."""
from __future__ import annotations
import argparse, json, os, sys, tempfile, uuid, re
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LUNA_MODEL = "gpt-5.6-luna"
FLASH_MODEL = "gemini-3.8-flash"
SOL_MODEL = "gpt-5.6-sol"
STATE_VERSION = "3.0"
FAMILIES = {"luna": ("Foundry", LUNA_MODEL), "sol": ("Foundry", SOL_MODEL), "flash": ("GitHub", FLASH_MODEL)}
ROLES = {"coordinator", "builder", "reviewer", "validator"}
class AssignmentBlocked(RuntimeError): pass

def _text(v: Any, name: str) -> str:
    if not isinstance(v, str) or not v.strip(): raise ValueError(f"{name} must be nonempty text")
    return v.strip()

def _timestamp(v: Any = None) -> str:
    if v is None: return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    s=_text(v,"timestamp"); s=s[:-1]+"+00:00" if s.endswith("Z") else s
    try: d=datetime.fromisoformat(s)
    except ValueError as e: raise ValueError("timestamp must be ISO-8601") from e
    if d.tzinfo is None: raise ValueError("timestamp must include timezone")
    return d.astimezone(timezone.utc).isoformat().replace("+00:00","Z")

def _time(v: str):
    return datetime.fromisoformat(_timestamp(v).replace("Z","+00:00"))

def _atomic(path: Path, value: dict[str,Any]):
    path.parent.mkdir(parents=True, exist_ok=True); fd,name=tempfile.mkstemp(prefix='.'+path.name+'.', suffix='.tmp', dir=path.parent)
    tmp=Path(name)
    try:
        with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as f: json.dump(value,f,indent=2,sort_keys=True); f.write('\n'); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if tmp.exists(): tmp.unlink()

@contextmanager
def _lock(path: Path):
    lock=path.with_name(path.name+'.lock'); lock.parent.mkdir(parents=True,exist_ok=True)
    with lock.open('a+b') as f:
        if os.name=='nt':
            import msvcrt
            f.seek(0,2)
            if f.tell()==0: f.write(b'0'); f.flush()
            f.seek(0); msvcrt.locking(f.fileno(),msvcrt.LK_LOCK,1)
            try: yield
            finally: f.seek(0); msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            fcntl.flock(f.fileno(),fcntl.LOCK_EX)
            try: yield
            finally: fcntl.flock(f.fileno(),fcntl.LOCK_UN)

def _load(path: Path):
    if not path.exists(): return {'schema_version':STATE_VERSION,'next_slot':0,'assignments':{},'events':[],'next_event_id':1}
    try: d=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,json.JSONDecodeError) as e: raise ValueError(f'invalid assignment state: {e}')
    if not isinstance(d,dict) or d.get('schema_version')!=STATE_VERSION or not isinstance(d.get('assignments'),dict) or not isinstance(d.get('events'),list): raise ValueError('invalid assignment state')
    ids=[]; previous=None
    for event in d['events']:
        if not isinstance(event,dict) or not isinstance(event.get('event_id'),str): raise ValueError('invalid event')
        if event['event_id'] in ids: raise ValueError('event_id collision')
        stamp=event.get('at',event.get('timestamp'))
        if stamp is None: raise ValueError('event timestamp required')
        current=_time(stamp)
        if previous is not None and current < previous: raise ValueError('event timestamps must be monotonic')
        ids.append(event['event_id']); previous=current
    return d

def _event(state: dict[str,Any], typ: str, aid: str, at: str, **extra):
    ids={e.get('event_id') for e in state['events']}
    n=state.get('next_event_id',1)
    while f'e-{n}' in ids: n+=1
    state['next_event_id']=n+1
    state['events'].append({'event_id':f'e-{n}','type':typ,'assignment_id':aid,'at':at,**extra})

def _candidate(raw: Any, required: int, explicit_model: str|None, explicit_provider: str|None, large: bool):
    if not isinstance(raw,dict): raise ValueError('candidate must be an object')
    role=_text(raw.get('role'),'candidate role')
    if role not in ROLES: raise AssignmentBlocked('unknown candidate role')
    family=_text(raw.get('family'),'candidate family').lower()
    if family not in FAMILIES: raise AssignmentBlocked('unknown candidate family')
    provider=_text(raw.get('provider'),'candidate provider')
    runtime_id=_text(raw.get('runtime_id'),'candidate runtime_id')
    if '/' not in runtime_id or runtime_id.startswith('/') or runtime_id.endswith('/') or '\\' in runtime_id: raise AssignmentBlocked('qualified runtime_id is required')
    if any(x in runtime_id.lower() for x in ('guid','connection','provider_guid')) or re.fullmatch(r'[0-9a-f-]{32,}',runtime_id,re.I): raise AssignmentBlocked('real or spoofed provider identity is not publishable')
    expected_provider, expected_model=FAMILIES[family]
    if provider != expected_provider or not runtime_id.endswith('/'+expected_model): raise AssignmentBlocked('family/provider/runtime correspondence is invalid')
    evidence=raw.get('route_evidence')
    if not isinstance(evidence,dict) or evidence.get('verified') is not True or evidence.get('source') not in {'host','local'}: raise AssignmentBlocked('verified host or local route evidence is required')
    if evidence.get('runtime_id') != runtime_id: raise AssignmentBlocked('route evidence does not bind runtime_id')
    cap=raw.get('context_capacity'); fit=isinstance(cap,int) and not isinstance(cap,bool) and cap>0 and cap>=required
    reasons=[]
    if raw.get('available') is not True: reasons.append('unavailable')
    if raw.get('authorized') is not True: reasons.append('unauthorized')
    if not fit: reasons.append('context-fit-unknown-or-insufficient')
    if family=='sol' and explicit_model != SOL_MODEL: reasons.append('explicit-user-override-required'); fit=False
    if large and provider!='GitHub': reasons.append('large-context-requires-GitHub'); fit=False
    if explicit_provider is not None and provider != explicit_provider: reasons.append('explicit-provider-conflict'); fit=False
    return {'role':role,'family':family,'provider':provider,'runtime_id':runtime_id,'route_evidence':deepcopy(evidence),'context_capacity':cap if isinstance(cap,int) else 'unknown','eligible':raw.get('available') is True and raw.get('authorized') is True and fit,'reasons':reasons or ['context-fit-confirmed']}

def _identity(r): return (r['task_id'],r['task_class'],r['acceptance_boundary'],r['required_context'],r['large_context'])

def select_assignment(state_path: str|Path, *, assignment_id:str, task_id:str, task_class:str, acceptance_boundary:str, required_context:int, candidates:list[dict[str,Any]], explicit_model:str|None=None, explicit_provider:str|None=None, large_context:bool=False, path:str='direct', now:str|None=None, cutoff:str|None=None):
    vals=[_text(v,n) for v,n in ((assignment_id,'assignment id'),(task_id,'task id'),(task_class,'task class'),(acceptance_boundary,'acceptance boundary'))]
    assignment_id,task_id,task_class,acceptance_boundary=vals
    if not isinstance(required_context,int) or isinstance(required_context,bool) or required_context<=0: raise ValueError('required_context must be a positive estimate')
    if not isinstance(candidates,list) or not candidates: raise AssignmentBlocked('no model candidates supplied')
    if explicit_model is not None: explicit_model=_text(explicit_model,'explicit model')
    if explicit_provider is not None: explicit_provider=_text(explicit_provider,'explicit provider')
    if path not in {'direct','coordinator','astra'}: raise ValueError('invalid assignment path')
    at=_timestamp(now); cutoff and _timestamp(cutoff)
    file=Path(state_path).expanduser().resolve()
    with _lock(file):
        state=_load(file); prior=state['assignments'].get(assignment_id)
        if prior:
            if _identity(prior)!=(task_id,task_class,acceptance_boundary,required_context,large_context): raise AssignmentBlocked('assignment identity mismatch; historical assignment preserved')
            if explicit_model and prior['runtime_id'].split('/',1)[-1] != explicit_model: raise AssignmentBlocked('resume rejects changed explicit model request')
            if explicit_provider and prior['provider'] != explicit_provider: raise AssignmentBlocked('resume rejects changed explicit provider request')
            current=[_candidate(c,required_context,explicit_model,explicit_provider,large_context) for c in candidates]
            if not any(x['eligible'] and x['runtime_id']==prior['runtime_id'] for x in current):
                _event(state,'reuse-blocked',assignment_id,at,reason='current admission failed'); _atomic(file,state); raise AssignmentBlocked('resume blocked; historical assignment preserved')
            return deepcopy(prior)
        items=[_candidate(c,required_context,explicit_model,explicit_provider,large_context) for c in candidates]
        roles=[x['role'] for x in items]
        if len(roles)!=len(set(roles)): raise AssignmentBlocked('duplicate pool roles are not allowed')
        eligible=[x for x in items if x['eligible']]
        if explicit_model:
            chosen=next((x for x in eligible if x['runtime_id'].split('/',1)[-1]==explicit_model),None)
            if not chosen: raise AssignmentBlocked('explicit model is unavailable, unauthorized, unverified, or does not fit')
            reason='explicit-user-model'
        elif not eligible: raise AssignmentBlocked('no authorized, available, verified model has confirmed sufficient context capacity')
        else:
            flash=[x for x in eligible if x['family']=='flash']; luna=[x for x in eligible if x['family']=='luna']
            chosen=(flash+luna)[state.get('next_slot',0)%len(flash+luna)] if flash and luna else eligible[0]
            if flash and luna: state['next_slot']=state.get('next_slot',0)+1; reason='alternating-context-fitting-pool'
            else: reason='only-context-fitting-authorized-candidate'
        result={'assignment_id':assignment_id,'task_id':task_id,'task_class':task_class,'acceptance_boundary':acceptance_boundary,'required_context':required_context,'large_context':large_context,'path':path,'selection_reason':reason,'selected_role':chosen['role'],'selected_family':chosen['family'],'selected_provider':chosen['provider'],'selected_runtime_id':chosen['runtime_id'],'selected_model':chosen['runtime_id'].split('/',1)[-1],'route_evidence':deepcopy(chosen['route_evidence']),'eligibility':items,'attempts':0,'reassignments':0,'outcome':'unknown','created_at':at}
        state['assignments'][assignment_id]=result; _event(state,'selected',assignment_id,at,identity={'task_id':task_id,'task_class':task_class,'acceptance_boundary':acceptance_boundary}); _atomic(file,state); return deepcopy(result)

def admit_assignment(state_path: str|Path, **kwargs): kwargs['path']=kwargs.get('path','coordinator'); return select_assignment(state_path,**kwargs)

def spawn_handoff(state_path: str | Path, assignment_id: str, *, task_id: str, handoff_id: str, timestamp: str | None = None) -> dict[str, Any]:
    """Create a compact handoff preserving the admitted qualified runtime identity."""
    aid = _text(assignment_id, "assignment id"); tid = _text(task_id, "task id"); hid = _text(handoff_id, "handoff id"); at = _timestamp(timestamp)
    file = Path(state_path).expanduser().resolve()
    with _lock(file):
        state = _load(file); assignment = state["assignments"].get(aid)
        if not assignment or assignment["task_id"] != tid: raise AssignmentBlocked("handoff task identity mismatch")
        handoff = {"handoff_id": hid, "assignment_id": aid, "task_id": tid, "provider": assignment["selected_provider"], "runtime_id": assignment["selected_runtime_id"], "family": assignment["selected_family"], "at": at}
        _event(state, "spawn-handoff", aid, at, handoff_id=hid, runtime_id=handoff["runtime_id"]); _atomic(file, state); return handoff

def record_outcome(state_path: str|Path, assignment_id:str, *, outcome:Any, actual_provider:str|None=None, actual_runtime_id:str|None=None, actual_model:str|None=None, attempts:int=1, reassignments:int=0, timestamp:str|None=None, evidence:list[str]|None=None, cutoff:str|None=None, task_id:str|None=None, delta:bool=False, cumulative:bool=False):
    aid=_text(assignment_id,'assignment id'); outcome=_text(outcome,'outcome'); at=_timestamp(timestamp); cutoff and _timestamp(cutoff)
    if isinstance(attempts,bool) or not isinstance(attempts,int) or attempts<0 or isinstance(reassignments,bool) or not isinstance(reassignments,int) or reassignments<0: raise ValueError('attempts and reassignments must be strict nonnegative integers')
    if delta==cumulative: raise ValueError('exactly one of delta or cumulative must be true')
    if evidence is not None and (not isinstance(evidence,list) or not all(isinstance(x,str) and x.strip() for x in evidence)): raise ValueError('evidence must be a list of nonempty strings')
    file=Path(state_path).expanduser().resolve()
    with _lock(file):
        state=_load(file); selected=state['assignments'].get(aid)
        if not selected: raise AssignmentBlocked('assignment identity is not registered')
        if task_id and task_id!=selected['task_id']: raise AssignmentBlocked('outcome task identity mismatch')
        if actual_runtime_id is not None:
            actual_runtime_id=_text(actual_runtime_id,'actual runtime_id')
            if '/' not in actual_runtime_id: raise AssignmentBlocked('actual runtime_id must be qualified')
        event={'type':'outcome','assignment_id':aid,'outcome':outcome,'actual_provider':actual_provider or 'unknown','actual_runtime_id':actual_runtime_id or 'unknown','actual_model':actual_model or 'unknown','attempts':attempts,'reassignments':reassignments,'timestamp':at,'evidence':list(evidence or []),'semantics':'delta' if delta else 'cumulative'}
        _event(state,'outcome',aid,at,**{k:v for k,v in event.items() if k not in {'type','assignment_id'}}); _atomic(file,state)
        observed=deepcopy(selected); observed.update({k:v for k,v in event.items() if k not in {'type','assignment_id','timestamp'}}); observed['timestamp']=at; return observed

def export_state(state_path: str|Path, cutoff: str|None=None):
    state=_load(Path(state_path).expanduser().resolve())
    if cutoff:
        boundary=_time(cutoff); kept=[]
        for e in state['events']:
            stamp=e.get('at',e.get('timestamp'))
            if stamp and _time(stamp)<boundary: kept.append(e)
        state['events']=kept; state['export']={'metadata':{'cutoff':_timestamp(cutoff)},'cursor':{'event_count':len(kept)}}
    return state

def _input(a): return json.loads(a.file.read_text(encoding='utf-8')) if a.file else json.loads(sys.stdin.read() or '{}')
def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('action',choices=['select','admit','spawn','record','export']); p.add_argument('--state',type=Path,default=Path(os.environ.get('COPILOT_HOME',Path.home()/'.copilot'))/'token-mizer'/'model-assignments.json'); p.add_argument('--file',type=Path); a=p.parse_args(argv)
    try:
        d=_input(a); result=select_assignment(a.state,**d) if a.action in {'select','admit'} else spawn_handoff(a.state,**d) if a.action=='spawn' else record_outcome(a.state,**d) if a.action=='record' else export_state(a.state,**d); print(json.dumps(result,indent=2,sort_keys=True)); return 0
    except (AssignmentBlocked,ValueError,OSError,json.JSONDecodeError) as e: print(json.dumps({'error':type(e).__name__,'message':str(e)}),file=sys.stderr); return 2
if __name__=='__main__': raise SystemExit(main())
