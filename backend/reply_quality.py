"""Finite consistency constraints. No general semantic correctness guarantee."""
import copy, re, hashlib, json

QUALITY_INSTRUCTION='''\n复用 customer.facts、customer.unknowns、records 和本次已核对知识片段；已明确模拟询盘时标模拟，不再问是否真实。区分企业公开自述背景与模拟工件需求，不编造真实采购需求。已知齿轮磨削不再问加工工艺，只问尚缺的机型、方式或装夹接口。用户本轮明确要求数量且未给定时，数量必须进入草稿本轮问题，可和图纸尺寸合并为一组。若存在冲突，先说明冲突而不选择一值。未知清单是输入缺失检查，不得引用 E003 产品描述或 K 宣传册为整个未知清单背书；逐项声明仅引用能支持该项的原文。技术资料引用只说明具体摘要文字和来源公司、状态，不证明实际适配。不要通过删除全部内容或统一全未知模板规避问题。'''

from .reply_slots import profile,quantity_question

QUALITY_INSTRUCTION += '\n每个字段保留独立状态与准确证据片段：只把明确陈述视为输入已述，不把问题/否定/未知升为事实。数量关联对象，夹具数不同于工件产量；完整保留千分位、小数、范围，不确定不填数字。精度要求与测量方法是两个独立字段。系统补充建议必须标明来源，不能冒称模型原文。'

QUALITY_INSTRUCTION += '\n数量问题必须明确对象：工件数量（pieces/parts per batch or order）与本次夹具套数（fixture units required for this review）是两个字段。英文草稿不要单独使用 quantity concerned、quantity needed this time 或 per part/per order 代替对象。两项都需要时可合并为一个明确区分两者的问题；已问本次夹具套数则不重复追加同一问题。不得从工厂小批量服务推断客户工件批量。'

def constrain_ordinary(result,context):
    return copy.deepcopy(result)

def compose_contextual(intents,context):
    from .structured_core.composer import QUESTIONS
    if not isinstance(intents,list) or any(i not in QUESTIONS for i in intents):return None
    p=profile(context);paras=['System-composed clarification draft; not model-written or approved.']
    if p['simulated']:paras.append('This is a simulated selection exercise, not a verified customer inquiry or purchase requirement.')
    # A verbatim request/background quote, explicitly attributed and never interpreted
    # as real purchasing intent; commands embedded in source text remain quoted data.
    q=p['question']
    if q:paras.append('User-provided scenario and background (unverified; quoted as input):\n'+q)
    if p['grinding']:paras.append('Gear grinding is already stated in the input; the actual machine and mounting interface still need technical review.')
    # Only scoped catalog knowledge (adapter validated reviewed_knowledge first).
    for e in context.get('evidence_catalog',[]):
        if e.get('source')=='knowledge' and re.search(r'(?<![A-Za-z0-9])S9(?![A-Za-z0-9])',q,re.I) and re.search(r'(?<![A-Za-z0-9])S9(?![A-Za-z0-9])',e.get('quote',''),re.I):
            if e.get('review_status') not in ('source_checked','confirmed'):continue
            paras.append('Selected catalog summary ['+e['ref_id']+'] ('+e.get('source_company','source company unspecified')+'; '+e['review_status']+'; not independent proof or verified fit):\n'+e['quote'])
    for code in dict.fromkeys(intents):
        if code=='clarify_process':
            needed=[]
            if p['slots']['process']['state']!='known':needed.append('machining process')
            if not p['supplied']['machine']:needed.append('machine model')
            if not p['supplied']['interface']:needed.append('fixture mounting/interface details')
            if needed:paras.append('Could you share or clarify the '+' and '.join(needed)+' for technical review?')
        elif code=='clarify_quantity':
            if p['quantity_state']!='known':paras.append(quantity_question(p))
        elif code=='clarify_drawing':
            needed=[]
            if not p['supplied']['drawing']:needed.append('workpiece drawing')
            if not p['supplied']['dimensions']:needed.append('key dimensions')
            if not p['supplied']['interface']:needed.append('clamping/interface dimensions')
            if needed:paras.append('Could you share the '+' and '.join(needed)+' for technical review?')
        elif code=='clarify_accuracy':
            needed=[]
            if not p['supplied']['accuracy']:needed.append('accuracy requirement')
            if not p['supplied']['measurement_method']:needed.append('measurement method')
            if needed:paras.append('Could you confirm the '+' and '.join(needed)+'?')
        else:paras.append(QUESTIONS[code])
    if p['quantity_state']=='known':
        slot=p['slots']['fixture_quantity'];qty=slot['value'];ref=next(e for e in slot['evidence'] if e['interpretation']=='known')
        paras.append('Input-stated fixture quantity: '+qty['amount']+' '+qty['unit']+'. This is not an order confirmation. Source '+ref['source']+'/'+str(ref.get('field',ref.get('record_id')))+': '+ref['quote'])
    if p['quantity_requested'] and p['quantity_state']!='known' and quantity_question(p) not in paras:paras.append(quantity_question(p))
    if not paras or not intents:return None
    return 'Subject: Selection information for technical review\n\nHello,\n\n'+'\n\n'.join(paras)+'\n\nBest regards,\nFactory team'

