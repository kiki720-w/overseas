"""Outbound knowledge projection. Internal lineage/audit data remains local."""
import re
from .knowledge import REVIEW_LABELS

def text(value,limit):
    return value if isinstance(value,str) and len(value)<=limit else ''

def pages(value):
    return sorted({p for p in value if type(p) is int and 1<=p<=10000})[:80] if isinstance(value,list) else []

def fields(value):
    return list(dict.fromkeys(v.strip() for v in value if isinstance(v,str) and 0<len(v.strip())<=100))[:30] if isinstance(value,list) else []

def location(value):
    value=value if isinstance(value,dict) else {};result={}
    kind=value.get('kind')
    if kind in ('paragraph','page','source_semantic_summary'):result['kind']=kind
    for key in ['page','paragraph','chunk_index']:
        v=value.get(key)
        if type(v) is int and 1<=v<=10000:result[key]=v
    if value.get('pdf_pages'):result['pdf_pages']=pages(value['pdf_pages'])
    occurrences=[]
    if isinstance(value.get('source_occurrences'),list):
        for item in value['source_occurrences'][:80]:
            if not isinstance(item,dict):continue
            page=item.get('pdf_page')
            if type(page) is not int or not 1<=page<=10000:continue
            occurrence={'pdf_page':page};printed=item.get('printed_page')
            if type(printed) is int and printed>0:printed=str(printed)
            printed=text(printed,100)
            if printed:occurrence['printed_page']=printed
            occurrences.append(occurrence)
    if occurrences:result['source_occurrences']=occurrences
    if value.get('quote_scope')=='this_new_summary_text_only_not_original_pdf_verbatim':result['quote_scope']=value['quote_scope']
    return result

def review(value,status):
    if not isinstance(value,dict):return None
    result={'reviewed_fields':fields(value.get('reviewed_fields')),'reason':text(value.get('reason'),2000),
            'scope_notice':text(value.get('scope_notice'),2000),'evidence_level':'publisher_self_statement','independently_proven':False}
    stamp=text(value.get('reviewed_at'),64)
    if stamp:result['reviewed_at']=stamp
    source=value.get('source')
    if isinstance(source,dict):
        safe={'filename':text(source.get('filename'),200),'pdf_pages':pages(source.get('pdf_pages')),
              'printed_pages':[p for p in source.get('printed_pages',[])[:80] if isinstance(p,str) and len(p)<=100] if isinstance(source.get('printed_pages'),list) else []}
        sha=source.get('sha256')
        if isinstance(sha,str) and re.fullmatch('[a-f0-9]{64}',sha):safe['sha256']=sha
        result['source']=safe
    carried=value.get('carried_original_field_review')
    if isinstance(carried,dict):
        # Explicit, small scope summary; no recursive audit/history JSON.
        result['original_reviewed_fields']=fields(carried.get('reviewed_fields'))
        result['inherited_original_scope']=True
    elif 'preserved_original_scope' in result['reviewed_fields']:result['inherited_original_scope']=True
    if value.get('migration_proposal_only') is True:result['approval_pending']=True
    if status=='source_checked' and not result['reviewed_fields']:result['scope_notice']='核对字段范围未明确；不能将整段摘要视为已核实事实。'
    return result

def project_knowledge(snippet):
    # These required identity/body fields come from server-owned knowledge_selection.
    result={key:snippet[key] for key in ('text','document_id','version','chunk_id','title','filename','source_company','review_status')}
    result['location']=location(snippet.get('location'))
    result['review_label']=REVIEW_LABELS.get(result['review_status'],'待核实')
    result['review_info']=review(snippet.get('review_info'),result['review_status'])
    result['independently_proven']=False
    result['category']=snippet.get('category','')
    result['revision']=snippet.get('revision',0)
    result['expired']=snippet.get('expired') is True;result['conflict']=snippet.get('conflict') is True
    result['text_role']='derived_summary' if result['location'].get('kind')=='source_semantic_summary' else 'selected_material_text'
    metadata=snippet.get('metadata');field_review=metadata.get('field_review') if isinstance(metadata,dict) else None
    if isinstance(field_review,dict):
        scope={'source_check_performed':field_review.get('visual_source_checked') is True,
               'checked_fields':fields(field_review.get('checked_fields')),'new_fields_inherit_confirmation':False,
               'evidence_level':'publisher_self_statement','independently_proven':False}
        status=field_review.get('document_review_status_inherited_only')
        if status in REVIEW_LABELS:scope['inherited_document_status']=status
        result['chunk_review_scope']=scope
    warnings=['资料只证明所选文本写了什么，不证明客户需求、性能、证书或权属。']
    if result['review_status']=='source_checked':warnings.append('仅列出的 reviewed_fields 已核对原文；未覆盖字段仍未知。')
    if result['review_status']=='confirmed':warnings.append('用户确认不等于独立有效证明。')
    if result['review_status']=='unverified':warnings.append('资料尚未核实。')
    if result['expired']:warnings.append('资料已过期。')
    if result['conflict']:warnings.append('资料存在同名冲突，请人工核对。')
    if result['text_role']=='derived_summary':warnings.append('这是派生摘要；引用仅指当前摘要文本，不是原 PDF 逐字引用。')
    if 'chunk_review_scope' in result:warnings.append('本片段新增字段不继承原文用户确认；原文核对仅限所列字段，不是独立证明。')
    if result['review_info'] and result['review_info'].get('approval_pending'):warnings.append('迁移摘要仍待批准；不得把继承的原文状态当作新摘要已获批准。')
    result['warnings']=warnings
    return result
