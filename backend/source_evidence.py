"""Bounded quotations and exact source claims; no entity verification or fuzzy matching."""
import re
import unicodedata
from urllib.parse import urlsplit
from .search_identity import host
from .discovery_quality import classify_page
from .application_recall import hobbing_statement

VERSION='source-evidence-v1'
LEGAL=r'(?:有限责任公司|股份有限公司|有限公司|GmbH(?:\s*&\s*Co\.?\s*KG)?|\b(?:LLC|Ltd\.?|Limited|Inc\.?|AG|KG)(?!\w))'
PROCESS=[('hobbing','滚齿',r'滚齿|\bhobbing\b|Wälzfräsen'),('gear_grinding','磨齿',r'磨齿|gear grinding|Wälzschleifen'),('gear_processing','齿轮加工',r'齿轮加工|齿轮生产|gear manufacturing|Lohnverzahnung|Zahnradfertigung'),('cnc_machining','CNC 加工',r'CNC[- ]?(?:加工|Drehen|Fräsen|machining|milling|turning)')]

def exact(value):
    return ' '.join(unicodedata.normalize('NFC',value).split())

def legal_names(title,summary):
    names=[]
    for raw in [title]+summary.splitlines():
        line=re.sub(r'^\s*#{1,6}\s*','',raw).strip()
        if re.match(r'powered by\b',line,re.I):continue
        attribution=re.search(r'(?:欢迎(?:访问|来到)[“"「]?\s*|official website of\s+|welcome to\s+)([^\n”"」|]{2,100}?'+LEGAL+r')',line,re.I)
        value=attribution.group(1) if attribution else re.split(r'\s*[|｜]\s*',line)[0]
        value=re.sub(r'^(?:Gears?|Products?|Services?|Home|About us|Company profile)\s+[-–—:]\s+','',value,flags=re.I)
        if len(re.findall(LEGAL,value,re.I))>1:continue
        match=re.fullmatch(r'([\w\u4e00-\u9fff &.,()（）-]{2,110}?'+LEGAL+r')(?:,\s*[\w -]{1,40})?',value,re.I)
        if match:
            name=exact(match.group(1))
            if name not in names:names.append(name)
    return names

def address_claim(summary):
    streets=list(dict.fromkeys(exact(v) for v in re.findall(r'(?m)^\s*([^\n|]{2,70}(?:Str\.|Straße|Strasse|street|road|straße)\s+\d+[a-zA-Z]?)\s*$',summary,re.I)))
    places=list(dict.fromkeys(exact(v) for v in re.findall(r'(?m)^\s*(\d{5}\s+[^\n|]{2,60})\s*$',summary)))
    return (streets[0]+' / '+places[0] if len(streets)==len(places)==1 else None),len(streets)>1 or len(places)>1

