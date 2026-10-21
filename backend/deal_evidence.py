"""Finite clause parser: identity/medium/field/value/unit/conditions stay together."""
import copy
import re

PROTOCOL='engineering-transcription-v3'
MODEL=r'[A-Za-z]+-?\d+[A-Za-z]*'
MODEL_RE=re.compile(r'(?<![A-Za-z0-9])'+MODEL+r'(?![A-Za-z0-9])')
NUMBER=r'\d+(?:\.\d+)?(?:\s*[-~–至]\s*\d+(?:\.\d+)?)?'
PRESSURE_LABEL=r'供气压力|气动压力|气压|液压压力|工作压力|压力|(?:air|pneumatic|hydraulic)\s+(?:operating|working)\s+pressure|(?:air|pneumatic|hydraulic|operating|working|supply)\s+pressure|pressure'
PORT_LABEL=r'气路接口|气动接口|供气接口|air\s+(?:port|interface)|pneumatic\s+(?:port|interface)'
PRESSURE=re.compile(r'(?P<label>'+PRESSURE_LABEL+r')\s*[:：=]\s*(?P<value>'+NUMBER+r')\s*(?P<unit>(?-i:MPa|kPa)|bar|psi)(?P<condition>\s+(?:at|在)\s*\d+(?:\.\d+)?\s*(?:°C|℃))?',re.I)
PORT=re.compile(r'(?P<label>'+PORT_LABEL+r')\s*[:：=]\s*(?P<value>(?:G|R)\d+/\d+|\d+/\d+\s*NPT|NPT\s*\d+/\d+)',re.I)
NEGATIVE=re.compile(r'未提供|未知|不详|示例|仅供|假设|待确认|待核实|未核实|撤回|撤销|废止|作废|不适用|不应|不得|不能作为|无适用|非.{0,12}规格|不作为|\b(?:not|no|never|unknown|example|hypothetical|unconfirmed|withdrawn|revoked|obsolete|invalid|inapplicable)\b|to\s+be\s+confirmed',re.I)
NUMERIC=re.compile(r'(?<![\w.])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?:/\d+|\s*[-~–至]\s*\d+(?:\.\d+)?)?\s*(?:MPa|kPa|Pa|bar|psi|mm|cm|rpm|N[·.]?m|kN|Hz|°C|℃)\b|(?<!\w)(?:G|R)\d+/\d+\w*|\d+/\d+\s*NPT\w*|\bNPT\s*\d+/\d+\w*',re.I)
PARAMETER=re.compile(PRESSURE_LABEL+'|'+PORT_LABEL,re.I)
UNITS=re.compile(r'\b(?:MPa|kPa|Pa|bar|psi|rpm|kN|Hz)\b',re.I)

def models(text):
    return list(dict.fromkeys(m.upper() for m in MODEL_RE.findall(text)
        if not re.match(r'^(?:ISO|DIN|IEC|EN)\d|^[GR]\d+$',m,re.I)))

def literal(value):return re.sub(r'\s+','',value).casefold()

def unit_identity(value):
    """SI symbols retain case. Only existing non-SI word aliases are folded.

    Whitespace between a number and a unit is parsed separately. Spaces inside
    an SI symbol are never removed, and no numerical conversion is performed.
    """
    symbol=value.strip()
    return symbol.casefold() if symbol.casefold() in ('bar','psi') else symbol

def label_scope(label):
    medium='air' if re.search(r'气|air|pneumatic',label,re.I) else 'hydraulic' if re.search(r'液压|hydraulic',label,re.I) else 'unspecified'
    qualifier='supply' if re.search(r'supply',label,re.I) or '供气' in label else 'operating' if re.search(r'工作|operating|working',label,re.I) else 'unspecified'
    return medium,qualifier

