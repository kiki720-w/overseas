"""Discover observed links in saved HTML; never fetch or resolve their targets."""
import re
from urllib.parse import urljoin,urlsplit
from .common import AppError
from .webpage_url import normalize_page_url
from .search_identity import reference_url


def candidates(tree,text,url,source_hash):
    from courlan import extract_links
    discovered=set()
    for external in (False,True):
        discovered.update(extract_links(text,url,external_bool=external,with_nav=True,redirects=False))
    valid=set()
    for value in discovered:
        try:valid.add(normalize_page_url(value))
        except AppError:continue
    def document_base(value):
        p=urlsplit(value);return p.scheme,p.netloc,p.path
    valid_bases={document_base(value) for value in valid}
    items=[];seen=set();rejected={}
    for node in tree.xpath('//a[@href]'):
        family=[node,*node.iterancestors()]
        if any(n.tag in ('script','style','template','noscript','iframe','form') or 'hidden' in n.attrib or n.get('aria-hidden','').lower()=='true' or re.search(r'display\s*:\s*none|visibility\s*:\s*hidden',n.get('style',''),re.I) for n in family):continue
        try:target=normalize_page_url(urljoin(url,node.get('href')))
        except AppError as error:
            rejected[error.code]=rejected.get(error.code,0)+1;continue
        if (target not in valid and document_base(target) not in valid_bases) or target==normalize_page_url(url) or target in seen:continue
        label=' '.join(node.text_content().split())[:160]
        same=urlsplit(target).hostname==urlsplit(url).hostname
        signal=urlsplit(target).path+' '+label
        if re.search(r'capabilit|machining|gearing|hobbing|grinding|service|加工|工艺|能力',signal,re.I):role='capabilities'
        elif re.search(r'products?|产品',signal,re.I):role='products'
        elif re.search(r'about|company|关于|公司',signal,re.I):role='about'
        elif not same and re.search(r'official (?:web)?site|company website|visit website|官网|官方网站',label,re.I):role='external_site_candidate'
        else:continue
        seen.add(target)
        items.append({'url':target,'label':label,'page_role':role,'relation':'same_host_page' if same else 'external_reference',
                      'source_url':url,'source_xpath':tree.getroottree().getpath(node),'source_html_sha256':source_hash,
                      'source_is_directory':reference_url(url),'official_site_verified':False,'entity_identity_verified':False,
                      'request_performed':False})
        if len(items)==12:break
    return {'extractor':'courlan','redirect_checks':False,'requests':0,'candidate_limit':12,
            'candidates':items,'rejected_reason_counts':rejected,
            'notice':'仅为原 HTML 中真实存在的链接线索；跨站链接不是已核实官网，需另行确认后访问。'}
