"""Conservative source grouping, not legal entity resolution or CRM merging."""
import hashlib

from .source_evidence import assessment,attach
from .lead_candidates import project

def claim(candidate):
    return assessment(candidate)['identity_claim']

def summarize(items,annotate=False):
    groups={};unresolved=[];references=0;roles={};lead_urls=set()
    for candidate in items:
        review=assessment(candidate);value=review['identity_claim'];role=review['classification']['page_role'];roles[role]=roles.get(role,0)+1
        references+=int(role in ('directory_list','company_detail_reference','document_reference','product_material','editorial_reference'))
        lead=project(candidate,review)
        if annotate:attach(candidate,review);candidate['lead']=lead
        if lead['eligible']:lead_urls.add(candidate['url'])
        if value is None:
            unresolved.append(candidate.get('candidate_id'))
            if annotate:candidate['entity_group']=None
            continue
        key='|'.join((value['basis'],value['publisher'],value['name'],value['address'] or ''))
        gid=hashlib.sha256(key.encode()).hexdigest()[:24]
        group=groups.setdefault(gid,dict(value,group_id=gid,source_pages=[],candidate_ids=[],capability_evidence=[],identity_evidence=[],pending_confirmation=review['pending_confirmation'],identity_verified=False,legal_entity_confirmed=False,purchase_intent='unknown',merge_scope='display_source_group_only'))
        url=value['source_url']
        group['capability_evidence'].extend(review['capability_evidence'])
        group['identity_evidence'].append({'source_url':url,'claim':value,'evidence_level':'search_summary_unverified'})
        if url not in group['source_pages']:group['source_pages'].append(url)
        if candidate.get('candidate_id') not in group['candidate_ids']:group['candidate_ids'].append(candidate.get('candidate_id'))
        if annotate:candidate['entity_group']={'group_id':gid,'name':value['name'],'basis':value['basis'],'identity_verified':False,'legal_entity_confirmed':False,'purchase_intent':'unknown'}
    for candidate in items:
        if annotate and candidate.get('entity_group'):
            group=groups[candidate['entity_group']['group_id']]
            candidate['source_assessment'].update(related_source_urls=group['source_pages'],group_capability_evidence=group['capability_evidence'])
    return {'unverified_lead_count':len(lead_urls),'lead_count_unit':'source_pages_not_unique_companies','reference_source_page_count':references,'unresolved_company_source_page_count':len(unresolved)-references,'page_role_counts':roles,'source_page_count':len(items),'entity_candidate_count':len(groups),'unresolved_source_page_count':len(unresolved),
            'verified_company_count':0,'groups':list(groups.values()),'unresolved_candidate_ids':unresolved,
            'count_unit':'unverified_entity_claim_groups_plus_unresolved_source_pages',
            'notice':'按明确来源声明归组的企业候选，非已核实独立法人；未解析页面另列。所有原页面保留，不合并客户记录。'}
