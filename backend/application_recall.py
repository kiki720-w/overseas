"""Human-maintained bilingual process-to-tooling recall, not compatibility scoring."""
import re

ONTOLOGY_VERSION='process-tooling-v3-hobbing-body'
CONCEPTS=[
 {'id':'gear_workholding','label':'齿轮加工／齿轮工装应用线索','factory_aliases':['滚齿夹具','磨齿夹具','珩齿夹具','齿轮夹具','齿轮工装','gear fixture','gear workholding'],
  'query':'滚齿夹具','buyer_patterns':[r'\b(?:manufactur(?:e[sd]?|ing)|produc(?:e[sd]?|ing|tion)|machin(?:e[sd]?|ing))\s+(?:(?:of|custom|precision|tight|tolerance|high|your|the|and)\s+){0,7}(?:gears|splines)\b',r'\b(?:machining|turning|milling|hobbing|grinding)\s+(?:services?\s+)?(?:for|of)\s+(?:(?:custom|precision|your|the)\s+){0,4}(?:gears|splines|gear\s+(?:blanks|components))\b',r'\b(?:offers?|provides?|our)\s+(?:(?:custom|precision)\s+){0,3}gear\s+(?:manufacturing|production|machining|hobbing|grinding)\b']},
 {'id':'machining_center_workholding','label':'CNC加工／加工中心工装应用线索','factory_aliases':['加工中心夹具','CNC夹具','CNC 夹具','五轴夹具','cnc fixture','cnc workholding'],
  'query':'加工中心夹具','buyer_patterns':[r'\bcnc\s+(?:machining|milling|turning)\b',r'\b(?:multi|[2-5])[- ]axis\b.{0,50}\bmachin\w*\b']},
]
CONCEPTS.insert(0,{'id':'gear_hobbing_workholding','label':'滚齿工序→滚齿夹具应用假设（非采购需求）',
 'factory_aliases':['滚齿夹具','滚齿工装','hobbing fixture','hobbing workholding'],'query':'滚齿夹具','buyer_patterns':[]})

def affirmative_context(text):
    """Accept only a direct, unconditional own statement; uncertain passages are not parsed."""
    value=text.strip().casefold()
    if not value.startswith(('我们','we ','our shop ')):return False
    if value.startswith(('我们的客户','我们客户','我们仅引用')):return False
    contrary=('不','未','并无','没有','需要先','如果','计划','拟提供','可能','引用','引述','表示','说法','合作伙伴')
    if any(marker in value for marker in contrary):return False
    if re.search(r'\b(?:not|never|cannot|can.t|without|false|untrue|if|unless|would|could|plan|planned|need to)\b',value):return False
    return True

def hobbing_statement(quote):
    """Bounded first-party process statement; equipment sales and negation are not demand."""
    if not affirmative_context(quote):return None
    for sentence in re.split(r'[。;；\n]',quote):
        if re.search(r'(?:客户|文章|报道|示例|他人).{0,8}(?:表示|称|说|介绍)|引用|引述',sentence):continue
        if re.search(r'不(?:提供|承接|生产|制造|进行)|没有滚齿|无需滚齿|\b(?:not|never|without|don.t)\b',sentence,re.I):continue
        if re.search(r'我们.{0,15}(?:销售|供应|制造).{0,25}(?:滚齿机|夹具)|\b(?:sell|supply|manufacture)\b.{0,35}\b(?:hobbing machines?|fixtures?)\b',sentence,re.I):continue
        if re.search(r'我们(?:自行|直接|内部|专门)?(?:定制|生产|制造)(?:的)?滚齿(?:正|斜)?齿轮',sentence):return 'first_party_hobbed_gear_product'
        if re.search(r'我们(?:的车间)?(?:为客户)?(?:提供|承接|进行)(?:专业|定制)?(?:齿轮)?滚齿加工(?:服务)?',sentence):return 'first_party_hobbing_service'
        if re.search(r'\b(?:we|our shop)\s+(?:directly\s+)?(?:offers?|provides?|performs?)\s+(?:precision\s+)?(?:gear\s+)?hobbing\s+(?:services?|operations?)\b',sentence,re.I):return 'first_party_hobbing_service'
        if re.search(r'\bwe\s+(?:directly\s+)?(?:manufacture|produce|make)\s+(?:custom\s+)?hobbed\s+(?:spur\s+|helical\s+)?gears\b',sentence,re.I):return 'first_party_hobbed_gear_product'
    return None

