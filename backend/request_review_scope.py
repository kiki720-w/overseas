"""Visible incomplete request checks, not general semantic question parsing."""
import copy,re

REASON='尚未完成核对；空的缺失字段列表不表示问题已覆盖。请对照原问题和来源人工确认。'

def segments(question):
    # Keep every original character and offsets. Conjunctions are ambiguous:
    # do not split "terms and conditions" or infer missing subjects/fields.
    cuts={0,len(question)}
    for m in re.finditer(r'[?？!！;；。\n]+',question):cuts.add(m.end())
    for m in re.finditer(r'(?<!\S)(?:\d{1,2}[.)、]|[（(]\d{1,2}[）)])(?=\s|[^\W\d_])',question):cuts.add(m.start())
    bounds=sorted(cuts);parts=[]
    for a,b in zip(bounds,bounds[1:]):
        if not question[a:b].strip():
            if parts:parts[-1]['end']=b;parts[-1]['text_verbatim']=question[parts[-1]['start']:b]
            continue
        parts.append(dict(start=a,end=b,text_verbatim=question[a:b]))
    # Leading whitespace belongs to the first question; never lose original text.
    if parts and parts[0]['start']>0:parts[0]['start']=0;parts[0]['text_verbatim']=question[:parts[0]['end']]
    return parts or [dict(start=0,end=len(question),text_verbatim=question)]

def attach(context,coverage,assess_single):
    out=copy.deepcopy(coverage);question=out.get('question_verbatim','');checks=[];pending=[]
    for n,part in enumerate(segments(question),1):
        text=part['text_verbatim'];sub=copy.deepcopy(context)
        sub['_request_review_scope_internal']=True;sub['deal_request']={'question':text.strip()}
        c=assess_single(sub);known=copy.deepcopy(c.get('supported_extracts',[]));missing=c.get('missing',[])
        # Local partial finite checks cannot silently certify arbitrary remainder.
        raw_unchecked=bool(c.get('unparsed_question_scope')) or c.get('status')=='not_assessed'
        status='partially_checked' if known and (raw_unchecked or missing) else 'not_checked' if raw_unchecked else 'awaiting_confirmation' if missing else 'source_tuples_only'
        unresolved=[]
        if raw_unchecked:unresolved.append(dict(text_verbatim=text,reason=REASON,field=None,state='unparsed_or_unassessed',field_inferred=False))
        for m in missing:
            unresolved.append(dict(text_verbatim=text,reason=m['reason'],field=m.get('field'),model=m.get('model'),state=m['state'],field_inferred=False))
        item=dict(question_id='Q%03d'%n,ordinal=n,text_verbatim=text,span=dict(start=part['start'],end=part['end']),check_status=status,
                  recognized_requirements=copy.deepcopy(c.get('outcomes',[])),supported_transcriptions=[dict(e,validation_scope='anchored_tuple_only') for e in known],
                  unresolved_parts=unresolved,complete_answer_validated=False)
        checks.append(item)
        if unresolved:
            pending.append(dict({k:item[k] for k in ('question_id','ordinal','text_verbatim','check_status')},reason=REASON))
    out.update(question_checks=checks,unresolved_questions=pending,request_check_status='incomplete' if pending else 'limited_source_checks_only',
               coverage_complete=False,missing_scope='recognized_fields_only',question_split_scope='syntactic_boundaries_only',review_unit_label='待核对原文片段',semantic_question_count=None)
    return out

def visible_review(coverage,question):
    # Defense for internal/older coverage objects: no empty missing => all covered.
    checks=copy.deepcopy(coverage.get('question_checks',[]));pending=copy.deepcopy(coverage.get('unresolved_questions',[]))
    if not checks:
        checks=[dict(question_id='Q001',ordinal=1,text_verbatim=question,span=dict(start=0,end=len(question)),check_status='not_checked',
                     recognized_requirements=[],supported_transcriptions=[],unresolved_parts=[dict(text_verbatim=question,reason=REASON,field=None,state='unassessed',field_inferred=False)],complete_answer_validated=False)]
        pending=[dict(question_id='Q001',ordinal=1,text_verbatim=question,check_status='not_checked',reason=REASON)]
    return dict(question_checks=checks,unresolved_questions=pending,request_check_status='incomplete' if pending else 'limited_source_checks_only',
                coverage_complete=False,missing_scope='recognized_fields_only',question_split_scope='syntactic_boundaries_only',review_unit_label='待核对原文片段',semantic_question_count=None)
