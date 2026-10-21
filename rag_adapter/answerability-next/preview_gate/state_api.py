"""Strict SERVER/QA-owned state overlays. Never bind this API to client JSON."""
import dataclasses
from .gate import CanonicalEvidence

STATE_FIELDS={
    'read_authorized':bool,'send_authorized':bool,'enabled':bool,'canonical':bool,'conflict':bool,
    'version':int,'current_version':int,'revision':int,'review_status':str,'expires_on':str,
    'document_id':(str,type(None)),'chunk_id':(str,type(None)),
}

def apply_state_overrides(records,overrides):
    """overrides={candidate_id:{supported_state_field:new_value}}.

    Unknown fields/IDs and wrong types RAISE; no silent ignoring. Body, units,
    field review and source company are NOT state overrides. Provider owns them.
    Candidate index version and authoritative current_version are distinct.
    """
    if not isinstance(overrides,dict):raise ValueError('state overrides must be mapping')
    by_id={r.candidate_id:r for r in records}
    if len(by_id)!=len(records):raise ValueError('duplicate server identity')
    if set(overrides)-set(by_id):raise ValueError('unknown state override candidate')
    result=[]
    for record in records:
        patch=overrides.get(record.candidate_id,{})
        if not isinstance(patch,dict) or set(patch)-set(STATE_FIELDS):raise ValueError('unsupported state override fields')
        for name,value in patch.items():
            expected=STATE_FIELDS[name]
            if type(value) not in (expected if isinstance(expected,tuple) else (expected,)):raise ValueError('invalid state override type: '+name)
            if name in ('version','current_version','revision') and value<1:raise ValueError('state counters must be positive')
        result.append(dataclasses.replace(record,**patch))
    return tuple(result)

def supported_state_contract():
    return {'interface':'apply_state_overrides(records, overrides_by_candidate_id)',
        'owner':'authenticated server provider or isolated QA harness, never request payload',
        'fields':list(STATE_FIELDS),'unknown_fields':'error','unknown_candidates':'error','default_permissions':'deny',
        'not_implemented':['user/group ACL resolution','canonical migration mapping construction',
            'HTTP endpoint authentication','live receipt store','production generation invocation'],
        'scope':'library filtering and gate semantics; no actual production authorization claim'}
