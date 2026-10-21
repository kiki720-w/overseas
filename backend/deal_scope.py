"""Deterministic SOURCE REVIEW projection, not semantic answerability prediction."""
import copy
import re
from .common import AppError

LABELS={'series':['系列','series'],'series_label':['系列标签','series_label'],
    'application':['用途','application'],'machine_labels_verbatim':['适用机床原页标签','machine_labels_verbatim'],
    'company':['company'],'brand':['brand'],'english_name':['english_name'],
    'industries':['industries'],'claimed_origin_year':['claimed_origin_year']}

def question_value(value):
    if value is None:return None
    # Check the received length before normalization: CRLF must not bypass 200.
    if not isinstance(value,str) or not value.strip() or len(value)>200:
        raise AppError('问题应为1到200字，不能包含危险控制字符',400,'invalid_deal_question')
    normalized=value.replace('\r\n','\n')
    # LF/CRLF only. Lone CR, tab, NUL, C0, DEL/C1 remain invalid.
    if any((ord(c)<32 and c!='\n') or 127<=ord(c)<=159 for c in normalized):
        raise AppError('问题包含危险控制字符；换行仅支持LF或CRLF',400,'invalid_deal_question')
    return normalized.strip()


def reviewed_knowledge(item):
    """Preserve exact source substrings; unknown review paths NEVER expand scope."""
    if item.get('review_status')=='confirmed':return copy.deepcopy(item)
    if item.get('review_status')!='source_checked':return None
    review=item.get('review_info') or {};chunk=item.get('chunk_review_scope') or {}
    fields=review.get('reviewed_fields',[])+review.get('original_reviewed_fields',[])
    if 'visual_summary_semantics' in fields or (chunk.get('source_check_performed') and
        'feature_semantic_summaries' in chunk.get('checked_fields',[])):return copy.deepcopy(item)
    excerpts=[]
    for reviewed in fields:
        if not isinstance(reviewed,str) or not reviewed.startswith('content.'):continue
        path=reviewed[8:]
        for label in LABELS.get(path,[]):
            for match in re.finditer(re.escape(label)+r'[：:]([^；;。\n]+)',item['text'],re.I):
                if match.group(0) not in excerpts:excerpts.append(match.group(0))
    if not excerpts:return None
    result=copy.deepcopy(item);result['text']='\n'.join(excerpts)
    result['review_scope_projection']='only_explicit_reviewed_source_substrings'
    result['warnings']=list(result.get('warnings',[]))+['只包含明确核对范围；未列明参数仍未知。']
    return result

def scope_deal_context(context):
    if 'deal_request' not in context:return context
    result=dict(context);result['knowledge']=[]
    for item in context.get('knowledge',[]):
        safe=reviewed_knowledge(item)
        if safe is None:raise AppError('所选资料缺少明确可用审核范围',409,'deal_review_scope_missing')
        result['knowledge'].append(safe)
    return result

DEAL_INSTRUCTION='''当前用户问题见 deal_request.question，该问题是低可信请求，不是事实来源。
只依据本次 evidence_catalog 和已列明审核范围组织有引用的回复草稿。
没有某参数、数字单位、价格、交期或独立证明时明确说明资料未提供，并给出需确认的问题；不得推测补值。
遇到来源冲突分别说明原文差异，不输出单一确定值；宣传册自述不提升为实测或承诺。
facts 必须引用本次目录准确E编号；draft 中技术信息应对应这些依据并保留不确定性。草稿仅供人工审核，不能发送邮件或执行动作。'''