def review_consistency(result,context):
    """Add actionable triage without claiming reference entailment validation."""
    if not result.get('_needs_review'):return result
    p=profile(context);out=result['original'];issues=result['review']['issues']
    for path in ('draft','questions','next_step','match','unknowns'):
        value=out.get(path,'');text='\n'.join(value) if isinstance(value,list) else value
        if p['simulated'] and re.search(r'是否.{0,8}真实|whether this is a real|confirm.{0,20}real selection',text,re.I):
            issues.append(dict(path=path,category='known_scenario_reasked',reason='输入已明确模拟；删除重复真实性反问，保留模拟限定。',original_text=text))
        if p['grinding'] and re.search(r'说明.{0,8}(?:磨齿|工艺)|confirm the machining process|主要加工工艺',text,re.I):
            issues.append(dict(path=path,category='known_process_reasked',reason='粗工艺已知；仅核对未给定机型、加工方式细节或装夹接口。',original_text=text))
        if path in ('match','unknowns') and re.search(r'(?:未知|未提供|unknown).{0,25}[（(][EK]\d{3}[）)]',text,re.I):
            issues.append(dict(path=path,category='missing_information_reference_review',reason='逐项核对引用；产品/技术资料不能证明完整客户缺失清单。',original_text=text))
    result['review']['reply_consistency_scope']='finite_triage_only_not_semantic_entailment'
    result['review']['reply_quality_proposal']=ordinary_proposal(out,context)
    return result

def current_quantity_ask(draft):
    # Recognize entire finite requests. Only a specific terminal disclaimer is
    # removable; negation of the actual request remains unrecognized.
    opening=r'(?:(?:could|would|can) you (?:please )?|please )'
    action=r'(?:share|confirm|provide|tell us)'
    subject=r'(?:(?:the|your) )?(?:required )?(?:fixture quantity|quantity of fixtures|number of fixtures|required quantity)'
    suffix=r'(?: for (?:(?:this|technical) )?review)?[?.]?'
    for line in draft.splitlines():
        line=line.strip()
        if line.lower().startswith('subject:'):continue
        line=re.sub(r'\s*\((?:not an order confirmation|not a purchase order|for review only)\)\s*(?=[?.]?$)','',line,flags=re.I)
        if re.fullmatch(opening+action+' '+subject+suffix,line,re.I):return True
        if re.fullmatch(r'How many fixtures (?:are required|do you need)(?: for (?:(?:this|technical) )?review)?\?',line,re.I):return True
        # The exact locally generated clarification is also a current request.
        if line in (quantity_question({'quantity_state':'unknown'}),quantity_question({'quantity_state':'ambiguous'}),quantity_question({'quantity_state':'conflict'})):return True
    return False

def match_text_and_offsets(text):
    """Normalize newlines only in a temporary match view.

    Boundary offsets map Python code points back to the exact original string;
    no source text, hash, or recorded span is normalized.
    """
    chars=[];offsets=[];index=0
    while index<len(text):
        offsets.append(index)
        if text[index]=='\r':
            chars.append('\n')
            index+=2 if index+1<len(text) and text[index+1]=='\n' else 1
        else:
            chars.append(text[index]);index+=1
    offsets.append(len(text))
    return ''.join(chars),offsets

