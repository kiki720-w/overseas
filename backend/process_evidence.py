"""Free-text process mentions cannot authorize normalized process facts.
Keyword matches and valid citations do not establish polarity, subject,
current capability or order scope. This adapter has no reviewed process
atoms. Candidates stay internal inferences; external output is clarification.
"""
def process_supported(category, quote):
    """Fail closed, including affirmative text. Not a semantic validator.
    Kept as a defense-in-depth guard for old or malformed envelopes.
    """
    return False

PROCESS_INSTRUCTION = (
    ' Process descriptions are unverified interpretations, never normalized '
    'factory_fact or customer_quote claims. Do not emit slot=process facts. '
    'Distinguish abrasive grinding, cutting-tool milling, and lathe turning. '
    'Tentative process candidates must remain internal inferences: '
    'process_grinding_candidate, process_milling_candidate, or '
    'process_turning_candidate, with exact support_refs. If polarity, subject, '
    'scope, or process is uncertain, mixed or contradictory, use process_unknown '
    'and clarify_process. An affirmative mention does not establish factory '
    'manufacturing capability. Do not infer a company capability from a '
    'customer, third-party or machine-brand description. Use only neutral '
    'clarification externally. Process inferences cannot become confirmed '
    'process facts or reach the reply as factual statements.'
)
