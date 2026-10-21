"""Finite complete declarations with independent states and exact source spans."""
import re
SLOTS=('scenario','process','fixture_quantity','drawing','dimensions','interface','machine','accuracy','measurement_method')
UNKNOWN=re.compile(r'unknown|unspecified|not provided|not known|pending|tbd|n/?a|待核|未知|未提供|不明|待定',re.I)
NEGATIVE=re.compile(r'\b(?:not|never|no)\b|不采用|不是|并非|未采用|不确定|尚未|未确认',re.I)
LABELS={'process':r'process|工艺','fixture_quantity':r'fixture quantity|夹具数量|quantity|数量',
    'drawing':r'drawing|图纸','dimensions':r'(?:workpiece |key )?dimensions|工件尺寸|尺寸',
    'interface':r'(?:fixture |mounting )?interface|接口','machine':r'machine model|机型',
    'accuracy':r'accuracy|精度','measurement_method':r'measurement method|测量方法'}
SCENARIO_ALIASES=('simulated inquiry','synthetic inquiry','模拟询盘')
SCENARIO_ALIAS_PATTERN='(?:'+'|'.join(re.escape(alias) for alias in SCENARIO_ALIASES)+')'
SCENARIO_HINT=re.compile(SCENARIO_ALIAS_PATTERN+r'|scenario\s*[:：=]|simulation status\s*[:：=]|模拟状态|询盘类型',re.I)
SCENARIO_STANDALONE=re.compile(r'(?:本条为)?'+SCENARIO_ALIAS_PATTERN+r'[。.]?',re.I)

def source_scope(context,ref):
    if ref['source']=='customer':return context.get('customer',{}).get(ref['field'],'')
    if ref['source']=='record':return context.get('records',[])[ref['record_index']].get('text','')
    return context.get('deal_request',{}).get('question','')

def scope_has_opaque_scenario_context(context,ref):
    """Do not silently ignore neighboring qualifiers in the same source block.

    Explicit independent slot declarations are separately interpreted. Any other
    nonempty neighboring clause is opaque; preserve scope and stay ambiguous.
    """
    for part in source_scope(context,ref).splitlines():
        part=part.strip()
        if not part or part==ref['quote'].strip() or SCENARIO_HINT.search(part):continue
        if any(re.fullmatch(r'(?:'+label+r')\s*[:：=]\s*.+',part,re.I) for label in LABELS.values()):continue
        if re.fullmatch(r'(?:annual gear production quantity|gear production quantity|workpiece quantity|gear quantity|工件数量|齿轮产量)\s*[:：=]\s*.+',part,re.I):continue
        return True
    return False

def source_lines(context):
    for field in ('product','facts','source','unknowns'):
        text=context.get('customer',{}).get(field,'')
        if not isinstance(text,str):continue
        for m in re.finditer(r'[^\r\n]+',text):
            yield m.group(0),dict(source='customer',field=field,start=m.start(),end=m.end(),quote=m.group(0)),field=='unknowns'
    for i,row in enumerate(context.get('records',[])):
        text=row.get('text','')
        if not isinstance(text,str):continue
        for m in re.finditer(r'[^\r\n]+',text):
            yield m.group(0),dict(source='record',record_id=row.get('id'),record_index=i,start=m.start(),end=m.end(),quote=m.group(0)),False
    q=context.get('deal_request',{}).get('question','')
    if q:yield q,dict(source='deal_request',field='question',start=0,end=len(q),quote=q),True