def request(question):
    # Complete controlled clauses only. Unknown words/conditions are retained,
    # never collapsed into an unspecified parameter.
    query=question.strip().rstrip('?\uff1f').strip()
    parts=re.split(r'\s+(?:and)\s+|[;\uff1b\u4e0e\u548c]',query,flags=re.I)
    active=[];pending=[];parameters=[];unchecked=[]
    condition=r'(?P<condition>\s+(?:at|\u5728)\s*\d+(?:\.\d+)?\s*(?:\u00b0C|\u2103))?'
    field_clause=re.compile(r'(?P<label>'+PRESSURE_LABEL+'|'+PORT_LABEL+r')'+condition,re.I)
    for original in parts:
        part=original.strip()
        if not part:unchecked.append(original);continue
        if re.fullmatch(MODEL,part):pending.append(part.upper());continue
        prefix=re.match(r'^('+MODEL+r')\s+(?=\S)',part)
        local=pending+([prefix.group(1).upper()] if prefix else [])
        if prefix:part=part[prefix.end():]
        match=field_clause.fullmatch(part)
        if not match:
            unchecked.append(original);pending=[];continue
        if local:active=list(dict.fromkeys(local))
        pending=[]
        label=match['label'];field='air_interface' if re.fullmatch(PORT_LABEL,label,re.I) else 'pressure'
        medium,qualifier=label_scope(label) if field=='pressure' else ('air','unspecified')
        for model in active or [None]:
            item={'model':model,'field':field,'medium':medium,'qualifier':qualifier,
                'conditions':(match['condition'] or '').strip(),'question_clause':original}
            if item not in parameters:parameters.append(item)
    if pending:unchecked.extend(pending)
    fields=list(dict.fromkeys(p['field'] for p in parameters))
    # Unsupported engineering text remains an unchecked request, not all covered.
    if not fields and re.search(PRESSURE_LABEL,question,re.I):fields.append('pressure')
    if not fields and re.search(PORT_LABEL,question,re.I):fields.append('air_interface')
    return {'fields':fields,'models':list(dict.fromkeys([p['model'] for p in parameters if p['model']]+models(question))),
        'parameters':parameters,'unchecked_fragments':unchecked if fields else [],
        'question_verbatim':question}

def technical(text):
    return bool(NUMERIC.search(text) or UNITS.search(text) or
        (PARAMETER.search(text) and re.search(r'(?<!\w)\d',text)))

def parse_block(text,default_model=None):
    """Only anchored labelled clauses or exact model headings, never last mentions.

    A negative/uncertain paragraph is withheld in full. This favours conservative
    omissions over assigning a rejected value to another model.
    """
    if NEGATIVE.search(text):return [],['negative_or_uncertain_source']
    result=[];issues=[];heading=default_model;heading_origin='title_or_explicit_default' if default_model else ''
    for line_index,line in enumerate(text.splitlines(),1):
        raw_line=line.strip()
        line=line.strip().strip('"“”').rstrip('。.')
        head=re.fullmatch(r'(?:(?:型号|model)\s*[:：])?\s*('+MODEL+r')\s*',line,re.I)
        if head:heading=head.group(1).upper();heading_origin='body_heading';continue
        line_model=heading;origin=heading_origin
        for clause in re.split(r'[;；]',line):
            clause=clause.strip().strip('"“”').rstrip('。.')
            prefix=re.match(r'^('+MODEL+r')\s+(?=\S)',clause)
            if prefix:line_model=prefix.group(1).upper();origin='body_prefix';clause=clause[prefix.end():]
            matched=PRESSURE.fullmatch(clause);field='pressure'
            if not matched:matched=PORT.fullmatch(clause);field='air_interface'
            if not matched:
                if technical(clause):issues.append('unsupported_or_ambiguous_parameter_clause')
                continue
            if not line_model:issues.append('model_not_bound_by_prefix_or_heading');continue
            if field=='pressure':
                medium,qualifier=label_scope(matched['label']);condition=(matched['condition'] or '').strip();unit=matched['unit']
            else:medium='air';qualifier='unspecified';condition='';unit='thread_literal'
            result.append({'model':line_model,'field':field,'medium':medium,'qualifier':qualifier,
                'value':matched['value'],'unit':unit,'conditions':condition,'literal':matched.group(0),'quote':raw_line,
                'label_verbatim':matched['label'],'model_binding':origin,'source_line':line_index})
    return result,issues

def key(row):
    return tuple(unit_identity(row[k]) if k=='unit' else literal(row[k])
        for k in ('model','field','medium','qualifier','value','unit','conditions'))

def matches_parameter(row,parameter):
    return row['field']==parameter['field'] and (parameter['medium']=='unspecified' or row['medium']==parameter['medium']) and (
        parameter['qualifier']=='unspecified' or row['qualifier']==parameter['qualifier']) and (
        not parameter['conditions'] or literal(row['conditions'])==literal(parameter['conditions']))

