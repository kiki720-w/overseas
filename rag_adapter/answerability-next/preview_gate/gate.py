"""Typed evidence coverage and server consent, independent of retrieval scores.

No model, network, database mutation or query auto-send. Authority comes only from
injected SERVER-owned canonical snapshots and a server consent verifier. This
experiment is not connected to the production API or frozen holdout pipeline.
"""
import copy
import datetime as dt
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Mapping, Optional, Tuple

PROTOCOL='evidence-gate-next-v1'

@dataclass(frozen=True)
class Requirement:
    requirement_id: str
    subject: str  # Company-qualified subject, never inferred from business group.
    field: str
    needs_unit: bool = False
    independent_proof: bool = False
    needs_current_record: bool = False
    current_record_max_age_seconds: int = 300

@dataclass(frozen=True)
class RequestPlan:
    question: str
    requirements: Tuple[Requirement, ...]
    scope_revision: int = 1

@dataclass(frozen=True)
class FieldEvidence:
    field: str
    raw_value: Optional[str]
    quote: str
    unit: Optional[str] = None
    unit_quote: str = ''
    unit_for_field: str = ''
    source_checked: bool = False
    independently_verified: bool = False
    current_record_as_of: str = ''
    missing_kind: str = ''

@dataclass(frozen=True)
class CanonicalEvidence:
    candidate_id: str
    document_id: Optional[str]
    version: int
    chunk_id: Optional[str]
    current_version: int
    revision: int
    subject: str
    source_company: str
    text: str
    fields: Tuple[FieldEvidence, ...]
    location: Mapping[str, object] = field(default_factory=dict)
    canonical: bool = False
    enabled: bool = True
    review_status: str = 'unverified'
    expires_on: str = ''
    conflict: bool = False
    read_authorized: bool = False
    send_authorized: bool = False

@dataclass(frozen=True)
class ConsentDecision:
    valid: bool = False
    scope_confirmed: bool = False
    allow_partial: bool = False

def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()

def _literal_value_in_quote(raw,quote):
    if raw is None or raw=='':return False
    # Numeric boundaries prevent the value 0 from claiming support in 20 or 0.2.
    if re.fullmatch(r'[+−-]?\d+(?:\.\d+)?',raw):
        return bool(re.search(r'(?<![A-Za-z\d_.])'+re.escape(raw)+r'(?![\d.])',quote))
    return raw in quote

def _field_checks(record,f,req,today,now):
    issues=[]
    if not record.read_authorized:issues.append('read_not_authorized')
    if not record.send_authorized:issues.append('send_not_authorized')
    if not record.canonical or not record.document_id or not record.chunk_id:issues.append('unassigned_canonical_identity')
    if record.version!=record.current_version:issues.append('stale_version')
    if not record.enabled:issues.append('disabled')
    if record.review_status not in ('unverified','source_checked','confirmed'):issues.append('review_not_eligible')
    if record.conflict:issues.append('conflict_flag')
    if record.expires_on:
        try:
            if dt.date.fromisoformat(record.expires_on)<today:issues.append('expired')
        except ValueError:issues.append('invalid_expiry')
    if f.quote not in record.text or not f.quote:issues.append('quote_not_in_canonical_text')
    if f.raw_value is None or f.raw_value=='' or f.missing_kind:issues.append('unknown_value')
    elif not _literal_value_in_quote(f.raw_value,f.quote):issues.append('value_not_in_quote')
    if not f.source_checked:issues.append('field_not_source_checked')
    if req.needs_unit:
        if not f.unit:issues.append('unknown_unit')
        elif not re.search(r'(?<![A-Za-zµμ])'+re.escape(f.unit)+r'(?![A-Za-zµμ])',f.quote):
            if not f.unit_quote or f.unit_quote not in record.text or not re.search(r'(?<![A-Za-zµμ])'+re.escape(f.unit)+r'(?![A-Za-zµμ])',f.unit_quote):
                issues.append('unit_not_in_source')
            elif f.unit_for_field!=f.field:issues.append('unit_scope_mismatch')
    if req.independent_proof and not f.independently_verified:issues.append('independent_evidence_missing')
    if req.needs_current_record:
        try:
            timestamp=dt.datetime.fromisoformat(f.current_record_as_of.replace('Z','+00:00'))
            age=(now-timestamp).total_seconds() if timestamp.tzinfo is not None else -1
            valid=timestamp.tzinfo is not None and 0<=age<=req.current_record_max_age_seconds
        except ValueError:valid=False
        if not valid:issues.append('current_record_missing')
    return issues