def profile(context):
    slots={k:dict(state='unknown',value=None,evidence=[]) for k in SLOTS}
    other_quantity_evidence=[]
    def add(slot,state,value,ref,reason):
        slots[slot]['evidence'].append(dict(ref,interpretation=state,value=value,reason=reason))
    for raw,ref,request_only in source_lines(context):
        line=raw.strip();uncertain=request_only or '?' in line or '？' in line or bool(UNKNOWN.search(line)) or bool(NEGATIVE.search(line)) or bool(re.search(r'\b(?:maybe|perhaps|might|would|could|either)\b|可能|是否|或者|或许',line,re.I))
        # Route an explicit OTHER-object label without making it a fixture fact.
        # A mixed clause mentioning fixtures is not safely separable here.
        other=re.fullmatch(r'(annual gear production quantity|gear production quantity|workpiece quantity|gear quantity|工件数量|齿轮产量)\s*[:：=]\s*(.+)',line,re.I)
        other_only=bool(other and not re.search(r'fixture|夹具',other[2],re.I))
        if other_only:
            other_quantity_evidence.append(dict(ref,object_label=other[1],raw_value=other[2],state='unparsed_other_object',reason='explicit_other_object_label_not_fixture_evidence'))
        for slot,label in LABELS.items():
            if slot=='fixture_quantity' and other_only:continue
            m=re.fullmatch(r'('+label+r')\s*[:：=]\s*(.+)',line,re.I)
            if not m:
                if re.search(label,line,re.I):add(slot,'unknown' if UNKNOWN.search(line) else 'ambiguous',None,ref,'not_an_explicit_declaration')
                continue
            value=m[2].strip();state='unknown' if UNKNOWN.search(value) else 'ambiguous' if uncertain else 'known';parsed=value;reason='explicit_declaration'
            if state=='known':
                if slot=='fixture_quantity':
                    n=re.fullmatch(r'([1-9]\d{0,8}|[1-9]\d{0,2}(?:,\d{3})+)\s*(sets?|pieces?|套|件)\.?',value,re.I)
                    if m[1].casefold() not in ('fixture quantity','夹具数量'):
                        state,parsed,reason='ambiguous',None,'quantity_object_unspecified'
                    elif not n:state,parsed,reason='ambiguous',None,'quantity_format_or_range_requires_review'
                    else:parsed={'amount':n[1].replace(',',''),'unit':n[2],'object':'fixture'}
                elif slot=='process' and value.casefold().rstrip('.') not in ('grinding','turning','milling','hobbing','磨削','齿轮磨削','磨齿','滚齿'):
                    state,parsed,reason='ambiguous',None,'process_value_requires_review'
                elif slot in ('drawing','dimensions','interface') and value.casefold().rstrip('.') not in ('provided','attached','已提供'):
                    state,parsed,reason='ambiguous',None,'provided_status_requires_review'
                elif slot=='accuracy' and not re.fullmatch(r'\d+(?:\.\d+)?\s*(?:mm|μm|um)\.?',value,re.I):
                    state,parsed,reason='ambiguous',None,'accuracy_format_requires_review'
                elif slot=='machine' and (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 _/-]{1,60}',value) or not re.search(r'\d',value)):
                    state,parsed,reason='ambiguous',None,'machine_value_requires_review'
                elif slot=='measurement_method' and not re.fullmatch(r'(?:CMM|micrometer|dial indicator|三坐标|千分尺|百分表)\.?',value,re.I):
                    state,parsed,reason='ambiguous',None,'measurement_method_requires_review'
            add(slot,state,parsed if state=='known' else None,ref,reason)
        # No arbitrary suffix. The only additional supported clause explicitly
        # concerns absence of contact/history, not simulation status itself.
        sim=bool(SCENARIO_STANDALONE.fullmatch(line) or
            re.fullmatch(r'(?:本条为)?模拟询盘[，,]\s*未提供(?:真实)?(?:联系人|联系方式|历史沟通)(?:(?:、|或|和)(?:真实)?(?:联系人|联系方式|历史沟通))*[。.]?',line) or
            (ref.get('field')=='product' and re.fullmatch(r'模拟(?:齿轮磨削|磨齿)夹具需求[，,]仅用于(?:授权测试|离线测试|模拟验收)',line)))
        opaque_scope=bool((sim or SCENARIO_HINT.search(line)) and scope_has_opaque_scenario_context(context,ref))
        sim_uncertain=request_only or '?' in line or '？' in line or bool(NEGATIVE.search(line)) or opaque_scope
        if sim:add('scenario','ambiguous' if sim_uncertain else 'known','simulated' if not sim_uncertain else None,ref,'opaque_source_scope' if opaque_scope else 'explicit_simulation_declaration')
        elif SCENARIO_HINT.search(line):add('scenario','ambiguous',None,ref,'scenario_not_explicit_or_negated')
        if opaque_scope:
            scope=source_scope(context,ref)
            scope_ref=dict(ref,start=0,end=len(scope),quote=scope)
            add('scenario','ambiguous',None,scope_ref,'full_source_scope_requires_review')
        if ref.get('field')=='product' and re.fullmatch(r'模拟(?:齿轮磨削|磨齿)夹具需求[，,]仅用于(?:授权测试|离线测试|模拟验收)',line):add('process','known','grinding',ref,'explicit_simulated_product_application')
    for entry in slots.values():
        es=entry['evidence'];unique=[]
        for e in es:
            if e['interpretation']=='known' and e['value'] not in unique:unique.append(e['value'])
        # Requests never invalidate real source declarations, but never create known.
        status_sources=[e for e in es if e['source']!='deal_request']
        if len(unique)>1:entry['state']='conflict'
        elif any(e['interpretation']=='ambiguous' for e in status_sources) or (unique and any(e['interpretation']=='unknown' for e in status_sources)):entry['state']='ambiguous'
        elif unique:entry.update(state='known',value=unique[0])
        elif status_sources and all(e['interpretation']=='unknown' for e in status_sources):entry['state']='unknown'
        elif any(e['interpretation']=='ambiguous' for e in es):entry['state']='ambiguous'
    q=context.get('deal_request',{}).get('question','');qty=slots['fixture_quantity'];process=slots['process']
    return dict(slots=slots,other_quantity_evidence=other_quantity_evidence,simulated=slots['scenario']['state']=='known' and slots['scenario']['value']=='simulated',
        grinding=process['state']=='known' and str(process['value']).casefold().rstrip('.') in ('grinding','齿轮磨削','磨齿','磨削'),
        quantity_state=qty['state'],quantities=[qty['value']['amount']] if qty['state']=='known' else [],
        quantity_requested=bool(re.search(r'数量|quantity|how many',q,re.I)),question=q,
        supplied={k:slots[k]['state']=='known' for k in ('drawing','dimensions','interface','machine','accuracy','measurement_method')})

def quantity_question(p):
    return 'Could you clarify which fixture quantity should be used, given the differing explicit quantity statements?' if p['quantity_state']=='conflict' else 'Could you share the required fixture quantity for this review?'
