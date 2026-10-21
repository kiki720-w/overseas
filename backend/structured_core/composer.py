"""Only server-controlled labels and source-checked normalized slots reach mail."""
import hashlib
import json
from pathlib import Path

POLICY_VERSION = 'controlled-mail-v1'
LABELS = {'gear_fixture':'gear workholding fixtures', 'cnc_fixture':'CNC fixtures',
          'steel':'steel', 'aluminum':'aluminum', 'turning':'turning', 'milling':'milling', 'grinding':'grinding'}
QUESTIONS = {'clarify_process':'Could you confirm the machining process and machine model?','clarify_drawing':'Could you share the drawing for technical review?',
             'clarify_quantity':'Could you confirm the required quantity?',
             'clarify_price':'Could you confirm your target budget?',
             'clarify_lead_time':'Could you confirm your required delivery date?',
             'clarify_certification':'Could you confirm the certification requirement?',
             'clarify_accuracy':'Could you confirm the accuracy requirement and measurement method?'}

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')).hexdigest()

def text_hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()

def policy_digest():
    # Binds the complete local validator/composer/workflow implementation.
    root=Path(__file__).resolve().parent
    return hashlib.sha256(b''.join((root/name).read_bytes() for name in ('contracts.py','composer.py','workflow.py'))).hexdigest()

def compose_clarification(intents):
    """Fixed questions only; accepts no claims, references or model prose.

    This separate unreviewed preview never substitutes for an approved mail.
    The normal composer still blocks every pending commitment.
    """
    if not isinstance(intents,list) or not 1<=len(intents)<=len(QUESTIONS):
        return None
    if any(type(code) is not str or code not in QUESTIONS for code in intents):
        return None
    if len(set(intents))!=len(intents):
        return None
    paragraphs=[QUESTIONS[code] for code in intents]
    return ('Subject: Information for technical review\n\nHello,\n\n'
            +'\n\n'.join(paragraphs)
            +'\n\nBest regards,\nFactory team')

def compose(envelope):
    paragraphs=[]
    internal=[]
    pending=[]
    for claim in envelope.claims:
        if claim.kind=='factory_fact':
            label=LABELS[claim.value.value]
            paragraphs.append({'product':f'Our reviewed catalog lists {label}.',
                               'material':f'The reviewed catalog entry lists {label} as a material.',
                               'process':f'The reviewed catalog entry lists {label} as a process.'}[claim.slot])
        elif claim.kind=='customer_quote':
            value=claim.value
            if value.kind=='category':
                sentence=f'Could you confirm that {LABELS[value.value]} remains part of your requirement?'
            elif value.kind=='measurement':
                unit={'day':'days','mm':'mm'}[value.unit]
                subject={'lead_time':'requested lead time','accuracy':'required accuracy'}[claim.slot]
                sentence=f'Could you confirm that {value.amount} {unit} remains your {subject}?'
            elif value.kind=='money':
                basis={'total':'in total','per_piece':'per piece'}[value.basis]
                sentence=f'Could you confirm your target budget of {value.currency} {value.amount} {basis} for {value.quantity} pieces?'
            elif value.kind=='certification':
                sentence=f'Could you confirm whether {value.standard} is a requirement?'
            else:
                sentence='Could you share the drawing so manufacturing feasibility can be reviewed?'
            paragraphs.append(sentence)
        elif claim.kind=='inference':
            internal.append(claim.code)
        else:
            pending.append(claim.slot)
    paragraphs.extend(QUESTIONS[code] for code in envelope.intents)
    paragraphs=list(dict.fromkeys(paragraphs))
    if pending:
        return None, sorted(set(internal)), sorted(set(pending))
    if not paragraphs:
        return None, sorted(set(internal)), ['no_external_intent']
    body='Subject: Requirements for technical review\n\nHello,\n\n'+'\n\n'.join(paragraphs)+'\n\nBest regards,\nFactory team'
    return body, sorted(set(internal)), []
