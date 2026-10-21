"""Purpose-aware public page URLs. No DNS, fetching, or credential retention."""
import re
from urllib.parse import parse_qsl,quote,unquote,urlsplit,urlunsplit
from .common import AppError

QUERY_KEYS=frozenset(('id','product','product_id','productid','category','category_id','cat','catid','cid','article','article_id','newsid','page','p','lang','language','locale','country','region','q','query','search','s','type','view','format','sort','order','limit','offset'))
SENSITIVE=re.compile(r'(?:token|secret|password|passwd|credential|session|authorization|bearer|signature|api.?key|access.?key|private.?key|oauth|^auth$|^code$|^sid$|^sig$|^jwt$|x.amz|x.goog)',re.I)
ROUTING_KEYS=frozenset(('url','uri','redirect','redirect_uri','redirect_url','return','returnto','return_url','next','callback','endpoint','target','destination','dest','host','proxy','continue'))
REASONS=frozenset(('page_url_malformed','page_url_encoding_invalid','page_url_authority_rejected','page_url_sensitive_query','page_url_query_not_supported','page_url_query_embedded_url','page_url_nested_encoding','page_url_hash_route_not_fetchable','page_url_sensitive_fragment'))

def fail(reason):
    assert reason in REASONS
    error=AppError('该网址不符合公开网页策略；请核对原始来源，不会自动改写敏感参数或访问',400,'website_url_invalid')
    error.url_policy_reason=reason;error.url_validation_stage='page_url_policy'
    raise error

def decoded(value):
    if re.search(r'%(?![0-9a-fA-F]{2})',value):fail('page_url_encoding_invalid')
    try:result=unquote(value,encoding='utf-8',errors='strict')
    except UnicodeError:fail('page_url_encoding_invalid')
    if any(ord(c)<32 or ord(c)==127 for c in result) or '\\' in result:fail('page_url_encoding_invalid')
    if re.search(r'%[0-9a-fA-F]{2}',result):fail('page_url_nested_encoding')
    return result

def check_query(query):
    if not query:return
    decoded(query)
    try:fields=parse_qsl(query,keep_blank_values=True,encoding='utf-8',errors='strict',max_num_fields=16)
    except (ValueError,UnicodeError):fail('page_url_encoding_invalid')
    if not fields:fail('page_url_query_not_supported')
    for key,value in fields:
        if not key or len(key)>40 or len(value)>160:fail('page_url_query_not_supported')
        if SENSITIVE.search(key) or re.search(r'(?:token|password|api.?key|authorization|secret)\s*[:=]',value,re.I):fail('page_url_sensitive_query')
        if key.casefold() in ROUTING_KEYS or re.search(r'(?:https?|file|ftp|gopher|data|javascript):|^\s*//',value,re.I):fail('page_url_query_embedded_url')
        if re.fullmatch(r'[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}',value):fail('page_url_sensitive_query')
        if key.casefold() not in QUERY_KEYS:fail('page_url_query_not_supported')

def inspect_page_url(value):
    """Retain observed query/fragment semantics; hash routes remain display-only."""
    if not isinstance(value,str) or not 1<=len(value)<=500 or any(ord(c)<33 or ord(c)==127 for c in value) or '\\' in value:fail('page_url_malformed')
    try:u=urlsplit(value)
    except ValueError:fail('page_url_malformed')
    path=u.path or '/';plain=decoded(path)
    if re.search(r'%2f|%5c',path,re.I) or any(part in ('.','..') for part in plain.split('/')):fail('page_url_encoding_invalid')
    try:path=quote(path,safe="/%:@-._~!$&'()*+,;=")
    except UnicodeError:fail('page_url_encoding_invalid')
    # Reuse the existing strict authority/protocol/port/IP/path gate unchanged.
    from .website_fetch import normalize_url
    try:base=normalize_url(urlunsplit((u.scheme,u.netloc,path,'','')))
    except AppError as error:
        if u.query or u.fragment:error.url_policy_reason='page_url_authority_rejected'
        error.url_validation_stage='page_url_policy';raise
    check_query(u.query)
    fragment=decoded(u.fragment) if u.fragment else ''
    if re.search(r'(?:https?|file|ftp|gopher|data|javascript):|^\s*//',fragment,re.I):fail('page_url_sensitive_fragment')
    if re.fullmatch(r'[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}',fragment):fail('page_url_sensitive_fragment')
    if SENSITIVE.search(fragment.partition('=')[0]) and '=' in fragment:fail('page_url_sensitive_fragment')
    if re.search(r'(?:token|password|api.?key|authorization|secret)\s*[:=]',fragment,re.I):fail('page_url_sensitive_fragment')
    if '?' in fragment:check_query(fragment.partition('?')[2])
    ordinary=bool(fragment and re.fullmatch(r'[\w.:-]{1,100}',fragment,flags=re.UNICODE))
    fragment_kind='none' if not fragment else 'document_anchor' if ordinary else 'client_route_or_ambiguous'
    b=urlsplit(base)
    # Never sort, drop, decode/re-encode or reinterpret query keys and values.
    try:query=quote(u.query,safe="%&=+;,:/?@-._~!$'()*");encoded_fragment=quote(u.fragment,safe="%/:?&=!-._~")
    except UnicodeError:fail('page_url_encoding_invalid')
    document=urlunsplit((b.scheme,b.netloc,b.path,query,''))
    display=urlunsplit((b.scheme,b.netloc,b.path,query,encoded_fragment))
    fetch_url=None if fragment_kind=='client_route_or_ambiguous' else document
    return {'display_url':display,'document_url':display if fetch_url is None else document,'fetch_url':fetch_url,
            'fragment_kind':fragment_kind,'ordinary_anchor_removed_for_fetch':ordinary,'query_preserved':bool(query),
            'fragment_interpretation':'syntactic_only_not_dom_verified','client_view_verified':False,
            'static_validation_only':True,'dns_validated':False,'request_performed':False}

def normalize_page_url(value):
    policy=inspect_page_url(value)
    if policy['fetch_url'] is None:fail('page_url_hash_route_not_fetchable')
    return policy['fetch_url']