def scoped_quantity_clause(text):
    """Recognize one finite, unquoted three-item question block; otherwise abstain."""
    view,offsets=match_text_and_offsets(text)
    # Quoted/attributed/negated examples cannot be silently edited.
    if re.search(r'[>"“”`]|\b(?:said|wrote|quoted|quote|example|not|never|do not)\b',view,re.I):return None
    if len(re.findall(r'could you share:',view,re.I))!=1:return None
    blocks=list(re.finditer(r'(?m)^(?:To review whether this category could suit your application, )?could you share:\n'
        r'1\) the workpiece drawing and key dimensions(?: \(module, number of teeth, bore, outside diameter, face width\))?;\n'
        r'2\) your gear grinding machine model(?: and the table/fixture mounting interface, plus how the part is located and clamped \(tooth, bore or face; any adhesive expansion sleeve\))?;\n'
        r'3\) the key accuracy requirement, how it is measured(?P<clause>, and the quantity needed this time)\.(?=\n\n|$)',view,re.I))
    if len(blocks)!=1:return None
    m=blocks[0]
    # Unknown surrounding prose may attribute or negate the block. Abstain.
    prefix=view[:m.start()].strip();suffix=view[m.end():].strip()
    if not re.fullmatch(r'(?:(?:Subject: Selection information for technical review)\n\n)?(?:(?:Hello,|Dear Sir/Madam,)\n\n)?',prefix+'\n\n' if prefix else ''):return None
    if suffix not in ('','Best regards,','Best regards,\nFactory team'):return None
    # Finite item descriptions must contain no secondary questions or attribution.
    if len(re.findall(r'quantity needed this time',view,re.I))!=1:return None
    start,end=offsets[m.start('clause')],offsets[m.end('clause')]
    return start,end