class EvidenceGate:
    def __init__(self,snapshot,consent_verifier=None,today=None,now=None):
        self.snapshot=snapshot
        self.consent_verifier=consent_verifier
        self.today=today or dt.date.today
        self.now=now or (lambda:dt.datetime.now(dt.timezone.utc))

    def _evaluate(self,plan,selections,records):
        if not isinstance(plan,RequestPlan) or not plan.question.strip() or len(plan.question)>200:
            raise ValueError('bounded request plan required')
        if not 1<=len(plan.requirements)<=20:raise ValueError('explicit requirements required; empty scope never passes')
        req_ids=[r.requirement_id for r in plan.requirements]
        if len(req_ids)!=len(set(req_ids)) or any(not r.subject or not r.field or not r.requirement_id for r in plan.requirements):
            raise ValueError('requirements need unique IDs, qualified subjects and fields')
        if any(type(r.current_record_max_age_seconds) is not int or not 1<=r.current_record_max_age_seconds<=86400 for r in plan.requirements):
            raise ValueError('freshness bounds must be server-approved 1..86400 seconds')
        if not isinstance(selections,(list,tuple)) or len(selections)>6 or len(set(selections))!=len(selections):
            raise ValueError('explicitly select at most six unique candidates')
        by_id={r.candidate_id:r for r in records}
        if len(by_id)!=len(records):raise ValueError('duplicate server candidate identity')
        selected=[by_id[cid] for cid in selections if cid in by_id]
        missing_selections=[cid for cid in selections if cid not in by_id]
        outcomes=[];allowed=[]
        for req in plan.requirements:
            matches=[(record,f) for record in selected if record.subject==req.subject for f in record.fields if f.field==req.field]
            diagnostics=[];valid=[]
            for record,f in matches:
                issues=_field_checks(record,f,req,self.today(),self.now())
                diagnostics.append({'candidate_id':record.candidate_id,'issues':issues})
                if not issues:valid.append((record,f))
            # Contradictory current, enabled source-checked values cannot be resolved by score.
            baseline=Requirement(req.requirement_id,req.subject,req.field)
            eligible=[(record,f) for record,f in matches if not _field_checks(record,f,baseline,self.today(),self.now())]
            values={f.raw_value for record,f in eligible}
            known_units={f.unit for record,f in eligible if f.unit is not None}
            conflicting=len(values)>1 or len(known_units)>1 or any('conflict_flag' in d['issues'] for d in diagnostics)
            if conflicting:
                state='conflicting_evidence';valid=[]
            elif valid:state='supported'
            elif not matches:state='missing_evidence'
            else:
                reasons={issue for d in diagnostics for issue in d['issues']}
                priority=('read_not_authorized','send_not_authorized','unassigned_canonical_identity','disabled','stale_version','review_not_eligible','expired','invalid_expiry',
                    'quote_not_in_canonical_text','value_not_in_quote','unknown_value','unknown_unit','unit_not_in_source',
                    'unit_scope_mismatch','field_not_source_checked','independent_evidence_missing','current_record_missing')
                state=next((reason for reason in priority if reason in reasons),'missing_evidence')
            outcomes.append({'requirement_id':req.requirement_id,'subject':req.subject,'field':req.field,
                'state':state,'diagnostics':diagnostics})
            for record,f in valid:
                allowed.append({'requirement_id':req.requirement_id,'ref_id':'N'+str(len(allowed)+1).zfill(3),
                    'document_id':record.document_id,'version':record.version,'chunk_id':record.chunk_id,
                    'revision':record.revision,'source_company':record.source_company,'location':dict(record.location),
                    'quote':f.quote,'unit_quote':f.unit_quote if req.needs_unit else '',
                    'raw_value':f.raw_value,'unit':f.unit,'unit_unknown':f.unit is None,
                    'evidence_level':'independently_verified' if f.independently_verified else 'publisher_self_statement'})
        supported=sum(o['state']=='supported' for o in outcomes)
        coverage='supported' if supported==len(outcomes) else 'partial' if supported else 'unsupported'
        context={'protocol':PROTOCOL,'request':asdict(plan),'selections':list(selections),
            'records':[asdict(r) for r in selected],'missing_selections':missing_selections,
            'outcomes':outcomes,'allowed_evidence':allowed}
        return {'protocol':PROTOCOL,'coverage':coverage,'outcomes':outcomes,'allowed_evidence':allowed,
            'unknown_requirements':[o for o in outcomes if o['state']!='supported'],
            'missing_selections':missing_selections,'context_digest':digest(context),
            'generation_allowed':False,'generation_state':'preview_only','cloud_context_added':False,
            'requires_explicit_preview':True,'retrieval_score_used_for_answerability':False}

    def preview(self,plan,selections):
        # No consent checks, generation or implicit retrieval selection here.
        return self._evaluate(plan,selections,copy.deepcopy(list(self.snapshot())))

    def authorize(self,plan,selections,receipt=None):
        result=self.preview(plan,selections)
        if self.consent_verifier is None or receipt is None:
            result['generation_state']='not_authorized';return result
        consent=self.consent_verifier(receipt,result['context_digest'])
        if not isinstance(consent,ConsentDecision) or not consent.valid:
            result['generation_state']='invalid_or_stale_consent';return result
        # State may change while a server consent verifier resolves a receipt.
        fresh=self.preview(plan,selections)
        if fresh['context_digest']!=result['context_digest']:
            fresh['generation_state']='preview_changed';return fresh
        if not consent.scope_confirmed:
            result['generation_state']='scope_not_confirmed';return result
        if result['missing_selections'] or result['coverage']=='unsupported':
            result['generation_state']='evidence_missing';return result
        if result['coverage']=='partial' and not consent.allow_partial:
            result['generation_state']='partial_scope_not_authorized';return result
        result['generation_state']='source_bounded_generation_permitted'
        result['generation_allowed']=True
        result['answer_decision']='partial_abstain' if result['coverage']=='partial' else 'source_bounded'
        return result

