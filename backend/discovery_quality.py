from .website_text import document_source
"""Conservative local evidence labels; never asserts company identity or purchase intent."""
import re
from urllib.parse import urlsplit
from .common import norm
from .search_identity import reference_url
from .application_recall import affirmative_context

def operating_role(text):
    """Label the stated offer, not the legal identity of the speaker."""
    sentences=re.split(r'\n+',text)
    for sentence in sentences:
        if not affirmative_context(sentence):continue
        if re.search(r'不(?:提供|生产|制造|承接)|\b(?:not|never|do not|don.t)\b',sentence,re.I):continue
        if re.search(r'\bwe\s+(?:manufacture|sell|supply|offer|provide)\b.{0,60}\b(?:machine tools?|hobbing machines?)\b',sentence,re.I):
            return 'equipment_supplier',sentence[:400]
        if re.search(r'我们(?:自行|直接|专门)?(?:制造|销售|供应|提供).{0,35}(?:滚齿机|机床设备)',sentence):
            return 'equipment_supplier',sentence[:400]
        if re.search(r'我们(?:自行|直接|内部|专门)?(?:定制|制造|生产)(?:的)?.{0,15}齿轮|我们(?:为客户)?提供(?:齿轮)?滚齿加工服务|\b(?:we|our shop)\s+(?:manufacture|produce|provide|offer)\s+(?:custom\s+|precision\s+)?(?:gears|gear hobbing services)',sentence,re.I):
            return 'terminal_manufacturing_statement',sentence[:400]
    stated_equipment=re.search(r'[^.!?\n]{0,180}\bmanufacturer of\s+(?:special\s+|precision\s+)?(?:gear[- ]processing machinery|machine tools?|hobbing machines?)[^.!?\n]*',text,re.I)
    if stated_equipment:return 'equipment_supplier',stated_equipment.group(0)[:400]
    stated_machining=re.search(r'[^.!?\n]{0,100}\b(?:we|our company)\s+(?:offer|provide|manufacture)\b[^.!?\n]{0,70}\b(?:machining services|gears)\b[^.!?\n]*',text,re.I)
    if stated_machining:return 'terminal_manufacturing_statement',stated_machining.group(0)[:400]
    # A machine-feature page describes equipment capability, not the viewer's factory.
    if re.search(r'在.{0,30}(?:车铣中心|机床)上采用齿轮加工技术|使.{0,30}车铣中心变身为.{0,15}齿加工机床',text):
        return 'equipment_solution_page','设备上的齿轮加工技术描述；不证明终端工厂提供加工服务'
    return 'unknown',''

def explicit_editorial(text):
    if re.search(r'原载于.{0,60}(?:报|刊)|(?:转载自|文章来源[:：]).{0,60}(?:报|新闻网)',text):return 'explicit publication attribution'
    if re.search(r'首次覆盖.{0,35}(?:评级|买入)',text) and re.search(r'归母净利润|\bPE\b|营收增速',text,re.I):return 'securities research coverage with financial forecast'
    return ''

def classify_page(url,title,snippet,known_directory=False,metadata=None):
    path=urlsplit(url).path.lower(); text=(title+' '+snippet).lower()
    plural=r'(?:companies|manufacturers|machine shops|cnc shops|suppliers|企业|厂家|加工厂)'
    roundup=bool(re.search(r'(?:top|best|leading)\s+(?:\d+\s+)?[^.!?]{0,100}'+plural,title,re.I) or re.search(r'(?:排行|十大|名录|榜单)',title))
    directory=known_directory or reference_url(url) or bool(re.search(r'/(?:directory|directories|companies|suppliers)(?:/|$)',path)) or (roundup and (snippet.count('|')>=6 or bool(re.search(r'\bdirectory\b|名录',snippet,re.I))))
    publication_basis=explicit_editorial(snippet)
    industry_portal=bool(re.search(r'行业网|行业资讯|行业信息|产业资讯|\b(?:industry|trade)\s+(?:news|portal|network|information)\b',title,re.I))
    document=document_source(url,metadata)
    editorial=industry_portal or bool(re.search(r'/(?:blog|news|articles?|feeds)(?:/|$)',path)) or roundup or bool(re.search(r'^(?:top|best)\s+\d+\b',title,re.I)) or bool(publication_basis)
    role,role_evidence=operating_role(snippet)
    if document:kind='third_party_document';label='第三方／归属待核PDF文件；不作为企业自述';basis='document URL only; publisher relationship and company identity are unverified'
    elif directory:kind='directory'; label='目录／聚合来源，企业身份待核验'; basis='directory structure or provider platform; candidate identity not established'
    elif editorial:kind='media'; label='媒体／榜单内容，非公司身份依据'; basis='editorial URL structure or multi-company roundup title'
    elif re.search(r'\b(?:offers?|provides?)\b.{0,40}\b(?:machining|manufacturing)\s+services',snippet,re.I) or re.search(r'\b(?:company|manufacturing|manufacturer|inc|llc|ltd|corp)\b|有限公司',title,re.I) or re.search(r'\b(?:we (?:offer|make|manufacture|machine|excel)|our (?:plant|shop|capabilities))\b',snippet,re.I):kind='company';label='公司页面线索，身份待核验';basis='company title or first-person operating statement; self statement only'
    else:kind='unknown';label='来源角色未知，需核对公司身份';basis='insufficient source-role evidence'
    if kind=='unknown' and role in ('terminal_manufacturing_statement','equipment_supplier'):
        kind='company';label='企业经营/技术页面线索，身份待核验';basis='bounded operating or equipment statement; legal identity not verified'
    if kind=='third_party_document':role='document_reference';role_evidence='hosted document; no verified publisher-to-company relationship'
    elif kind=='media':role='editorial_reference';role_evidence=publication_basis or basis
    elif kind=='directory':role='directory_reference'
    return {'page_role':'product_material' if not document and re.search(r'/(?:products?|catalog|brochures?|downloads?)(?:/|$)',path) and kind not in ('directory','media') else None,'kind':kind,'label':label,'basis':basis,'business_role':role,'role_evidence':role_evidence,'source_url':url,'source_type':source_type_for({'kind':kind}),'evidence_level':'third_party_document_unverified' if document else 'source_role_unverified','publisher_relationship':'unverified' if document else 'not_assessed','listing_scope':'multiple_entities_or_search_results' if directory and re.search(r'/(?:suche|search)(?:/|$)',path) else 'single_entry_or_unknown','single_company_candidate':False if directory else None,'confidence':'heuristic_unverified','identity_verified':False,'country_verified':False,'purchase_intent':'unknown','independently_proven':False,'notice':'分类仅描述此页面角色，不认证公司、工厂能力或采购需求。'+(' 此页描述设备供货/技术方案，不据此视为齿轮加工终端工厂。' if role in ('equipment_supplier','equipment_solution_page') else '')}

