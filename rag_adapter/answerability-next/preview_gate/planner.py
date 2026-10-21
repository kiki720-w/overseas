"""Local bilingual scope suggestions, NOT automatic proof of complete intent."""
import re
from dataclasses import dataclass
from typing import Mapping, Tuple
from .gate import Requirement,RequestPlan

# Field codes are a server schema, not model-invented JSON attributes.
FIELDS=(
    ('overall_clamping_range',r'总体夹持范围|总夹持范围|overall (?:clamping|gripping) range',True),
    ('slot_range',r'槽范围|slot range',True),
    ('pressure',r'压力|\bpressure\b',True),
    ('rated_torque',r'额定扭矩|rated torque',True),
    ('speed',r'转速|\bspeed\b|\brpm\b',True),
    ('accuracy',r'精度|\baccuracy\b|\btolerance\b',True),
    ('roughness',r'粗糙度|\broughness\b',True),
    ('material',r'材料|\bmaterial\b',False),
    ('clamping_method',r'夹紧方式|夹持方式|clamping (?:method|mechanism)',False),
    ('price',r'价格|多少钱|\bprice\b|\bcost\b',True),
    ('stock',r'库存|\bstock\b|inventory',False),
    ('lead_time',r'交期|lead time|delivery date',False),
    ('certification',r'认证|证书|certification|certificate',False),
    ('transaction_amount',r'成交金额|transaction amount|sales amount',True),
)

@dataclass(frozen=True)
class PlanSuggestion:
    plan: RequestPlan
    scope_confirmed: bool = False
    requires_human_scope_review: bool = True
    unresolved: Tuple[str,...] = ()

def suggest_scope(question,subject_registry:Mapping[str,str],default_subject=None):
    """Registry maps exact local model names to company-qualified subjects.

    Natural-language suggestions never confirm that every sub-question is covered.
    Confirmation belongs to the SERVER consent receipt after a visible scope preview.
    """
    if not isinstance(question,str) or not question.strip() or len(question)>200:raise ValueError('bounded question required')
    q=question.casefold();subjects=[]
    for identifier,qualified in subject_registry.items():
        if re.search(r'(?<![a-z0-9])'+re.escape(identifier.casefold())+r'(?![a-z0-9])',q):subjects.append(qualified)
    subjects=list(dict.fromkeys(subjects));unresolved=[]
    if not subjects:
        if default_subject:subjects=[default_subject]
        else:subjects=['UNRESOLVED_SUBJECT'];unresolved.append('subject_not_resolved')
    found=[(field,unit) for field,pattern,unit in FIELDS if re.search(pattern,q)]
    if not found:found=[('UNRESOLVED_FIELD',False)];unresolved.append('requested_fields_not_resolved')
    quote_intent=bool(re.search(r'宣传册|原文|as printed|brochure|publisher wording',q))
    proof_intent=bool(re.search(r'证明|独立验证|\bprove\b|\bproof\b|independent(?:ly)? verif',q))
    measured=bool(re.search(r'实测|\bmeasured\b',q))
    current=bool(re.search(r'今天|当前|现在|\btoday\b|\bcurrent\b|\bnow\b',q))
    requirements=[]
    for subject in subjects:
        for code,needs_unit in found:
            independent=proof_intent or (measured and not quote_intent)
            if code=='certification' and re.search(r'有效|\bvalid\b',q):independent=True
            commercial=code in ('price','stock','lead_time')
            commitment=bool(re.search(r'保证|承诺|guarantee|guaranteed',q))
            live=(commercial and (current or commitment or not quote_intent)) or (current and code=='certification')
            requirements.append(Requirement('R'+str(len(requirements)+1).zfill(2),subject,code,needs_unit,independent,live))
    return PlanSuggestion(RequestPlan(question,tuple(requirements)),unresolved=tuple(unresolved))