def validate_claims(claims,result):
    """Only approved exact field quotes become facts; no arbitrary summary statement."""
    if not result.get('generation_allowed'):raise ValueError('generation has not been authorized')
    if not isinstance(claims,list) or len(claims)>20:raise ValueError('invalid claim list')
    evidence={e['ref_id']:e for e in result['allowed_evidence']};checked=[]
    for claim in claims:
        if not isinstance(claim,dict) or set(claim)!={'requirement_id','ref_id','quote'}:raise ValueError('strict quote-only claim shape required')
        e=evidence.get(claim['ref_id'])
        if not e or claim['requirement_id']!=e['requirement_id'] or claim['quote']!=e['quote']:
            raise ValueError('claim does not match approved evidence')
        checked.append(copy.deepcopy(e))
    return checked

class PreviewService:
    """Prepared local seam. Default-off; caller cannot enable via request payload."""
    def __init__(self,retrieve,enabled=False,snapshot=None,include_expired=False,include_conflicts=True):
        self.retrieve=retrieve;self.enabled=enabled;self.snapshot=snapshot
        self.include_expired=include_expired;self.include_conflicts=include_conflicts
    def preview(self,query):
        if not self.enabled:return {'enabled':False,'status':'feature_disabled','items':[],
            'generation_allowed':False,'cloud_context_added':False}
        if self.snapshot is None:return {'enabled':True,'status':'authoritative_state_required','items':[],
            'generation_allowed':False,'cloud_context_added':False}
        if not isinstance(query,str) or not query.strip() or len(query)>200:raise ValueError('bounded query required')
        items=self.retrieve(query)
        if not isinstance(items,list):raise ValueError('candidate list required')
        # Read fresh authority AFTER retrieval/reranking, never item/client status flags.
        records=list(self.snapshot());by_id={r.candidate_id:r for r in records}
        if len(by_id)!=len(records):raise ValueError('duplicate authoritative identity')
        visible=[]
        for item in items:
            if not isinstance(item,dict) or not isinstance(item.get('candidate_id'),str):raise ValueError('candidate_id required')
            r=by_id.get(item['candidate_id'])
            if not r or not r.read_authorized or not r.enabled or r.version!=r.current_version or r.review_status not in ('unverified','confirmed','source_checked'):continue
            if r.conflict and not self.include_conflicts:continue
            if r.expires_on:
                try:expired=dt.date.fromisoformat(r.expires_on)<dt.date.today()
                except ValueError:continue
                if expired and not self.include_expired:continue
            # Do not reuse stale indexed text or source metadata supplied by retrieval.
            visible.append({'candidate_id':r.candidate_id,'text':r.text,'subject':r.subject,
                'source_company':r.source_company,'location':dict(r.location),'version':r.version,'revision':r.revision,
                'review_status':r.review_status,'expires_on':r.expires_on,'conflict':r.conflict,
                'canonical':r.canonical,'send_authorized':r.send_authorized,'score':item.get('score'),
                'generation_allowed':False})
        return {'enabled':True,'status':'candidate_preview_only','items':visible[:20],
            'generation_allowed':False,'cloud_context_added':False,'selections':[],
            'notice':'相似候选不代表有答案。请明确选择资料、核对需求范围及缺证状态后进行发送预览。'}
