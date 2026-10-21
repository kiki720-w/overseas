"""Strict local worker protocol; no extraction or source authority of its own."""
import re
from .common import AppError

def invalid():
    raise AppError('Invalid optional worker reply',502,'optional_worker_output_invalid')

def validate_links(value):
    from .webpage_url import normalize_page_url
    fields={'extractor','redirect_checks','requests','candidate_limit','candidates','rejected_reason_counts','notice'}
    if not isinstance(value,dict) or set(value)!=fields:invalid()
    if value['extractor']!='courlan' or value['redirect_checks'] is not False or type(value['requests']) is not int or value['requests']!=0 or type(value['candidate_limit']) is not int or value['candidate_limit']!=12:invalid()
    if not isinstance(value['notice'],str) or not value['notice']:invalid()
    counts=value['rejected_reason_counts']
    if not isinstance(counts,dict) or not all(isinstance(k,str) and type(v) is int and v>=0 for k,v in counts.items()):invalid()
    items=value['candidates']
    if not isinstance(items,list) or len(items)>12:invalid()
    required={'url','label','page_role','relation','source_url','source_xpath','source_html_sha256','source_is_directory','official_site_verified','entity_identity_verified','request_performed'}
    seen=set()
    for item in items:
        if not isinstance(item,dict) or set(item)!=required:invalid()
        if not all(isinstance(item[k],str) for k in ('url','label','source_url','source_xpath','source_html_sha256')):invalid()
        if not item['source_xpath'] or len(item['label'])>160 or not re.fullmatch('[0-9a-f]{64}',item['source_html_sha256']):invalid()
        if item['page_role'] not in ('capabilities','products','about','external_site_candidate') or item['relation'] not in ('same_host_page','external_reference'):invalid()
        if type(item['source_is_directory']) is not bool or any(item[k] is not False for k in ('official_site_verified','entity_identity_verified','request_performed')):invalid()
        try:
            if normalize_page_url(item['url'])!=item['url'] or normalize_page_url(item['source_url'])!=item['source_url']:invalid()
        except AppError:invalid()
        if item['url'] in seen:invalid()
        seen.add(item['url'])

def validate(result):
    if not isinstance(result,dict):invalid()
    if 'error' in result:
        if set(result)-{'error','status','code','_worker_boundary'} or not isinstance(result['error'],str) or type(result.get('status')) is not int or result['status'] not in (413,502,503) or result.get('code') not in ('optional_runtime_incompatible','optional_dependency_missing','optional_input_limit','optional_worker_output_invalid','optional_worker_failed','optional_worker_output_limit'):invalid()
        raise AppError(result['error'],result['status'],result['code'])
    if set(result)-{'result','_worker_boundary'}:invalid()
    o=result.get('result')
    fields={'feature_enabled','primary_status','primary_extractor','primary_version','primary_internal_fallback','status','fallback_count','fallback_limit','total_quote_limit','quote_character_limit','candidate_paragraphs','unquoted_candidate_count','native_method','segments','company_identity_verified','actual_compatibility','purchase_intent','scope_notice'}
    fields.add('link_discovery')
    if not isinstance(o,dict) or set(o)!=fields:invalid()
    validate_links(o['link_discovery'])
    if o['feature_enabled'] is not True or o['company_identity_verified'] is not False or o['actual_compatibility']!='unknown' or o['purchase_intent']!='unknown' or o['primary_extractor']!='trafilatura':invalid()
    if o['primary_status'] not in ('dependency_missing','extractor_error','body_missing','extracted_text_requires_source_check','input_too_large'):invalid()
    for key in ('primary_version','primary_internal_fallback'):
        if o[key] is not None and not isinstance(o[key],str):invalid()
    for key in ('native_method','scope_notice'):
        if not isinstance(o[key],str) or not o[key]:invalid()
    for key in ('fallback_count','fallback_limit','total_quote_limit','quote_character_limit','candidate_paragraphs','unquoted_candidate_count'):
        if type(o[key]) is not int or o[key]<0:invalid()
    if (o['fallback_limit'],o['total_quote_limit'],o['quote_character_limit'])!=(3,8,300):invalid()
    ss=o['segments']
    if not isinstance(ss,list) or len(ss)>8:invalid()
    required={'text','quote','heading','selection_features','position','responsive_visibility_unverified','extractor','extractor_version','native_parser_version','source_url','fetched_at','source_html_sha256','fallback','fallback_reason','confidence','source_alignment','identity_verified','actual_compatibility','purchase_intent'}
    for s in ss:
        if not isinstance(s,dict) or set(s)!=required:invalid()
        for key in ('text','quote','extractor_version','native_parser_version','source_url','fetched_at','source_html_sha256'):
            if not isinstance(s[key],str) or not s[key]:invalid()
        if not all(re.fullmatch(r'[0-9a-f]{64}',s[key]) for key in ('native_parser_version','source_html_sha256')):invalid()
        if type(s['fallback']) is not bool or type(s['responsive_visibility_unverified']) is not bool or s['identity_verified'] is not False or s['actual_compatibility']!='unknown' or s['purchase_intent']!='unknown' or s['source_alignment']!='exact_normalized_native_visible_paragraph':invalid()
        if s['heading'] is not None and not isinstance(s['heading'],str):invalid()
        expected=('structured_product_fallback','low_trust_structured_product_requires_human_review') if s['fallback'] else ('trafilatura','source_aligned_website_self_statement_unverified')
        if (s['extractor'],s['confidence'])!=expected:invalid()
        if s['fallback']:
            if not re.fullmatch(r'[0-9a-f]{64}',s['extractor_version']):invalid()
        elif s['extractor_version']!=o['primary_version']:invalid()
        if s['fallback']:
            if s['fallback_reason'] not in ('paragraph_missing_in_primary','primary_missing_or_error'):invalid()
        elif s['fallback_reason'] is not None:invalid()
        p=s['position'];f=s['selection_features']
        if not isinstance(p,dict) or set(p)!={'xpath','html_line','tag'} or not isinstance(p['xpath'],str) or not p['xpath'] or p['tag'] not in ('p','td') or type(p['html_line']) is not int or p['html_line']<1:invalid()
        ff={'paragraph_tag','normalized_characters','word_count','assertion_match','product_terms','preceding_product_heading','anchor_character_ratio','native_paragraph_alignment'}
        if not isinstance(f,dict) or set(f)!=ff:invalid()
        if f['paragraph_tag']!=p['tag'] or type(f['normalized_characters']) is not int or f['normalized_characters']!=len(s['text']) or type(f['word_count']) is not int or f['word_count']!=len(s['text'].split()) or not isinstance(f['assertion_match'],str) or not f['assertion_match'] or f['native_paragraph_alignment'] is not True:invalid()
        if not isinstance(f['product_terms'],list) or not all(isinstance(x,str) for x in f['product_terms']) or f['preceding_product_heading']!=s['heading'] or type(f['anchor_character_ratio']) not in (int,float) or not 0<=f['anchor_character_ratio']<=1:invalid()
    count=sum(s['fallback'] for s in ss)
    if count>3 or o['fallback_count']!=count or o['candidate_paragraphs']<len(ss) or o['unquoted_candidate_count']!=o['candidate_paragraphs']-len(ss):invalid()
    status='body_insufficient' if not ss else 'structured_product_fallback_only_low_trust' if count==len(ss) else 'primary_with_limited_structured_fallback' if count else 'source_aligned_primary'
    if o['status']!=status or len({s['quote'] for s in ss})!=len(ss):invalid()
    return o
