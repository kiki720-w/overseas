"""Unverified source-page leads, separate from legal identity and CRM decisions."""
import re
from .search_identity import host
from .source_evidence import exact, legal_names
from .discovery_quality import operating_role

GENERIC={'custom','gear','gears','precision','manufacturer','manufacturing','products','product','services','service','home','about','us','company','profile','cnc','machining','china','solutions','solution','technology','technologies','industrial','industry','factory','supplier','suppliers','processing','processes','process','capabilities','capability','contact','quality','why','choose','our'}
PROCESS=re.compile(r'齿轮|滚齿|磨齿|CNC|\b(?:gears?|hobbing|grinding|machining|milling|turning)\b',re.I)
BUSINESS=re.compile(r'(?:生产|制造|加工).{0,18}齿轮|(?:本公司|公司|我们|为客户).{0,100}(?:生产|制造|加工|提供|定制)|\b(?:we|our shop)\s+(?:(?:also|can|directly|speciali[sz]e in)\s+)?(?:manufactur(?:e|ing)|produce|provide|offer|support|make|machine)\b',re.I)
UNCERTAIN=re.compile(r'不(?:提供|生产|制造|加工|承接)|未提供|计划|假如|如果|例如|客户(?:声称|表示)|\b(?:not|never|cannot|would|could|unless|if|plan|planned|example)\b',re.I)
EQUIPMENT=re.compile(r'\bwe\b.{0,35}\b(?:sell|supply|offer|manufacture)\b.{0,55}\b(?:machine tools?|hobbing machines?|CNC gear machines?)\b|我们.{0,15}(?:销售|供应|制造).{0,25}(?:机床|滚齿机)',re.I)


def display_name(title,summary,review):
    names=review['named_entities']
    if len(names)==1:
        name=re.sub(r'^(?:公司介绍|公司简介)\s*[-—:：]\s*','',names[0])
        return (name if name in summary else names[0]),'source_name'
    if len(names)>1:return None,None
    # Display claims only: this does not change strict legal-identity grouping.
    segments=[re.sub(r'^(?:公司介绍|公司简介)\s*[-—:：]\s*','',part.strip())
              for part in re.split(r'[|｜]',title)]
    title_names=list(dict.fromkeys(n for part in segments for n in legal_names(part,'')))
    title_names=[n for n in title_names if n in summary]
    if len(title_names)==1:return title_names[0],'source_name'
    if len(title_names)>1:return None,None
    # Only a bounded company introduction, not names scattered among clients.
    inline=re.findall(r'(?m)^([^\n。]{2,65}?有限公司)(?=成立于|始建于|创立于)',summary)
    named=list(dict.fromkeys(n for value in inline for n in legal_names(value,'')))
    if len(named)==1:return named[0],'source_name'
    if len(named)>1:return None,None
    brands=list(dict.fromkeys(re.findall(r'(?m)^([\u4e00-\u9fff]{2,12}(?:集团|技术))(?=深耕|专注|现在拥有)',summary)))
    brands=[b for b in brands if not re.match(r'^(?:本公司|我们|客户|合作|供应商|加工|精密|先进|专业)',b)
            and (b in title or summary.count(b)>=2)]
    if len(brands)==1:return brands[0],'brand'
    if len(brands)>1:return None,None
    groups=re.findall(r'(?:^|[-—|｜])([\u4e00-\u9fff]{2,8}集团)(?=[—|｜]|$)',title)
    groups=[g for g in dict.fromkeys(groups) if len(re.findall(re.escape(g[:-2])+r'现在拥有',summary))>=2]
    if len(groups)==1:return groups[0],'brand'
    for part in re.split(r'\s*\|\s*',title):
        if not re.fullmatch(r'[\u4e00-\u9fffA-Za-z0-9]{2,16}(?:齿轮|机械|精工|制造|科技)',part):continue
        if re.match(r'^(?:精密|定制|数控|中国|加工|产品)',part):continue
        if re.search(re.escape(part)+r'(?:专注|提供|生产|制造|拥有)',summary):return exact(part),'brand'
    by=re.search(r'\bManufacturing by ([A-Z][A-Za-z0-9 &.-]{2,50})$',title)
    if by and by[1] in summary:return exact(by[1]),'brand'
    for part in reversed(re.split(r'\s*\|\s*',title)):
        words=re.findall(r'[A-Za-z][A-Za-z0-9.-]*',part)
        if not 1<=len(words)<=8 or len(part)>80:continue
        distinctive=[word for word in words if word.casefold() not in GENERIC]
        if distinctive and any(re.fullmatch(r'[A-Z][A-Za-z0-9.-]{2,30}',word) for word in distinctive):
            return exact(part),'branch_or_brand' if re.search(r'\bin\b',part) else 'brand'
    return None,None


