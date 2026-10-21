"""Optional retrieval preview. Semantic regex advice NEVER blocks manual selection.

No HTTP registration, model send, consent issuance or production writes.
"""
import copy
import dataclasses
import datetime as dt
import hashlib
import json
import re
import secrets
import time

def fingerprint(record):
    return hashlib.sha256(json.dumps(dataclasses.asdict(record),sort_keys=True,
        separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

class PreviewError(ValueError):
    def __init__(self,code,message):super().__init__(message);self.code=code

def governance_issue(r):
    if not r.read_authorized:return 'read_not_authorized'
    if not r.canonical or not r.document_id or not r.chunk_id:return 'canonical_identity_missing'
    if not all(isinstance(v,str) and re.fullmatch('[a-f0-9]{16}',v) for v in (r.document_id,r.chunk_id)):
        return 'canonical_identity_invalid'
    if any(type(v) is not int or v<1 for v in (r.version,r.current_version,r.revision)):return 'invalid_version'
    if not r.enabled:return 'disabled'
    if r.version!=r.current_version:return 'stale_version'
    if r.review_status not in ('confirmed','source_checked','unverified'):return 'review_rejected_or_unknown'
    if r.expires_on:
        try:
            if dt.date.fromisoformat(r.expires_on)<dt.date.today():return 'expired'
        except ValueError:return 'invalid_expiry'
    return None

class EnhancedPreviewAPI:
    def __init__(self,retrieve,snapshot,knowledge_selection,project_knowledge,evidence_catalog,
        advisory=None,enabled=False,ttl_seconds=900,clock=None):
        self.retrieve=retrieve;self.snapshot=snapshot;self.knowledge_selection=knowledge_selection
        self.project_knowledge=project_knowledge;self.evidence_catalog=evidence_catalog
        self.advisory=advisory;self.enabled=enabled;self.ttl_seconds=ttl_seconds
        self.clock=clock or time.monotonic;self.previews={}

    def _snapshot(self):
        rows=list(self.snapshot());by_id={r.candidate_id:r for r in rows}
        if len(rows)!=len(by_id):raise PreviewError('invalid_authority','duplicate server identity')
        return rows,by_id

    def search(self,payload):
        if not self.enabled:return {'enabled':False,'status':'feature_disabled','items':[],
            'user_selections':[],'send_authorization':{'consent_present':False,'generation_allowed':False}}
        if not isinstance(payload,dict) or set(payload)-{'q','limit'}:raise PreviewError('invalid_query','q/limit only')
        query=payload.get('q');limit=payload.get('limit',6)
        if not isinstance(query,str) or not query.strip() or len(query)>200 or type(limit) is not int or not 1<=limit<=20:
            raise PreviewError('invalid_query','bounded query and limit 1..20 required')
        result=self.retrieve(query,limit)
        if not isinstance(result,dict) or not isinstance(result.get('items'),list):raise PreviewError('invalid_retrieval','retriever item list required')
        rows,by_id=self._snapshot() # Fresh authority AFTER real retrieval/rerank.
        items=[];seen=set();blocked=0;saved={}
        for hit in result['items']:
            if not isinstance(hit,dict) or not isinstance(hit.get('candidate_chunk_id'),str):raise PreviewError('invalid_retrieval','candidate ID required')
            cid=hit['candidate_chunk_id'];r=by_id.get(cid)
            if r is None or governance_issue(r):blocked+=1;continue
            if cid in seen:continue
            seen.add(cid)
            selection={'document_id':r.document_id,'version':r.version,'chunk_id':r.chunk_id}
            # Existing selection validation and outbound whitelist remain authoritative.
            selected=self.knowledge_selection([selection])
            if len(selected)!=1 or selected[0]['text']!=r.text:raise PreviewError('knowledge_changed','canonical body changed during preview')
            projected=self.project_knowledge(selected[0])
            if projected['version']!=r.version or projected['revision']!=r.revision:
                raise PreviewError('knowledge_changed','canonical revision changed during preview')
            unit_notes=[{'field':f.field,'raw_value':f.raw_value,'unit':f.unit,
                'unit_unknown':f.unit is None,'missing_kind':f.missing_kind or None} for f in r.fields]
            items.append({'candidate_chunk_id':cid,'selection':selection,'source':projected,
                'text':r.text,'text_sha256':hashlib.sha256(r.text.encode()).hexdigest(),
                'rank':len(items)+1,'score':hit.get('score'),
                'raw_rerank_logit':hit.get('raw_rerank_logit'),'scores_prove_answer':False,
                'source_constraints':{'evidence_level':'publisher_self_statement','independently_proven':False,
                    'conflict':r.conflict,'unknown_units_not_filled':True,'field_values':unit_notes},
                'manual_selection_allowed':True,'selected':False})
            saved[cid]=fingerprint(r)
            if len(items)>=limit:break
        # Re-read after selection projection so a state change during read cannot leak.
        _,fresh=self._snapshot()
        if any(cid not in fresh or governance_issue(fresh[cid]) or fingerprint(fresh[cid])!=mark for cid,mark in saved.items()):
            raise PreviewError('knowledge_changed','authority changed during preview; retry')
        signal=None
        if self.advisory:
            try:signal=self.advisory(query,rows,list(saved))
            except Exception:signal={'status':'scope_advice_unavailable'}
        preview_id=secrets.token_hex(16)
        self.previews={key:value for key,value in self.previews.items() if value['expires']>self.clock()}
        if len(self.previews)>=100:raise PreviewError('preview_capacity','too many pending previews')
        self.previews[preview_id]={'expires':self.clock()+self.ttl_seconds,'saved':saved,
            'selections':{cid:next(i['selection'] for i in items if i['candidate_chunk_id']==cid) for cid in saved}}
        return {'enabled':True,'status':'manual_evidence_preview','preview_id':preview_id,'items':items,
            'retrieval':{'method':result.get('method','local_hybrid_rrf_rerank'),'returned':len(items),
                'retrieval_only':True,'provenance':result.get('provenance','injected local retriever')},
            'governance':{'server_snapshot_checked':True,'blocked_count':blocked,'canonical_scope':'isolation_only'},
            'semantic_answerability':{'status':'requires_review','blocking':False,'experimental_advice':signal,
                'notice':'相关资料供人工选用；规则不能证明完整答案，未识别要求和未知值需人工核对。'},
            'send_authorization':{'consent_present':False,'generation_allowed':False,'cloud_context_added':False},
            'user_selections':[],'selection_protocol':'existing document_id/version/chunk_id; max 6'}

    def context_preview(self,payload):
        if not self.enabled:raise PreviewError('feature_disabled','enhanced preview disabled')
        if not isinstance(payload,dict) or set(payload)!={'preview_id','selections'}:raise PreviewError('invalid_selection','preview_id/selections only')
        pending=self.previews.get(payload['preview_id'])
        if pending is None or pending['expires']<=self.clock():raise PreviewError('preview_expired','preview expired; search again')
        selections=payload['selections']
        if not isinstance(selections,list) or not selections or len(selections)>6:raise PreviewError('invalid_selection','explicitly select 1..6 chunks')
        expected=pending['selections'];reverse={tuple(v[k] for k in ('document_id','version','chunk_id')):cid for cid,v in expected.items()}
        selected_ids=[]
        for selection in selections:
            if not isinstance(selection,dict) or set(selection)!={'document_id','version','chunk_id'}:raise PreviewError('invalid_selection','existing selection shape required')
            if type(selection['version']) is not int or not all(isinstance(selection[k],str) for k in ('document_id','chunk_id')):
                raise PreviewError('invalid_selection','strict existing selection value types required')
            key=tuple(selection[k] for k in ('document_id','version','chunk_id'))
            if key not in reverse:raise PreviewError('outside_preview_scope','selection not in this preview')
            cid=reverse[key]
            if cid in selected_ids:raise PreviewError('invalid_selection','duplicate selection')
            selected_ids.append(cid)
        _,by_id=self._snapshot()
        def unchanged(authority):
            return all(cid in authority and not governance_issue(authority[cid])
                and fingerprint(authority[cid])==pending['saved'][cid] for cid in selected_ids)
        if not unchanged(by_id):raise PreviewError('knowledge_changed','selection authority changed; re-preview')
        originals=self.knowledge_selection(copy.deepcopy(selections))
        knowledge=[self.project_knowledge(item) for item in originals]
        # This is only the knowledge portion; no customer/config data is fetched here.
        local_context={'customer':{},'factory':{},'records':[],'knowledge':knowledge}
        catalog=self.evidence_catalog(local_context)
        if len(json.dumps(local_context,ensure_ascii=False).encode())>48*1024:
            raise PreviewError('context_too_large','selected knowledge exceeds 48KiB; existing full context limit still applies')
        _,after=self._snapshot()
        if not unchanged(after):raise PreviewError('knowledge_changed','selection changed during context preview')
        return {'status':'selected_knowledge_context_preview','selections':copy.deepcopy(selections),
            'knowledge':knowledge,'evidence_catalog':catalog,'context_scope':'selected_knowledge_only',
            'semantic_answerability':{'status':'requires_review','blocking':False},
            'send_authorization':{'consent_present':False,'generation_allowed':False,'cloud_context_added':False},
            'next_step':'Keep selections unchanged; use existing selected-customer model preview/token/consent protocol.',
            'existing_model_protocol_preserved':True}