def assessment(candidate):
    p=candidate.get('search_provenance') or {};url=p.get('source_url') or candidate.get('url','');title=p.get('title') or candidate.get('suggested_name','')
    summary=p.get('summary','');snippet=p.get('snippet','');summary=summary[:4000] if isinstance(summary,str) else '';snippet=snippet[:3000] if isinstance(snippet,str) else ''
    c=classify_page(url,title,snippet);names=legal_names(title,summary);path=urlsplit(url).path.casefold()
    listing=bool(re.search(r'/(?:suche|search|themen|directory|directories|companies|suppliers)(?:/|$)',path))
    detail=bool(re.search(r'/(?:company|profile|firma|supplier|hytd_detail|selldetail)/[^/]+',path))
    product=bool(re.search(r'/(?:products?|product-categor(?:y|ies)|catalog|brochures?|downloads?)(?:/|$)',path))
    official=bool(re.search(r'欢迎(?:访问|来到)[^\n]{0,120}官方(?:网站|站点)|official website of|welcome to[^\n]{0,100}official website',summary,re.I))
    own=bool(re.search(r'本公司.{0,12}(?:生产|提供|加工)|我们.{0,12}(?:生产|提供|制造)|\bwe\s+(?:manufacture|produce|provide|offer)\b|\bwir\s+(?:fertigen|produzieren|bieten)\b',summary,re.I))
    if re.search(r'本公司.{0,10}(?:不|未|不能)|我们.{0,10}(?:不|未|不能)|\bwe\s+(?:do not|cannot|never)\b',summary,re.I):own=False
    if c['kind']=='third_party_document':form='document_reference'
    elif c['kind']=='directory' or listing or re.search(r'company directory|business directory|supplier directory|directory listing|Unternehmensverzeichnis|企业目录|企业黄页',title+' '+summary+' '+snippet,re.I):
        c.update(label='目录 / 企业详情参考 · 身份待核',kind='directory',business_role='directory_reference',source_type='directory_or_platform',single_company_candidate=False)
        form='company_detail_reference' if detail and not listing and len(names)==1 else 'directory_list'
    elif c['kind']=='media':form='editorial_reference'
    elif product:form='product_material'
    elif official and len(names)==1:form='official_site_candidate'
    elif c['kind']=='company' or own:form='company_detail_candidate'
    else:form='unknown'
    labels={'official_site_candidate':'官网候选 · 自述待核','company_detail_candidate':'企业详情线索 · 身份待核','company_detail_reference':'目录企业详情 · 第三方待核','directory_list':'目录列表 · 不计企业候选','document_reference':'PDF / 文件资料 · 不计企业候选','product_material':'产品资料 · 不计企业候选','editorial_reference':'报道 / 行业资料','unknown':'来源归属待确认'}
    c.update(page_role=form,page_role_label=labels[form],listing_scope='multiple_entities_or_search_results' if form=='directory_list' else 'single_entry_or_unknown')
    reference=form in ('directory_list','company_detail_reference','document_reference','product_material','editorial_reference')
    if reference:c['single_company_candidate']=False
    elif form in ('official_site_candidate','company_detail_candidate') and (official or own):c.update(kind='company',source_type='company_page_unverified')
    identity=None;address,address_conflict=address_claim(summary)
    normalized=exact(title+' '+summary)
    attribution_conflict=len(names)==1 and any(not normalized[:m.end()].endswith(names[0]) for m in re.finditer(LEGAL,normalized,re.I))
    if not candidate.get('capture') and form in ('official_site_candidate','company_detail_candidate') and len(names)==1 and not address_conflict and not attribution_conflict and (official or own):
        identity={'name':names[0],'publisher':host(url),'address':address,'basis':'same_publisher_exact_legal_name_and_self_statement','evidence':[names[0],summary[:400] if official else 'bounded first-person operating statement'],'source_url':url,'claim_type':'website_self_attribution','identity_verified':False}
    evidence=[];seen=set()
    for origin,text in [('search_summary',summary),('search_snippet',snippet)]:
        for line in re.split(r'[\n。!?；;]',text):
            line=line.strip()
            if not line or len(line)>800:continue
            for process,label,pattern in PROCESS:
                m=re.search(pattern,line,re.I)
                if not m or (process,line) in seen:continue
                seen.add((process,line));start=max(0,m.start()-100)
                if re.search(r'不|未|没有|不能|计划|如果|可能|\b(?:not|never|cannot|without|kein|nicht|plan|if|could|would)\b',line,re.I):state='negated_or_uncertain'
                elif reference:state='reference_only'
                elif re.search(r'设备|机床|hobbing machine|machine tool',line,re.I):state='equipment_mention_unverified'
                else:state='self_statement_unverified' if hobbing_statement(line) else 'mention_unverified'
                evidence.append({'process':process,'label':label,'quote':line[start:start+400],'source_url':url,'evidence_kind':origin,'observed_at':p.get('searched_at'),'status':state,'company_attribution_verified':False,'capability_verified':False,'purchase_intent':'unknown'})
                if len(evidence)>=12:break
            if len(evidence)>=12:break
        if len(evidence)>=12:break
    pending=['企业法定身份与网站归属','加工能力的实际设备、规格及有效性','当前采购需求与联系人']
    if len(names)>1 or attribution_conflict or address_conflict:pending.insert(0,'来源出现多个法定名称，未自动分组')
    if reference:pending.insert(0,'资料与单一企业的对应关系')
    return {'version':VERSION,'classification':c,'identity_claim':identity,'named_entities':names,'capability_evidence':evidence,'pending_confirmation':pending,'purchase_intent':'unknown','identity_verified':False,'source_preserved':True}

def attach(candidate,value=None):
    if candidate.get('source_kind')!='search_result' or candidate.get('capture'):return candidate
    from .lead_candidates import project
    value=value or assessment(candidate);candidate['source_assessment']=value;candidate['source_classification']=value['classification'];candidate['source_type']=value['classification']['source_type'];candidate['lead']=project(candidate,value)
    return candidate