def knowledge_state(k):
    reasons=[]
    if k.get('expired'):reasons.append('expired')
    if k.get('conflict'):reasons.append('conflict')
    if k.get('enabled') is False:reasons.append('disabled')
    if k.get('archived'):reasons.append('archived')
    if k.get('review_status') not in ('source_checked','confirmed','unverified'):reasons.append('review_not_usable')
    warnings=list(dict.fromkeys(str(x)[:400] for x in k.get('warnings',[])))[:8]
    labels={'expired':'知识已过期，不参与召回','conflict':'知识存在冲突，不参与召回','disabled':'知识已停用，不参与召回','archived':'知识已归档，不参与召回','review_not_usable':'知识审核状态不可用，不参与召回'}
    warnings.extend(labels[x] for x in reasons if labels[x] not in warnings)
    if k.get('review_status')=='unverified' and '知识未核实' not in warnings:warnings.append('知识未核实')
    return {'document_id':k.get('document_id'),'version':k.get('version'),'chunk_id':k.get('chunk_id'),'location':k.get('location'),'review_status':k.get('review_status'),'review_info':k.get('review_info'),'expired':bool(k.get('expired')),'conflict':bool(k.get('conflict')),'enabled':k.get('enabled',True),'archived':bool(k.get('archived')),'eligible_for_recall':not reasons,'exclusion_reasons':reasons,'warnings':warnings,'independently_proven':False}

def recall(factory,usable,knowledge,leads=None):
    links=[]
    for concept in CONCEPTS:
        sources=[];related_knowledge=False
        for k in knowledge:
            text=k.get('title','')+' '+k.get('text','')
            alias=next((a for a in concept['factory_aliases'] if a.casefold() in text.casefold()),None)
            if alias:
                related_knowledge=True;state=knowledge_state(k)
                if state['eligible_for_recall'] and k.get('document_id') and k.get('chunk_id'):
                    sources.append(dict(state,source='knowledge',title=k.get('title',''),source_company=k.get('source_company'),quote=k['text'][:400],matched_factory_phrase=alias))
        if not sources and not related_knowledge:
            for field in ['products','capabilities']:
                text=factory.get(field,'')
                if re.search(r'虚构|合成|synthetic|demo',text,re.I):continue
                alias=next((a for a in concept['factory_aliases'] if a.casefold() in text.casefold()),None)
                if alias:sources.append({'source':'factory_field','field':field,'quote':text[:400],'matched_factory_phrase':alias,'review_status':'user_entered_not_independently_verified','warnings':['产品能力设置为用户填写，未独立验证'],'independently_proven':False})
        if not sources:continue
        weak=None
        for page,e in usable:
            quote=e['quote']
            if quote==page.get('title') or e.get('role') in ('title','heading','navigation','nav'):continue
            if not affirmative_context(quote):continue
            if concept['id']=='gear_hobbing_workholding':
                pattern=hobbing_statement(quote)
                if pattern:
                    links.append({'concept_id':concept['id'],'label':concept['label'],'method':'human_maintained_bilingual_process_recall','ontology_version':ONTOLOGY_VERSION,'rule':pattern,'factory_sources':sources[:2],'customer_evidence':{'quote':quote,'source_url':e.get('source_url',page['url']),'fetched_at':page['fetched_at'],'evidence_level':'website_self_statement'},'actual_compatibility':'unknown','machine_fixture_interface':'unknown','purchase_intent':'unknown','confidence':'application_hypothesis_requires_human_review','AI_called':False})
                    break
                continue
            if re.search(r"\b(?:not|never|without|don't)\b.{0,65}\b(?:cnc|gear|machin|manufactur|produc)\w*",quote,re.I):continue
            if re.search(r'\b(?:sell|distribut|resell|supplier of|serve gear manufactur)\w*',quote,re.I):continue
            if not re.search(r'\b(?:offers?|provides?|manufactures?|produces?|we\s+machine|machining services|milling|turning|hobbing|grinding|capabilit\w*|specializ\w*)\b',quote,re.I):continue
            pattern=next((p for p in concept['buyer_patterns'] if re.search(p,quote,re.I)),None)
            if concept['id']=='gear_workholding' and (not pattern or not re.search(r'\b(?:we|our|offers?|provides?|manufactures?|produces?|specializ\w*|capabilit\w*)\b',quote,re.I)):
                if re.search(r'\b(?:gears?|splines?)\b',quote,re.I):weak=(page,e)
                continue
            if not pattern:continue
            links.append({'concept_id':concept['id'],'label':concept['label'],'method':'human_maintained_bilingual_process_recall','ontology_version':ONTOLOGY_VERSION,'rule':pattern,'factory_sources':sources[:2],'customer_evidence':{'quote':quote,'source_url':e.get('source_url',page['url']),'fetched_at':page['fetched_at'],'evidence_level':'website_self_statement'},'actual_compatibility':'unknown','machine_fixture_interface':'unknown','purchase_intent':'unknown','confidence':'application_hypothesis_requires_human_review','AI_called':False})
            break
        else:
            if weak and leads is not None:
                page,e=weak
                leads.append({'concept_id':concept['id'],'status':'requires_verification','reason':'名称或单独词语命中不足：正文未明确把齿轮产品与企业实际提供的加工工艺联系起来；仅作为待核验线索，不形成应用关联。','factory_sources':sources[:2],'customer_evidence':{'quote':e['quote'],'source_url':e.get('source_url',page['url']),'fetched_at':page['fetched_at']},'actual_compatibility':'unknown','purchase_intent':'unknown','AI_called':False})
    return links