def ordinary_proposal(out,context):
    p=profile(context);before=out.get('draft','');after=before;changes=[];suggestions=[]
    ambiguous=bool(re.search(r'quantity needed this time|quantity concerned\s*\(per part and per order, if different\)',before,re.I))
    located=scoped_quantity_clause(before) if p['quantity_requested'] and p['quantity_state']!='known' else None
    if located and not current_quantity_ask(before):
        start,end=located;after=before[:start]+before[end:]
        changes.append(dict(origin='system_suggestion',rule='current_round_fixture_quantity',operation='remove_exact_question',removed_text=before[start:end],source_span={'start':start,'end':end}))
    if p['quantity_requested'] and p['quantity_state']!='known' and not current_quantity_ask(before):
        text=quantity_question(p)
        if ambiguous and located is None:
            text='Manual review required: clarify whether the existing quantity question asks about workpieces or fixtures; no automatic draft change.'
        suggestions.append(dict(origin='system_suggestion',rule='current_round_fixture_quantity',text=text,slot_state=copy.deepcopy(p['slots']['fixture_quantity'])))
        if before.strip() and not (ambiguous and located is None):
            note='[System suggestion — not model output]\n'+text
            pos=re.search(r'(?im)^best regards',after)
            after=after[:pos.start()]+note+'\n\n'+after[pos.start():] if pos else after+'\n\n'+note
            original_pos=re.search(r'(?im)^best regards',before)
            offset=original_pos.start() if original_pos else len(before)
            changes.append(dict(origin='system_suggestion',rule='current_round_fixture_quantity',operation='insert',added_text=note,source_span={'start':offset,'end':offset}))
    if p['quantity_state']=='known':
        query='Could you confirm the required quantity?'
        match_text,offsets=match_text_and_offsets(before)
        for m in reversed(list(re.finditer(r'(?m)^'+re.escape(query)+r'[ \t]*$',match_text))):
            start,end=offsets[m.start()],offsets[m.end()]
            removed=before[start:end]
            changes.append(dict(origin='system_suggestion',rule='explicit_fixture_quantity_already_stated',operation='remove_exact_question',removed_text=removed,source_span={'start':start,'end':end},slot_state=copy.deepcopy(p['slots']['fixture_quantity'])))
            after=after[:start]+after[end:]
    scope_hash=hashlib.sha256(json.dumps(context,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    for items in (changes,suggestions):
        seen=set();unique=[]
        for item in items:
            logical='rqs-'+hashlib.sha256((scope_hash+'|'+item['rule']).encode()).hexdigest()[:24]
            item['suggestion_id']=logical
            identity={'scope':scope_hash,'rule':item['rule'],'text':item.get('text',item.get('added_text',item.get('removed_text'))),'operation':item.get('operation','suggest'),'source_span':item.get('source_span')}
            item['id']=logical if 'operation' not in item else 'rqc-'+hashlib.sha256(json.dumps(identity,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:24]
            if item['id'] not in seen:unique.append(item);seen.add(item['id'])
        items[:]=unique
    original_hash=hashlib.sha256(before.encode()).hexdigest()
    return dict(protocol='reply-quality-proposal-v1',id='rqp-'+hashlib.sha256((scope_hash+original_hash).encode()).hexdigest()[:24],context_sha256=scope_hash,
        origin='system_suggestion',model_output_preserved=True,source_field='original.draft',
        original_sha256=hashlib.sha256(before.encode()).hexdigest(),derived_sha256=hashlib.sha256(after.encode()).hexdigest(),
        suggested_draft=after if changes else None,changes=changes,suggestions=suggestions,slots=p['slots'],other_quantity_evidence=p['other_quantity_evidence'],semantic_correctness_validated=False)


def english_word_count(text):
    """English words; contractions and hyphenated words count as one."""
    return len(re.findall(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*",text))


CAPABILITY_TERMS={
    'turning':r'\bturning\b|车削|车加工',
    'grinding':r'\bgrinding\b|磨削|磨齿',
    'cnc_machining':r'\bCNC(?:[- ]machining)?\b|数控加工',
    'milling':r'\bmilling\b|铣削',
}

def supplier_capabilities(context):
    """User-declared factory capabilities; customer text never proves supplier ability."""
    text=context.get('factory',{}).get('capabilities','')
    declared=[]
    for clause in re.split(r'[;；。\n]+',text):
        if re.search(r'待核|未知|未确认|未核实|不具备|不能|\b(?:unverified|unknown|not|no|never|pending|may|might)\b',clause,re.I):continue
        declared.extend(k for k,v in CAPABILITY_TERMS.items() if re.search(v,clause,re.I) and k not in declared)
    return dict(basis='user_declared_factory_field',declared_processes=declared,independently_verified=False)

def evidence_provenance(context):
    """Server-owned operation scope, separate from exact quote references.

    Manual legacy fields have no acquisition timestamp/round. A save date is not
    a fetch date, and a historical sentence saying 'this round' is not telemetry.
    """
    customer=context.get('customer',{});records={r.get('id'):r for r in context.get('records',[])}
    hints='\n'.join(str(customer.get(k,'')) for k in ('source','facts','unknowns','blocker'))
    summary=bool(re.search(r'搜索摘要|检索摘要|search (?:result|summary|snippet)',hints,re.I))
    sources=[]
    for ref in context.get('evidence_catalog',[]):
        kind='legacy_record_statement';saved_at=None;observations=[]
        if ref['source']=='factory':kind='user_declared_factory_information'
        elif ref['source']=='knowledge':kind='selected_knowledge_excerpt'
        elif ref['source']=='record':
            record=records.get(ref.get('record_id'),{})
            kind='captured_body_self_statement' if record.get('website_capture_id') and record.get('evidence_level')=='website_self_statement' else 'user_record_statement'
            observations=record.get('source_observations',[])
            saved_at=record.get('created_at') or None
        elif summary:kind='legacy_search_summary_or_user_statement'
        sources.append(dict(ref_id=ref['ref_id'],source_kind=kind,observed_at=observations[0]['observed_at'] if len(observations)==1 else None,round_id=None,record_saved_at=saved_at,observations=observations))
    return dict(protocol='evidence-provenance-v1',operation='model_analysis',fetch_performed_by_operation=False,
                business_round_id=None,legacy_observation_time='unknown',sources=sources)

def outreach_scope(context):
    customer=context.get('customer',{});records=context.get('records',[])
    text='\n'.join(str(customer.get(k,'')) for k in ('source','facts','unknowns','blocker'))
    body=any(r.get('website_capture_id') and r.get('evidence_level')=='website_self_statement' for r in records)
    summary=bool(re.search(r'搜索摘要|检索摘要|search (?:result|summary|snippet)',text,re.I))
    failed=bool(re.search(r'Fetch.{0,35}(?:404|失败|无正文|未获得|未获)|fetch.{0,35}(?:failed|no (?:usable )?body)',text,re.I))
    first=customer.get('stage')=='待开发' and not any(not r.get('website_capture_id') for r in records)
    question=context.get('deal_request',{}).get('question','')
    if question and re.search(r'数量|套数|批量|图纸|报价|交期|quantity|drawing|quote|lead time',question,re.I):first=False
    return {'first_contact':first,'source_basis':'fetched_body_self_statement' if body else 'search_summary_only' if summary else 'unconfirmed_source',
            'website_ownership':'unverified','reported_fetch_failed':failed,'independently_proven':False,
            'fetch_failure_basis':'historical_input_statement' if failed else 'not_reported',
            'current_operation_fetch':'not_performed','observation_time':'unknown','business_round':'unknown'}

def outreach_instruction(context):
    scope=outreach_scope(context)
    instructions=['来源边界：只有网址不等于已查阅网页；搜索/检索摘要不是实际抓取正文。公开正文仍是来源自述，不是已核实官网归属、企业身份、加工能力或采购意向的证明。不得写“已查看/核实贵司官网”或 from your website，除非输入明确支持实际查阅和归属；仅有历史摘要时应写 public search summary。Fetch失败应在内部保留，不必把HTTP错误生硬写入英文开发信。',
      '时序边界：本次 model_analysis 操作不执行 Fetch。evidence_provenance 是本机标注，观察时间或业务轮次缺失就是未知；记录保存日期不代表抓取日期。旧记录中的“本轮Fetch失败”只是历史输入陈述，不能改写成当前操作抓取失败。应明确说历史记录提及抓取失败、发生时间/轮次未知；只有摘要不能写“网页原文”或“已抓取正文”。',
      '供方能力边界：客户的加工场景、客户资料和 hypotheses 中的产品类别不能证明我方能提供车削、磨削或CNC加工。supplier_capabilities 仅标注工厂 capabilities 字段明确自述的工艺，仍未经独立核实。不要写没有输入依据的 We work with / We provide / Our capabilities include 等能力声明；可以中性询问对方场景，不暗示已确认我方适配能力。对该字段明确支持的能力可按用户自述适度表达，不扩大范围。']
    if scope['first_contact']:
        instructions.append('本次是首次接触：首封只询问是否为合适联系人、是否方便交流相关加工/装夹场景。不要索取图纸、具体机型/接口/精度，不询问采购套数、批量、报价、预算或交期；这些留待对方愿意交流并提供真实需求后再问。未知项可留在内部清单，不能全部塞进邮件或下一步第一轮要求。')
    instructions.append('英文草稿目标80–160词（含称呼与落款，缩写和连字符词按一词计）；生成前自查长度。词数或质量不合格时保留完整原稿并提示人工修订，不得截断、悄悄重写或自动发起新的付费调用。有限规则零警告不等于语义全面正确或来源已经核实，始终需要人工复核。')
    return '\n'.join(instructions)

def outreach_issue_messages(count):
    return {'draft_word_count':f'英文草稿 {count} 词，目标 80–160 词；完整原文已保留，请人工修订。',
            'source_attribution':'草稿把待核实站点或搜索摘要写成已查阅/已核实官网；请按实际来源改写，不提高证据等级。',
            'first_contact_burden':'首次接触问题过早涉及图纸、详细技术或采购参数；先确认联系人及交流意愿，把细节留待后续。',
            'supplier_capability':'输出可能暗示未由工厂能力字段支持的我方加工能力；请人工核实供方能力，客户场景不能作为供方能力证明。',
            'evidence_time':'输出把历史或时间未知的 Fetch 失败写成当前操作结果；本次分析未执行 Fetch，保留历史陈述并标明时间/轮次未知。',
            'evidence_grade':'输出可能把搜索摘要或未确认来源写成网页原文/已抓取正文；请保留实际证据等级。'}

def outreach_review(draft,context,result=None):
    """Finite local warnings only; never edits text, verifies identity, or retries."""
    scope=outreach_scope(context);count=english_word_count(draft);issues=[]
    def warn(code):
        if not any(i['code']==code for i in issues):issues.append({'code':code,'message':outreach_issue_messages(count)[code]})
    if not 80<=count<=160:warn('draft_word_count')
    official=re.search(r'\byour (?:verified |official )*official website\b|已核实.{0,8}官网|核实.{0,8}官网归属',draft,re.I)
    claimed=re.search(r'\bfrom your (?:public |official )?website\b|\bwe (?:have )?(?:reviewed|visited|checked|read) your (?:public |official )?website\b|已(?:查看|查阅|浏览).{0,10}官网',draft,re.I)
    if official or scope['source_basis']!='fetched_body_self_statement' and claimed:warn('source_attribution')
    if scope['first_contact']:
        for sentence in re.split(r'(?<=[.!?])\s+|\n+',draft):
            asks='?' in sentence or re.search(r'\b(?:please|could you|would you|can you)\b',sentence,re.I)
            details=re.search(r'\b(?:drawings?|blueprints?|quantit(?:y|ies)|batch|units?|how many|quot(?:e|ation)s?|pric(?:e|ing)|budget|delivery|lead time|machine model|mounting interface|accuracy requirement)\b',sentence,re.I)
            negated=re.search(r'\b(?:do not|don.t|not asking|no need to|not required|not needed)\b',sentence,re.I)
            if asks and details and not negated:warn('first_contact_burden');break
    texts=[draft]
    if result:
        texts.extend(result.get(k,'') for k in ('match','angle','next_step','unknowns','stage_blocker','notice'))
        texts.extend(result.get('hypotheses',[]));texts.extend(result.get('questions',[]))
    declared=supplier_capabilities(context)['declared_processes']
    for text in texts:
        for sentence in re.split(r'(?<=[.!?。])\s*|\n+',text):
            uncertain=bool(re.search(r'未|未知|不具备|不代表|不得|不能|待核|\b(?:not|no|never|unknown|unverified|may|might)\b',sentence,re.I))
            supplier=re.search(r'\bwe (?:work with|offer|provide|support|speciali[sz]e in|can|manufacture)\b|\bour (?:capabilities|services|factory|company)\b|我方.{0,12}(?:提供|支持|具备|擅长)|我们.{0,10}(?:提供|支持|具备|擅长)',sentence,re.I)
            if supplier and not uncertain and any(re.search(v,sentence,re.I) and k not in declared for k,v in CAPABILITY_TERMS.items()):warn('supplier_capability')
            historical=bool(re.search(r'历史|此前|上轮|旧记录|旧资料|previous|historical|earlier',sentence,re.I))
            current_failure=re.search(r'(?:本轮|本次|当前).{0,20}(?:Fetch|抓取).{0,30}(?:失败|未获得|未获|无正文|404)|(?:this|current) (?:round|operation).{0,30}fetch.{0,25}(?:failed|no (?:usable )?body)',sentence,re.I)
            if current_failure and not historical:warn('evidence_time')
            if scope['source_basis']!='fetched_body_self_statement':
                for claim in re.finditer(r'网页原文|已抓取正文|抓取到的正文|fetched (?:page )?body|full webpage text',sentence,re.I):
                    prefix=sentence[max(0,claim.start()-18):claim.start()]
                    if not re.search(r'不是|不等于|并非|未获得|没有|不能称为|not |no |never ',prefix,re.I):warn('evidence_grade')
    return dict(scope,word_count=count,target_min=80,target_max=160,count_rule='English alphanumeric words; contractions and hyphenated words count as one; greeting/sign-off included',
                issues=issues,requires_human_review=True,semantic_correctness_validated=False,review_protocol='outreach-boundaries-v2',
                automatic_rewrite=False,automatic_truncation=False,automatic_paid_retry=False)

def public_outreach_review(value):
    """Serialize finite local triage metadata, never raw provider fields."""
    if not isinstance(value,dict) or type(value.get('word_count')) is not int or not 0<=value['word_count']<=12000:return None
    if value.get('source_basis') not in ('search_summary_only','fetched_body_self_statement','unconfirmed_source'):return None
    if type(value.get('first_contact')) is not bool or type(value.get('reported_fetch_failed')) is not bool:return None
    count=value['word_count'];messages=outreach_issue_messages(count);raw=value.get('issues');codes=[]
    if not isinstance(raw,list) or len(raw)>len(messages):return None
    for issue in raw:
        if not isinstance(issue,dict) or issue.get('code') not in messages:return None
        if issue['code'] not in codes:codes.append(issue['code'])
    out={'word_count':count,'target_min':80,'target_max':160,'first_contact':value['first_contact'],
         'source_basis':value['source_basis'],'reported_fetch_failed':value['reported_fetch_failed'],
         'website_ownership':'unverified','independently_proven':False,
         'issues':[{'code':code,'message':messages[code]} for code in codes],
         'requires_human_review':True,'semantic_correctness_validated':False,
         'automatic_rewrite':False,'automatic_truncation':False,'automatic_paid_retry':False}
    if value.get('review_protocol')=='outreach-boundaries-v2':
        out.update(review_protocol='outreach-boundaries-v2',current_operation_fetch='not_performed',observation_time='unknown',business_round='unknown',
                   fetch_failure_basis='historical_input_statement' if value['reported_fetch_failed'] else 'not_reported')
    return out
