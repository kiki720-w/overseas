"""V2 candidate-only retrieval; source_refs are lineage, NEVER exact summary citations."""
import copy
import hashlib
import re
from dataclasses import dataclass
from .core import bm25,model_keys,equipment_model_keys,rrf,unit_vector

@dataclass(frozen=True)
class SemanticCandidate:
    record: dict
    @property
    def identity(self):return ('cleaned-v2-preparation-only',self.record.get('_index_version',0),self.record['candidate_chunk_id'])
    @property
    def embedding_text(self):return self.record['text']
    @property
    def content_hash(self):return hashlib.sha256(self.embedding_text.encode('utf-8')).hexdigest()

def adapt_semantic_v2(records):
    result=[];seen=set()
    for incoming in records:
        r=copy.deepcopy(incoming)
        needed=('candidate_chunk_id','origin_candidate_id','record_type','text','source_refs','source_occurrences',
            'review','target_document_id','target_version','eligible_for_product_retrieval','body_mode')
        if any(k not in r for k in needed):raise ValueError('missing cleaned-v2 semantic fields')
        cid=r['candidate_chunk_id']
        if not isinstance(cid,str) or not re.fullmatch('[a-f0-9]{16}',cid) or cid in seen:raise ValueError('invalid duplicate candidate identity')
        seen.add(cid)
        if r['target_document_id'] is not None or r['target_version'] is not None:raise ValueError('candidate target must remain unassigned')
        if r.get('source_refs_role','legacy_lineage_not_verbatim_quote_of_new_summary') not in ('legacy_lineage_not_verbatim_quote_of_new_summary','legacy_lineage'):raise ValueError('lineage-only source contract required')
        if not isinstance(r['text'],str) or not r['text'].strip():raise ValueError('semantic text required')
        if type(r['eligible_for_product_retrieval']) is not bool:raise ValueError('eligibility must be boolean')
        if r['review'].get('independently_proven') is not False or r.get('field_review',{}).get('independently_proven',False) is not False:
            raise ValueError('cannot upgrade evidence')
        for ref in r['source_refs']:
            if set(ref)!= {'document_id','version','chunk_id'} or type(ref['version']) is not int or ref['version']<1:
                raise ValueError('invalid lineage ref')
            if any(not re.fullmatch('[a-f0-9]{16}',ref[k]) for k in ('document_id','chunk_id')):raise ValueError('invalid lineage ID')
        result.append(SemanticCandidate(r))
    return result

def model_scope(candidate):
    r=candidate.record if isinstance(candidate,SemanticCandidate) else candidate
    primary=r.get('_primary_model_verbatim')
    return equipment_model_keys(primary) if primary else equipment_model_keys(r['text'])

def cover_requested_models(items,query,limit):
    models=equipment_model_keys(query)
    if len(models)<2:return items[:limit]
    selected=[];seen=set()
    for model in sorted(models):
        item=next((item for item in items if model in model_scope(item)),None)
        if item and item['candidate_chunk_id'] not in seen:
            selected.append(item);seen.add(item['candidate_chunk_id'])
    selected.extend(item for item in items if item['candidate_chunk_id'] not in seen)
    return selected[:limit]

def semantic_search(candidates,query,embedder=None,cache=None,min_cosine=.70,top_k=20,state_snapshot=None):
    if not isinstance(query,str) or not query.strip() or len(query)>200:raise ValueError('invalid query')
    if type(top_k) is not int or not 1<=top_k<=20:raise ValueError('invalid top K')
    if embedder and not cache:raise ValueError('dedicated cache required')
    models=equipment_model_keys(query)
    states=copy.deepcopy(state_snapshot()) if state_snapshot else None
    def allowed(c,current):
        r=c.record
        if current is not None:
            state=current.get(r['candidate_chunk_id'])
            return bool(state and state['enabled'] and state['review_status'] in ('unverified','confirmed','source_checked')
                and r.get('_index_version',0)==state['current_version'])
        return bool(r['review'].get('enabled') and r['review'].get('review_status') in ('unverified','confirmed','source_checked'))
    pool=[]
    for c in candidates:
        r=c.record
        if not r['eligible_for_product_retrieval'] or r['record_type'] in ('runtime_guidance','historical_audit_issue','official_research'):continue
        if not allowed(c,states):continue
        if models and not models & model_scope(c):continue
        pool.append(c)
    lexical=bm25(query,[c.embedding_text for c in pool])
    lex=sorted((i for i,x in enumerate(lexical) if x>0),key=lambda i:(-lexical[i],pool[i].identity))[:20]
    rankings=[[pool[i].identity for i in lex]];similarities={}
    if embedder and pool:
        raw=embedder.embed_query(query)
        if raw is not None:
            q=unit_vector(raw);vectors=cache.vectors(pool,embedder)
            if any(len(v)!=len(q) for v in vectors):raise ValueError('dimension mismatch')
            similarities={c.identity:sum(a*b for a,b in zip(q,v)) for c,v in zip(pool,vectors)}
            dense=sorted((key for key,value in similarities.items() if value>=min_cosine),key=lambda key:(-similarities[key],key))[:20]
            rankings.append(dense)
    order,scores=rrf(rankings);by_id={c.identity:c for c in pool}
    if len(by_id)!=len(pool):raise ValueError('duplicate candidate identity/version')
    fresh=copy.deepcopy(state_snapshot()) if state_snapshot else None
    items=[]
    for key in order:
        if not allowed(by_id[key],fresh):continue
        if states is not None and fresh.get(key[2])!=states.get(key[2]):continue
        r=copy.deepcopy(by_id[key].record)
        r.update(score=scores[key],vector_score=similarities.get(key),lexical_score=lexical[pool.index(by_id[key])],
            candidate_only=True,sendable=False,source_refs_are_exact_quotes=False,
            effective_source_refs_role='legacy_lineage_not_verbatim_quote_of_new_summary',
            field_review_missing=('field_review' not in r))
        items.append(r)
    items=cover_requested_models(items,query,top_k)
    return {'method':'candidate_v2_hybrid_rrf' if embedder else 'candidate_v2_bm25','candidate_only':True,'sendable':False,
        'items':items,'notice':'离线候选包状态不是服务端实时授权。新摘要身份未入库；旧source_refs仅沿革，不能引用成新摘要原文。'}
