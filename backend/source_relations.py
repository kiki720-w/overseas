"""Explicit published relationship claims produce reminders, never exclusions."""
import re

def text(candidate):
    provenance=candidate.get('search_provenance') or {}
    return provenance.get('snippet',''),provenance.get('source_url') or candidate.get('url','')

def claims(candidate):
    snippet,url=text(candidate)
    if contrary(snippet):return []
    if candidate.get('source_classification',{}).get('kind') in ('media','directory'):return []
    pattern=r'(?P<subject>[\u4e00-\u9fffA-Za-z0-9]{4,45}(?:有限公司|股份公司))（下称[：:]?[“\"]?(?P<alias>[^”\"）]{3,25})[”\"]?）[^\n]{0,180}?隶属于[^。\n]{0,30}?[“\"](?P<parent>[^”\"]{5,70})[”\"]'
    return [{'subject':m['subject'],'alias':m['alias'],'parent':m['parent'],'quote':m.group(0),'source_url':url} for m in re.finditer(pattern,snippet) if not contrary(m.group(0))]

def contrary(value):
    return any(word in value for word in ('并非','不隶属','未隶属','没有关系','无关联','无关','独立','同名','不同企业'))

def warnings(candidate,others):
    result=[]
    for other in others:
        if candidate.get('domain')==other.get('domain'):continue
        for owner,peer in [(candidate,other),(other,candidate)]:
            peer_text,peer_url=text(peer)
            if contrary(peer_text):continue
            for claim in claims(owner):
                peer_claim=next((p for p in claims(peer) if p['parent']==claim['parent']),None)
                if not peer_claim:continue
                phrase=claim['parent'];quote=peer_claim['quote']
                result.append({'kind':'possible_related_source','related_candidate_id':other.get('candidate_id'),'basis':'two explicit subsidiary statements naming the same parent; not legal entity resolution','relationship_claim':claim,'other_evidence':{'quote':quote,'matched_phrase':phrase,'source_url':peer_url},'notice':'跨域来源可能涉及同一集团或相关企业，请核对法人/采购主体；未经核验不合并、不排除。','identity_verified':False,'same_legal_entity':'unknown','exclude':False,'merge':False})
                break
            if result and result[-1]['related_candidate_id']==other.get('candidate_id'):break
    return result[:3]

def attach(candidate,others):
    result=warnings(candidate,others)
    if result:
        candidate['source_relation_warnings']=result
        first=result[0];claim=first['relationship_claim'];other=first['other_evidence']
        notice=' 跨域关联提醒：可能涉及同集团或相关企业，非重复公司结论；请核对法人/采购主体，不自动合并或排除。关系原文：'+claim['quote']+'（'+claim['source_url']+'）；另一来源提到“'+other['matched_phrase']+'”：'+other['quote']+'（'+other['source_url']+'）。'
        if notice not in candidate['match_hint']['notice']:candidate['match_hint']['notice']+=notice
    return candidate