def project(candidate,review):
    provenance=candidate.get('search_provenance') or {}
    title=provenance.get('title') or candidate.get('suggested_name','')
    summary=provenance.get('summary') or '';snippet=provenance.get('snippet') or ''
    result={'eligible':False,'website_verified':False,'identity_verified':False,'terminal_buyer_verified':False,
            'purchase_intent':'unknown','unit':'source_page_not_unique_company','business_evidence':[]}
    role=review['classification']['page_role']
    if candidate.get('source_kind')!='search_result' or candidate.get('capture'):
        return dict(result,reason='当前来源不属于搜索摘要线索')
    if role in ('directory_list','company_detail_reference','document_reference','editorial_reference'):
        return dict(result,reason='第三方、目录或文件参考，不自动作为终端加工线索')
    offer=summary+'\n'+snippet
    if operating_role(offer)[0] in ('equipment_supplier','equipment_solution_page') or EQUIPMENT.search(offer):
        return dict(result,reason='设备供应/方案来源，不是终端加工买家证明')
    name,kind=display_name(title,summary,review)
    if not name:return dict(result,reason='没有足够明确的品牌、分厂或来源名称')
    evidence=[]
    for origin,body in (('search_summary',summary),('search_snippet',snippet)):
        for quote in re.split(r'[\n。!?；;]|(?<=[a-z])\.\s+',body):
            quote=quote.strip()
            if quote.startswith('#') or exact(quote)==exact(title):continue
            if not 8<=len(quote)<=800 or UNCERTAIN.search(quote):continue
            brand_statement=kind=='brand' and re.match(re.escape(name)+r'(?:深耕|专注).{0,60}(?:研发|制造|生产)',quote)
            if PROCESS.search(quote) and (BUSINESS.search(quote) or brand_statement):
                if any(item['quote']==quote for item in evidence):continue
                evidence.append({'quote':quote,'source_url':candidate['url'],'evidence_kind':origin})
                if len(evidence)==2:break
        if len(evidence)==2:break
    if not evidence:return dict(result,display_name=name,name_basis=kind,reason='已有名称，但没有可直接引用的加工/制造业务原句')
    missing=['法律主体与站点归属未核验','当前采购需求未知']
    if kind!='source_name':missing.insert(0,'缺少可核对的完整法律名称')
    if kind=='branch_or_brand':missing.insert(0,'品牌/分厂与法律主体的关系待核实')
    if role=='product_material':missing.insert(0,'来源为产品材料，不是企业身份依据')
    terms=list(dict.fromkeys(match.group(0) for item in evidence for match in PROCESS.finditer(item['quote'])))[:4]
    return dict(result,eligible=True,display_name=name,name_basis=kind,publisher=host(candidate['url']),
                source_url=candidate['url'],business_evidence=evidence,missing_identity=missing,
                website_status='来源站点，疑似官网归属未核实',
                match_reason='业务原句提到 '+ '、'.join(terms)+'，可作为加工业务线索；工装适配和终端买家身份待核实',
                reason='品牌/来源名称与加工业务原句同时存在；仅展示待核实线索')