def assess_legacy(context):
    asked=request(context.get('deal_request',{}).get('question',''));parameters=asked['parameters'];wanted=asked['models']
    extracts=[];issues=['unchecked_question_structure'] if asked['unchecked_fragments'] else []
    for chunk in context.get('knowledge',[]):
        title=chunk.get('title','').strip()
        title_model=title.upper() if re.fullmatch(MODEL,title) else None
        rows,errors=parse_block(chunk['text'],title_model);issues.extend(errors)
        for row in rows:
            if not any(row['model']==p['model'] and matches_parameter(row,p) for p in parameters):continue
            ref=next((e for e in context.get('evidence_catalog',[]) if e['source']=='knowledge' and
                all(e.get(k)==chunk.get(k) for k in ('document_id','version','chunk_id')) and row['quote'] in e['quote']),None)
            if not ref:issues.append('complete_clause_not_in_reference');continue
            extracts.append(dict(row,ref_id=ref['ref_id'],document_id=chunk['document_id'],
                version=chunk['version'],chunk_id=chunk['chunk_id']))
    missing=[];conflicts=[];ambiguous=[]
    for parameter in parameters:
            model=parameter['model']
            rows=[e for e in extracts if e['model']==model and matches_parameter(e,parameter)]
            identity=dict(parameter)
            if not rows:missing.append(dict(identity,reason='no_accepted_bound_tuple_in_selected_reviewed_context'))
            elif len({(e['medium'],e['qualifier'],literal(e['conditions'])) for e in rows})>1:
                ambiguous.append(dict(identity,reason='multiple_media_qualifiers_or_conditions_require_confirmation'))
            elif len({(literal(e['value']),unit_identity(e['unit'])) for e in rows})>1:conflicts.append(identity)
    if not asked['fields']:status='not_assessed'
    elif asked['unchecked_fragments']:status='unchecked'
    elif any(p['model'] is None for p in parameters):status='needs_model_confirmation'
    elif conflicts or any(k.get('conflict') for k in context.get('knowledge',[])):status='conflicting_evidence'
    elif ambiguous:status='needs_scope_confirmation'
    elif not extracts and any(i!='negative_or_uncertain_source' for i in issues):status='unchecked'
    elif not extracts:status='evidence_missing'
    elif missing:status='partial'
    else:status='supported_extracts'
    mode='source_bounded' if status in ('supported_extracts','partial','not_assessed') else 'clarification_only'
    media=list(dict.fromkeys(p['medium'] for p in parameters if p['field']=='pressure'))
    questions=[]
    if status=='needs_model_confirmation':questions.append('请确认产品型号及气动或液压应用；首条检索型号不代表您的型号。')
    if missing or ambiguous or status in ('needs_model_confirmation','conflicting_evidence'):
        questions.append('请提供对应型号、介质、适用条件的压力/气路接口规格；本次预览不能确认缺失或冲突参数。')
    return {'protocol':PROTOCOL,'assessment_scope':'selected_reviewed_preview_only','requested_fields':asked['fields'],
        'requested_models':wanted,'requested_parameters':parameters,'pressure_medium':media[0] if len(media)==1 else 'multiple' if media else '',
        'status':status,'supported_extracts':extracts,'missing':missing,'conflicts':conflicts,'ambiguous':ambiguous,
        'question_verbatim':asked['question_verbatim'],'unchecked_fragments':asked['unchecked_fragments'],
        'unverified_reasons':list(dict.fromkeys(issues)),'generation_mode':mode,'clarification_questions':questions,
        'limitations':['Anchored labelled clauses only; unsupported prose/conditions are unverified, not proof of absence.',
            'No conversions, suitability proof, independent proof or corpus-wide absence claim.']}

def safe_draft(coverage):
    model=', '.join(coverage['requested_models']) or 'the product model'
    return ('Thank you for your question. Please confirm '+model+' and the pneumatic or hydraulic application. '
        'The selected information does not establish all requested specifications and their applicable conditions. '
        'Please share the corresponding specification or drawing for technical review. '
        'We will keep unverified parameters open until the correct specification is confirmed.')

def assess(context):
    from .deal_semantic_contract import assess_contract
    return assess_contract(context,assess_legacy,request,models,PRESSURE_LABEL,PORT_LABEL)

def guard(result,context):
    from .deal_semantic_contract import guard_output
    return guard_output(result,context)

INSTRUCTION='''deal_request.answer_coverage 为服务端有限字段检查，不是通用语义正确性或独立证明。
每个参数必须同时绑定型号、介质、字段、单位、数值和适用条件，不换算或补值。
数值参数用单独标注行逐字转录 supported_extracts；不能引用正确原文后另加错误归属。
clarification_only 只写诚实澄清草稿；partial 列出未覆盖参数；任何撤回或不适用原文不作为规格。
不得声称全库/工厂不存在参数，只能说明本次已审核预览未建立该参数或条件。'''