def classify_capture(capture):
    pages=capture.get('pages',[])
    if not pages:return classify_page(capture.get('input_url',''),'','')
    page=pages[0]
    snippet=page.get('body_excerpt') or '\n'.join(e.get('quote','') for e in page.get('evidence',[]))
    result=classify_page(page.get('url',capture.get('input_url','')),page.get('title',''),snippet,metadata=dict(capture,**page))
    result.update(classification_scope='fetched_primary_page',fetched_at=page.get('fetched_at'),human_identity_review_required=result['kind'] in ('unknown','third_party_document') or result['basis'].startswith('bounded operating'))
    if result['human_identity_review_required']:result['notice']+=' 页面角色未知，加入客户前需要单独人工确认企业身份及依据。'
    return result

def classified_candidate(candidate):
    classification=classify_capture(candidate['capture'])
    candidate.update(source_classification=classification,source_type=source_type_for(classification),entity_review_required=classification['human_identity_review_required'])
    if classification['kind']=='third_party_document':candidate['evidence_level']='third_party_document_unverified'
    if classification['kind'] in ('directory','media','third_party_document'):
        candidate['match_hint'].update(status='source_reference_only',assessment_state='not_company_candidate',matched_terms=[],application_links=[],notice=classification['label']+'；仅保留来源，不作为公司客户或工装适配结论。'+(' 知识状态：'+'；'.join(candidate['match_hint']['knowledge_warnings']) if candidate['match_hint'].get('knowledge_warnings') else ''))
    return candidate

def substantive(text):
    return len(text)>=25 and (len(text.split())>=4 or len(re.findall(r'[\u4e00-\u9fff]',text))>=12)

def match_hint(factory,capture,knowledge=None):
    terms=list(dict.fromkeys(x.strip() for x in re.split(r'[,，、;；\n]',factory.get('focus','')+'\n'+factory.get('products','')) if 2<=len(x.strip())<=100))[:12]
    usable=[(p,e) for p in capture.get('pages',[]) for e in p.get('evidence',[]) if substantive(e.get('quote','')) and e.get('quote')!=p.get('title')]
    hits=[]; evidence=[]
    for term in terms:
        found=next(((p,e) for p,e in usable if norm(term) in norm(e['quote'])),None)
        if found:
            p,e=found;hits.append(term);evidence.append({'term':term,'quote':e['quote'],'source_url':e.get('source_url',p['url']),'fetched_at':p['fetched_at']})
    if not terms:state='factory_knowledge_missing'; reason='未提供可用工厂产品词；尚不能评估产品匹配。'
    elif not usable:state='website_evidence_insufficient'; reason='没有足够正文证据；导航或标题不作为产品匹配依据。'
    elif hits:state='keyword_overlap_only';reason='发现字面产品线索；仍需语义与接口核对，不代表产品匹配已验证。'
    else:state='semantic_analysis_not_run';reason='正文没有字面重合；未运行跨语言或语义分析，不能据此判定实际不匹配。'
    from .application_recall import recall,knowledge_state
    knowledge_status=[];seen=set()
    for k in knowledge or []:
        kstate=knowledge_state(k);key=(kstate["document_id"],kstate["version"],kstate["chunk_id"])
        if key not in seen:knowledge_status.append(kstate);seen.add(key)
    knowledge_warnings=list(dict.fromkeys(w for k in knowledge_status for w in k["warnings"]))
    leads=[];links=recall(factory,usable,knowledge or [],leads)
    if links:state='application_related_unverified';reason='中英文工艺概念召回发现可能的工装应用关联；尚未核对工件、机床或夹具接口，不代表实际适配或采购需求。'
    if knowledge_warnings:reason+=' 知识状态：'+'；'.join(knowledge_warnings)
    if leads:reason+=' 齿轮线索待核验：公司名称、标题或单独词语不能证明产品工艺关联，正文未提供足够依据；不推断适配或采购。'
    return {'application_leads':leads,'knowledge_status':knowledge_status,'knowledge_warnings':knowledge_warnings,'application_links':links,'status':'local_evidence_only','assessment_state':state,'matched_terms':hits,'evidence':evidence,'semantic_analysis':'not_run','actual_compatibility':'unknown','purchase_intent':'unknown','usable_body_evidence_count':len(usable),'notice':reason+' 未调用模型；来源为网站自述，不是独立证明。'}


def source_type_for(classification):
    if classification['kind']=='third_party_document':return 'third_party_document_unverified'
    if classification['kind'] in ('directory','media'):return 'directory_or_platform'
    return 'company_page_unverified'
