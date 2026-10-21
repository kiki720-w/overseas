"""Per-field evidence + explicit manual review, not prose entailment inference."""
import copy,re,json
from .common import AppError
EXTRA_FIELDS={'rated_torque':r'额定扭矩|rated\s+torque','roughness':r'粗糙度|roughness','current_price':r'当前售价|售价|current\s+price'}

def plan(question,legacy_request,models,pressure_label,port_label):
    parsed=legacy_request(question);wanted=models(question);atoms=[]
    for p in parsed['parameters']:
        label=re.search(pressure_label if p['field']=='pressure' else port_label,p['question_clause'],re.I)
        if label:atoms.append(dict(p,label=label.group(0),original_question=question))
    # Named slots are extracted regardless of the order of English/Chinese
    # wording. With multiple subjects, unparsed model/field binding stays unknown.
    for field,pattern in [('pressure',pressure_label),('air_interface',port_label)]+list(EXTRA_FIELDS.items()):
        for match in re.finditer(pattern,question,re.I):
            if any(a['field']==field and a['label'].casefold()==match.group(0).casefold() for a in atoms):continue
            model=wanted[0] if len(wanted)==1 else None
            condition=re.search(r'(?:at|在)\s*\d+(?:\.\d+)?\s*(?:°C|℃)',question,re.I)
            atoms.append({'model':model,'field':field,'label':match.group(0),'conditions':condition.group(0) if condition else '',
                'medium':'unspecified','qualifier':'unspecified','question_clause':question,'original_question':question})
    remainder=question
    # This is only an exact finite clause contract. Unknown wording is never
    # forced into it; it is allowed into communication/manual review instead.
    for a in atoms:remainder=re.sub(re.escape(a['label']),' ',remainder,flags=re.I)
    for m in wanted:remainder=re.sub(r'(?<![A-Za-z0-9])'+re.escape(m)+r'(?![A-Za-z0-9])',' ',remainder,flags=re.I)
    remainder=re.sub(r'\b(?:and|of)\b|[?？,，、;；和与及\s]','',remainder,flags=re.I)
    pure=bool(atoms) and not remainder
    return {'question_verbatim':question,'requirements':atoms,'requested_models':wanted,
        'pure_finite_field_request':pure,'unparsed_question_scope':question if not pure else None,
        'request_fully_semantically_validated':False}

def assess_contract(context,legacy_assess,legacy_request,models,pressure_label,port_label):
    question=context.get('deal_request',{}).get('question','')
    p=plan(question,legacy_request,models,pressure_label,port_label);outcomes=[];supported=[];missing=[]
    for a in p['requirements']:
        if a['field'] in EXTRA_FIELDS:
            state='field_not_supported_by_finite_parser';extracts=[];reason='Field remains unverified; selected text is not proof of corpus-wide absence.'
        elif not a['model']:
            state='model_field_binding_unconfirmed';extracts=[];reason='Exact subject/field association requires confirmation.'
        else:
            atomic=copy.deepcopy(context);atomic['deal_request']={'question':a['model']+' '+a['label']+(' '+a['conditions'] if a['conditions'] else '')}
            atomic['knowledge']=[k for k in atomic.get('knowledge',[]) if not k.get('expired') and k.get('review_status') in ('confirmed','source_checked')]
            check=legacy_assess(atomic);state=check['status'];extracts=check['supported_extracts'] if state=='supported_extracts' else []
            reason='Selected reviewed preview does not establish this field and scope.'
        item={'model':a['model'],'field':a['field'],'label_verbatim':a['label'],'original_question':question,'state':state,'supported':bool(extracts)}
        outcomes.append(item);supported.extend(extracts)
        if not extracts:missing.append(dict(item,reason=reason))
    if outcomes and all(o['supported'] for o in outcomes):status='supported_extracts'
    elif supported:status='partial'
    elif any(o['state']=='conflicting_evidence' for o in outcomes):status='conflicting_evidence'
    elif outcomes:status='evidence_missing'
    else:status='not_assessed'
    mode='finite_source_preview' if p['pure_finite_field_request'] and not missing else 'finite_missing_preview' if p['pure_finite_field_request'] else 'communication_review'
    result={'protocol':'deal-semantic-contract-v3','assessment_scope':'selected_reviewed_preview_only','question_verbatim':question,
        'requested_models':p['requested_models'],'requested_fields':list(dict.fromkeys(a['field'] for a in p['requirements'])),
        'requested_parameters':p['requirements'],'status':status,'generation_mode':mode,'supported_extracts':supported,'missing':missing,
        'outcomes':outcomes,'unparsed_question_scope':p['unparsed_question_scope'],'unverified_reasons':['free_prose_requires_human_review'],
        'conflicts':[o for o in outcomes if o['state']=='conflicting_evidence'],'ambiguous':[o for o in outcomes if o['state']=='model_field_binding_unconfirmed'],
        'request_fully_semantically_validated':False,'complete_answer_validated':False,'clarification_questions':[],
        'limitations':['Finite field checks are not universal semantic entailment; preserve unparsed request and require review of novel prose.']}
    if context.get('_request_review_scope_internal'):return result
    from .request_review_scope import attach
    return attach(context,result,lambda c:assess_contract(c,legacy_assess,legacy_request,models,pressure_label,port_label))

