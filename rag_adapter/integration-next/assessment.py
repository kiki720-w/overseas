"""Separate local evidence coverage, canonical/owner authority and cloud consent."""
import dataclasses as dc
import re
from preview_gate.gate import EvidenceGate,Requirement,RequestPlan
from preview_gate.planner import FIELDS

EXTRA=(('overall_clamping_range',r'总夹持范围|夹持范围|clamping range|gripping range',True),
    ('smooth_bore',r'光孔|smooth bore',False),('radial_axial_grooves',r'径向槽和轴向槽|radial and axial grooves',False),
    ('radial_grooves',r'径向槽(?!和轴向)|radial grooves',False),
    ('slot_count',r'开槽数|number of slots|slot count',False),('slot_width',r'槽宽|slot width',True),
    ('length',r'长度|\blength\b',True),('taper',r'锥度|\btaper\b',False),
    ('selection_remarks',r'选型备注|selection remarks',False),('order_code',r'订货(?:型号|代码)|order code|order no',False),
    ('D',r'(?<![A-Za-z0-9])D(?![A-Za-z0-9])',True),('L',r'(?<![A-Za-z0-9])L(?![A-Za-z0-9])',True),
    ('W',r'(?<![A-Za-z0-9])W(?![A-Za-z0-9])',True),('series',r'系列|\bseries\b',False),
    ('machine_labels',r'机器标签|机床标签|machine labels',False),
    ('features',r'特点|特征|功能|features|benefits|describes? ',False),
    ('minimum_order_quantity',r'最小起订量|minimum order quantity|\bMOQ\b',False))

def make_plan(question,registry):
    if not isinstance(question,str) or not question.strip() or len(question)>200:raise ValueError('bounded query required')
    patterns=list(FIELDS)+list(EXTRA);subjects=[]
    for model,subject in registry.items():
        if re.search(r'(?<![a-z0-9])'+re.escape(model)+r'(?![a-z0-9])',question,re.I):subjects.append(subject)
    subjects=list(dict.fromkeys(subjects)) or ['UNRESOLVED_SUBJECT']
    found={code:unit for code,pattern,unit in patterns if re.search(pattern,question,re.I)}
    unresolved=[]
    if subjects==['UNRESOLVED_SUBJECT']:unresolved.append('subject_not_resolved')
    known={identifier.casefold() for identifier in registry}
    mentioned=re.findall(r'(?<![A-Za-z0-9])[A-Za-z]+\d+[A-Za-z]*(?![A-Za-z0-9])',question)
    for identifier in mentioned:
        if identifier.casefold() not in known:unresolved.append('subject_not_resolved:'+identifier)
    if not found:unresolved.append('requested_fields_not_resolved')
    if re.search(r'合并|补成|沿用|确定范围|唯一准确|自动纠正|同一个值|\bmerge\b|\bfill\b|\binfer\b|same value|guarantee',question,re.I):
        unresolved.append('requested_inference_or_normalization_not_proven_by_raw_quote')
    # Preserve unknown requested conjuncts even when another field matched.
    # This is conservative clause coverage, not a complete language understanding claim.
    parts=re.split(r'以及|并且|同时|另外|和|、|[，,;；]|\band\b|\balso\b|\bplus\b|as well as',question,flags=re.I)
    for i,part in enumerate(parts):
        if len(parts)<2 or not part.strip():continue
        if any(re.search(pattern,part,re.I) for _,pattern,_ in patterns):continue
        stripped=part
        for model in registry:stripped=re.sub(re.escape(model),'',stripped,flags=re.I)
        stripped=re.sub(r'\b(?:what|are|is|the|of|compare|please|both|its|their)\b|比较|分别|的|与|是多少|是什么|[？?\s]+','',stripped,flags=re.I)
        if stripped and len(stripped)>2:unresolved.append('unresolved_clause_'+str(i))
    raw_intent=bool(re.search(r'原样|原文|原始值|怎么写|raw|as printed|verbatim',question,re.I))
    explicit_unit=bool(re.search(r'单位|毫米|MPa|\bmm\b|units?',question,re.I))
    proof=bool(re.search(r'实测|独立|有效(?:证书|认证)|\bmeasured\b|\bprove\b|independent|valid certificate',question,re.I))
    requirements=[]
    for subject in subjects:
        for field,unit in found.items():
            commercial=field in ('price','stock','lead_time','minimum_order_quantity')
            requirements.append(Requirement('R'+str(len(requirements)+1),subject,field,
                unit and (explicit_unit or not raw_intent),proof,commercial))
        for unknown in unresolved:
            requirements.append(Requirement('R'+str(len(requirements)+1),subject,'UNRESOLVED:'+unknown))
    if len(requirements)>20:raise ValueError('scope too complex; explicit server plan required')
    return RequestPlan(question,tuple(requirements)),unresolved

def assess(plan,records,selections,unresolved=()):
    # Local semantics projection: neither send permission nor citation identity proves
    # coverage. Original records are retained separately; projection NEVER authorizes send.
    projected=[dc.replace(r,read_authorized=True,send_authorized=True,canonical=True,
        document_id=r.document_id or 'ASSESSMENT_ONLY',chunk_id=r.chunk_id or 'ASSESSMENT_ONLY') for r in records]
    gate=EvidenceGate(lambda:projected)
    result=gate.preview(plan,selections)
    selected={r.candidate_id:r for r in records if r.candidate_id in selections}
    authority=[]
    for r in selected.values():
        authority.append({'candidate_id':r.candidate_id,'read_authorized':r.read_authorized,
            'canonical_mapped':bool(r.canonical and r.document_id and r.chunk_id),
            'document_id':r.document_id,'version':r.version,'chunk_id':r.chunk_id,
            'send_authorized':r.send_authorized})
    visible=[e for e in result['allowed_evidence'] if any(r.document_id==e['document_id'] and r.chunk_id==e['chunk_id']
        and r.read_authorized and r.canonical for r in selected.values())]
    semantic_decision='source_bounded' if result['coverage']=='supported' else 'partial_abstain' if result['coverage']=='partial' else 'abstain'
    supported={o['requirement_id'] for o in result['outcomes'] if o['state']=='supported'}
    visible_requirements={e['requirement_id'] for e in visible}
    authority_status='usable' if supported and supported<=visible_requirements else 'partial' if visible_requirements else 'blocked'
    actionable='abstain' if authority_status=='blocked' else 'partial_abstain' if authority_status=='partial' else semantic_decision
    return {'evidence_answerability':result['coverage'],'evidence_decision':semantic_decision,
        'answer_decision':actionable,'authorization_status':authority_status,
        'outcomes':result['outcomes'],'unresolved_scope':list(unresolved),'requires_scope_confirmation':True,
        'authorization_canonical':authority,'preview_evidence':visible,
        'send_consent':{'present':False,'generation_allowed':False},'generated_answer':False,
        'cloud_context_added':False,'assessment_context_digest':result['context_digest']}
