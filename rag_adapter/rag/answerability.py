"""Narrow evidence requirements, separate from ranking logits; no generation."""
import re
from .core import normalized

def assess_evidence_requirements(query,items):
    """Factory-document scope checks, not a general learned answerability detector.

    No gold IDs, expected labels or fixture concepts are consulted. A high rank is
    insufficient for live commercial values, measured proof or absent typed fields.
    Partial items remain available for human preview; missing values stay unknown.
    """
    q=normalized(query);texts=[normalized(i.get('text','')) for i in items]
    missing=[]
    if not items:missing.append('no_retrieved_evidence')
    if re.search(r'多少钱|报价金额|现在.{0,5}库存|当前.{0,5}库存|现货价格|库存.{0,8}交期|当前.{0,5}交期',q):
        missing.append('current_commercial_record_required')
    if re.search(r'(?:今天|当前|现在|保证|每件).{0,20}(?:价格|交期|起订量|库存)',q):
        missing.append('current_commercial_record_required')
    if re.search(r'证明|实测|独立验证',q) and re.search(r'成交|金额|精度|认证|证书|专利',q):
        if not any(i.get('independently_proven') is True or i.get('field_review',{}).get('independently_proven') is True for i in items):
            missing.append('independent_evidence_required')
    required=[]
    if '额定扭矩' in q:required.append(('rated_torque',r'额定扭矩',r'(?:n[·.]?m|牛米)'))
    if '最高允许转速' in q or '最大允许转速' in q:
        required.append(('maximum_permitted_speed',r'(?:最高允许转速|最大允许转速|最大转速)',r'(?:rpm|r/min|转/分)'))
    if '压力' in q and re.search(r'多少|最大|允许|额定',q):
        required.append(('pressure',r'(?:液压)?压力',r'(?:mpa|bar|psi|帕)'))
    for name,label,unit in required:
        pattern=re.compile(label+r'[^。；;\n]{0,20}?\d+(?:\.\d+)?\s*'+unit)
        if not any(pattern.search(text) for text in texts):missing.append('explicit_'+name+'_value_and_unit_required')
    if '总体夹持范围' in q:
        overall_found=False
        for item in items:
            cells=item.get('_model_cells',[])
            for cell in cells:
                header=normalized(str(cell.get('header_as_previously_transcribed','')))
                if '夹持范围' in header and '槽' not in header and cell.get('value_verbatim') not in (None,'') and not cell.get('missing_kind'):
                    overall_found=True
        if not overall_found:missing.append('overall_clamping_range_missing_do_not_fill_from_slot_range')
    unit_gaps=[]
    if '单位' in q:
        for item in items:
            unit_gaps.extend(cell.get('header_as_previously_transcribed','unknown_field') for cell in item.get('_model_cells',[])
                if cell.get('unit') is None and cell.get('value_verbatim') not in (None,''))
    return {'status':'evidence_missing' if missing else 'partial_evidence' if unit_gaps else 'candidate_support_only',
        'missing_requirements':sorted(set(missing)),'unknown_unit_fields':sorted(set(unit_gaps)),
        # Passing these narrow checks does not establish full question entailment.
        'checked_requirements_passed':not missing and not unit_gaps,
        'can_assert_complete_answer':False,'requires_answer_span_validation':True,
        'ranking_score_is_probability':False,'requires_human_preview':True,
        'notice':'仅窄范围规则，独立holdout仍待验证。未知单位、空值和商业信息不补全；厂商自述不是独立证明。'}