def generation_permitted(coverage):
    return coverage.get('generation_mode') in ('communication_review','finite_source_preview')

def unknown_lines(coverage):
    return [str(m['model'] or 'subject unconfirmed')+' / '+m['label_verbatim']+' — 未知/待核实（'+m['state']+'）' for m in coverage.get('missing',[])]

COMMITMENT_HINT=re.compile(r'guarantee(?:d)?|promise|definitely suitable|lowest price|immediate availability|available.{0,25}lowest|meets every|solves all|适配|保证|承诺|最低价|现货|立即交付|全部认证',re.I)
SUGGESTION_HINT=re.compile(r'^(?:please|could|would|can|may|thank|help|suggest|ask|arrange|请|能否|是否|建议|感谢|麻烦)',re.I)
def reject_sensitive_content(text):
    if re.search(r'sk-[A-Za-z0-9_-]{20,}|Authorization\s*:\s*Bearer\s+\S{20,}',text,re.I):
        raise AppError('草稿含疑似凭据，不能进入待审展示',403,'review_sensitive_content')
def audit_prose(text,path):
    # Categories are triage hints, never proof of safety or factual correctness.
    if not text.strip():return []
    category='unsupported_commitment' if COMMITMENT_HINT.search(text) else 'communication_suggestion_review' if SUGGESTION_HINT.search(text) or path.startswith('questions') else 'unverified_statement'
    return [{'path':path,'category':category,'reason':'Human review required; no general semantic entailment verification.','original_text':text}]

def guard_output(result,context):
    coverage=context.get('deal_request',{}).get('answer_coverage')
    if not coverage:return result
    out=copy.deepcopy(result);hard=out.pop('_hard_commitment_issues',[])
    reject_sensitive_content(json.dumps(out,ensure_ascii=False))
    extracts=coverage.get('supported_extracts',[]);issues=[]
    accepted={e['literal'].strip().rstrip('.。') for e in extracts}|{(e['model']+' '+e['literal']).strip().rstrip('.。') for e in extracts}
    for field in ('draft','match','angle','next_step','unknowns','stage_blocker','questions','hypotheses'):
        values=out.get(field,[]) if field in ('questions','hypotheses') else [out.get(field,'')]
        for i,text in enumerate(values):
            path=field+'.'+str(i) if field in ('questions','hypotheses') else field
            if text.strip() and not all(line.strip().rstrip('.。') in accepted for line in text.splitlines() if line.strip()):issues.extend(audit_prose(text,path))
    issues.extend(hard)
    for fact in out.get('facts',[]):
        fact['answer_support']='source_excerpt_only'
        fact['parameter_bindings']=[dict(e,validation_scope='anchored_tuple_only') for e in extracts if any(r.get('source')=='knowledge' and all(r.get(k)==e.get(k) for k in ('document_id','version','chunk_id')) and e['quote']==r.get('quote') for r in fact.get('evidence',[]))]
    from .request_review_scope import visible_review
    request_review=visible_review(coverage,context.get('deal_request',{}).get('question',''))
    issues.extend({'path':'request_questions.'+q['question_id'],'category':'request_not_fully_checked',
                   'reason':q['reason'],'original_text':q['text_verbatim']} for q in request_review['unresolved_questions'])
    return {'_needs_review':True,'original':out,'review':{**request_review,
        'supported_transcriptions':[dict(e,validation_scope='anchored_tuple_only') for e in extracts],
        'source_catalog':copy.deepcopy(context.get('evidence_catalog',[])),
        'unknowns':copy.deepcopy(coverage.get('missing',[])),
        'unparsed_question_scope':coverage.get('unparsed_question_scope'),'issues':issues,
        'request_fully_semantically_validated':False,'edited_content_fact_checked':False}}
