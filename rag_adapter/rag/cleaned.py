"""Cleaning-audit candidates, kept separate from sendable canonical KB chunks."""
import copy
import re
from dataclasses import dataclass
from typing import Any, Mapping, Tuple
from .core import bm25, model_keys

TYPES=frozenset(('company_information','product_description','model_specification','performance_claim',
    'application_overview','application_images','official_research','runtime_guidance','historical_audit_issue'))
NON_PRODUCT=frozenset(('official_research','runtime_guidance','historical_audit_issue'))

@dataclass(frozen=True)
class CleanedCandidate:
    candidate_record_id: str
    proposed_chunk_id: str
    record_type: str
    retrieval_text: str
    source_refs: Tuple[Mapping[str,Any], ...]
    record: Mapping[str,Any]

def adapt_cleaned_records(records):
    """No target identity assignment, original text rewrite, or business truth promotion."""
    out=[];seen=set()
    for incoming in records:
        r=copy.deepcopy(incoming)
        required=('candidate_record_id','proposed_chunk_id','record_type','title','source_refs','review','source',
            'retrieval_text','eligible_for_product_retrieval','migration_status','target_document_id','target_version')
        if any(k not in r for k in required):raise ValueError('missing cleaned-factory-v1 fields')
        if r['record_type'] not in TYPES:raise ValueError('unknown candidate layer')
        if r['migration_status']!='candidate_only_not_imported' or r['target_document_id'] is not None or r['target_version'] is not None:
            raise ValueError('only unassigned preparation candidates accepted')
        ids=(r['candidate_record_id'],r['proposed_chunk_id'])
        if any(not isinstance(x,str) or not re.fullmatch('[a-f0-9]{16}',x) for x in ids) or ids[0] in seen:raise ValueError('invalid/duplicate candidate identity')
        seen.add(ids[0])
        if type(r['eligible_for_product_retrieval']) is not bool:raise ValueError('eligibility must be explicit boolean')
        if r['eligible_for_product_retrieval'] and (not isinstance(r['retrieval_text'],str) or not r['retrieval_text'].strip()):raise ValueError('eligible candidate text required')
        if r['retrieval_text'] is not None and not isinstance(r['retrieval_text'],str):raise ValueError('invalid nonproduct retrieval text')
        if not isinstance(r['source_refs'],list) or not r['source_refs']:raise ValueError('existing source refs required')
        for ref in r['source_refs']:
            if set(ref)!= {'document_id','version','chunk_id'} or type(ref['version']) is not int or ref['version']<1 or any(
                not isinstance(ref[k],str) or not re.fullmatch('[a-f0-9]{16}',ref[k]) for k in ('document_id','chunk_id')):
                raise ValueError('invalid existing source identity')
        if r['review'].get('independently_proven') is not False:raise ValueError('candidate cannot upgrade evidence')
        if r['record_type']=='model_specification' and any(k not in r for k in ('model_verbatim','cells','source_occurrences','legacy_row_numbers','issues')):
            raise ValueError('model provenance required')
        # Keep cells, null units, missing kinds, publisher and field-review scopes verbatim.
        out.append(CleanedCandidate(ids[0],ids[1],r['record_type'],r['retrieval_text'] or '',tuple(r['source_refs']),r))
    return out

def search_cleaned_candidates(candidates, query, top_k=6):
    """Local navigation preview only. NEVER feed this output into model context.

    Returns candidate identities separately from original citation refs. Caller must
    resolve those refs through canonical knowledge_selection and preview ORIGINAL
    selected text; cleaning transformations are not validated old quote substrings.
    Candidate review is audit snapshot, not live server authorization.
    """
    if not isinstance(query,str) or not query.strip() or len(query)>200:raise ValueError('invalid query')
    if type(top_k) is not int or not 1<=top_k<=20:raise ValueError('invalid top K')
    models=model_keys(query)
    pool=[]
    for c in candidates:
        r=c.record
        if not r['eligible_for_product_retrieval'] or c.record_type in NON_PRODUCT:continue
        explicit=r.get('model_verbatim')
        keys=model_keys(explicit) if explicit else model_keys(c.retrieval_text)
        if models and not models<=keys:continue
        pool.append(c)
    scores=bm25(query,[c.retrieval_text for c in pool])
    order=sorted((i for i,score in enumerate(scores) if score>0),key=lambda i:(-scores[i],pool[i].candidate_record_id))[:top_k]
    return {'method':'cleaned_candidate_bm25','candidate_only':True,'sendable':False,'requires_canonical_source_resolution':True,
        'cloud_context_added':False,'items':[{'candidate_record_id':pool[i].candidate_record_id,
            'proposed_chunk_id':pool[i].proposed_chunk_id,'record_type':pool[i].record_type,'title':pool[i].record['title'],
            'retrieval_text':pool[i].retrieval_text,'source_refs':copy.deepcopy(list(pool[i].source_refs)),
            'review_scope_snapshot':copy.deepcopy(pool[i].record['review']),'source':copy.deepcopy(pool[i].record['source']),
            'score':scores[i],'notice':'清洗候选不是原引用文本或实时授权；新ID不能覆盖旧块。'} for i in order]}
