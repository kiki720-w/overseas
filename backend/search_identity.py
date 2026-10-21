"""Local-only CRM identity filtering. No CRM content is sent to the search provider."""
import json
import sqlite3
import re
from urllib.parse import urlsplit,urlunsplit
from .common import norm

AGGREGATORS={'top.chinaz.com','wlw.be','wlw.de','wlw.at','wlw.ch','dasoertliche.de','crustdata.com','directory.imts.com','techpilot.com','companyhouse.de','rocketreach.co','leadiq.com','linkedin.com','zoominfo.com','crunchbase.com','dnb.com','alibaba.com','globalspec.com','thomasnet.com','kompass.com','made-in-china.com','facebook.com','tianyancha.com','shuidi.cn','patsnap.com','qcc.com','qixin.com','aiqicha.baidu.com','cement365.com'}

def aggregate(domain):
    return any(domain==site or domain.endswith('.'+site) for site in AGGREGATORS)

def reference_url(url):
    domain=host(url);path=urlsplit(url).path.casefold()
    # Publisher routes identify third-party entries, never the named company's own site.
    platform_routes={'pocketautoparts.com':r'^/company/',
                     'mmsonline.com.cn':r'^/company/',
                     'cgma.net.cn':r'^/hytd_detail/',
                     'ejxcn.com':r'^/selldetail/'}
    return (aggregate(domain) or domain=='bloomberg.com' and path.startswith('/profile/company/')
            or any((domain==site or domain.endswith('.'+site)) and re.search(route,path)
                   for site,route in platform_routes.items()))

def source_identity(url,title):
    from .webpage_url import inspect_page_url
    policy=inspect_page_url(url);u=urlsplit(policy['document_url']);domain=host(url)
    # Only default-port spelling is normalized; unknown paths and query parameters remain distinct.
    authority=domain if u.port in (None,80 if u.scheme=='http' else 443) else u.netloc
    canonical=urlunsplit((u.scheme.lower(),authority,u.path or '/',u.query,u.fragment))
    claimed=title.split('|',1)[0].strip()
    return {'source_url':url,'source_path':u.path or '/','claimed_entity_name':claimed,
            'entity_verified':False,'aggregate_source':reference_url(url),
            'deduplication_scope':'same_source_page_and_title','key':canonical+'|'+norm(title)}

def host(url):
    try:
        value=(urlsplit(url).hostname or '').encode('idna').decode('ascii').lower().rstrip('.')
        return value[4:] if value.startswith('www.') else value
    except (ValueError,UnicodeError):return ''

def distinctive_name(name):
    value=norm(name)
    if re.search(r'(有限公司|有限责任公司|股份公司|机械厂|加工厂|\b(?:inc|ltd|llc|gmbh|corp|limited|corporation)\b)',value):return len(value)>=6
    generic={'cnc','gear','gears','fixture','fixtures','workholding','machining','precision','machine','shop','manufacturer','manufacturing','company','official','website','factory','products','industrial','equipment'}
    words=re.findall('[a-z0-9]+',value)
    return len(value)>=8 and len(words)>=2 and any(w not in generic for w in words)

def index(store):
    # Production ClosedStore.db() yields a connection through a context manager.
    # The legacy sqlite connection also implements this protocol.
    context=store.db()
    try:
        with context as db:
            rows=[]
            for (raw,) in db.execute('SELECT data FROM customers'):
                customer=json.loads(raw)
                rows.append({'id':customer['id'],'name':customer['name'],'name_key':norm(customer['name']),'domain':host(customer.get('website','')),'distinctive':distinctive_name(customer['name']),'website':customer.get('website','')})
            return rows
    finally:
        # Preserve explicit closure for the legacy raw-connection Store.
        if isinstance(context,sqlite3.Connection):context.close()

def match(rows,domain,title,url=''):
    key=norm(title)
    for row in rows:
        if row['domain'] and row['domain']==domain and not (aggregate(domain) or reference_url(url)):return row,'exact_domain',[]
    for row in rows:
        if row['distinctive'] and row['name_key']==key:return row,'exact_full_title',[]
        if (aggregate(domain) or reference_url(url)) and row['distinctive'] and row['name_key']==norm(title.split('|',1)[0]):return row,'exact_entity_name',[]
    possible=[{'customer_id':row['id'],'name':row['name'],'reason':'name_in_title_unverified'} for row in rows if len(row['name_key'])>=6 and row['name_key'] in key][:3]
    return None,None,possible
