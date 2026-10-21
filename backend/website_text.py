"""Bounded structural text extraction; exact normalized excerpts, no generated facts."""
import re
from html.parser import HTMLParser

class VisibleHTML(HTMLParser):
    BLOCK={'script','style','noscript','template','svg','head','iframe','nav','footer','aside','form','button','select'}
    BREAK={'p','div','li','h1','h2','h3','h4','br','tr','section','article','main'}
    VOID={'br','img','input','meta','link','hr','source','wbr','area','base','embed','param','track','col'}
    def __init__(self):
        super().__init__(convert_charrefs=True);self.stack=[];self.parts=[];self.primary=[];self.links=[];self.title=[];self.restrictions=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs);role=a.get('role','').lower();tokens=re.split(r'\s+',a.get('class','').lower()+' '+a.get('id','').lower())
        hidden='hidden' in a or a.get('aria-hidden','').lower()=='true' or bool(re.search(r'display\s*:\s*none|visibility\s*:\s*hidden',a.get('style',''),re.I))
        boiler=tag in self.BLOCK or role in ('navigation','banner','contentinfo') or tag=='header' and not any(f['primary'] for f in self.stack) or any(re.fullmatch(r'(?:site[-_])?(?:nav(?:igation)?|footer|menu|cookie(?:[-_]banner)?|breadcrumb|sidebar)',t) for t in tokens)
        blocked=hidden or boiler or any(f['blocked'] for f in self.stack)
        primary=tag in ('main','article') or role=='main' or any(f['primary'] for f in self.stack)
        if tag=='meta' and a.get('name','').lower() in ('robots','overseasresearchbot'):self.restrictions.append(a.get('content','').lower())
        # Follow-up links remain bounded by Collector; links in global chrome do not drown out content links.
        if tag=='a' and a.get('href') and not hidden and not any(f['tag'] in ('script','style','noscript','template','svg','head','iframe') for f in self.stack):self.links.append(a['href'])
        if tag in self.BREAK and not blocked:self.parts.append('\n');self.primary.append('\n') if primary else None
        if tag not in self.VOID:self.stack.append({'tag':tag,'blocked':blocked,'primary':primary})
    def handle_endtag(self,tag):
        if tag in self.VOID:return
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i]['tag']==tag:
                f=self.stack[i]
                if tag in self.BREAK and not f['blocked']:self.parts.append('\n');self.primary.append('\n') if f['primary'] else None
                del self.stack[i:];break
    def handle_data(self,data):
        if any(f['tag']=='title' for f in self.stack):self.title.append(data)
        if not any(f['blocked'] for f in self.stack):
            self.parts.append(data)
            if any(f['primary'] for f in self.stack):self.primary.append(data)
    def lines(self):
        def clean(parts):
            result=[]
            for raw in ''.join(parts).splitlines():
                text=' '.join(raw.split())
                if len(text)>3 and text not in result:result.append(text)
            return result
        primary=clean(self.primary);return (primary,'semantic_main') if any(len(x)>=45 for x in primary) else (clean(self.parts),'body_without_chrome')

def evidence_lines(parser):
    lines,method=parser.lines()
    lines=content_lines(lines)
    # Sentences/paragraphs precede short heading labels without creating summaries.
    ranked=sorted(enumerate(lines),key=lambda p:(len(p[1])>=60,len(p[1].split())>=8,len(p[1])>=30,-p[0]),reverse=True)
    quoted=[line[:300] for _,line in ranked[:8]]
    return lines,quoted,method

# Exact generic labels only. Product names, headings with values, contact data and
# substantive statements are retained; these rules never generate new evidence.
NAV_LABELS={'产品与服务','产品服务','应用领域','合作客户','联系我们','公司简介','发展历程','营销网络','供应商管理','投资者关系','股票走势','集团公告','财报下载','企业文化','员工风采','社会责任','人才发展','人才理念','职业发展','网站地图','网站建设','了解更多','新闻资讯','首页','返回顶部','products','services','products and services','applications','customers','partners','about us','contact us','sitemap','privacy policy','terms of use','read more','learn more'}
def noise_line(text):
    t=' '.join(text.split());key=t.strip(' :：|›»<>').casefold()
    if key in NAV_LABELS:return True
    tokens=[p.strip().casefold() for p in re.split(r'\s*[|›»]\s*',t) if p.strip()]
    if len(tokens)>1 and all(p in NAV_LABELS for p in tokens):return True
    # Whole-block legal grammar only; do not strip copyright substrings and
    # reinterpret the remaining prose as an owner. Separators, prose casing,
    # numbers, email, or extra clauses make the block uncertain: retain it.
    owner=r'(?:[A-Z][A-Za-z&-]*(?: [A-Z][A-Za-z&-]*){0,7} (?:Ltd|LLC|Inc|Company)|[\u4e00-\u9fff]{2,60}(?:股份有限公司|有限公司))'
    mark=r'(?:Copyright\s*[@©]?\s*\d{4}|©\s*\d{4})'
    legal=r'(?:版权所有\.?\s*)?(?:[Aa][Ll][Ll] [Rr][Ii][Gg][Hh][Tt][Ss] [Rr][Ee][Ss][Ee][Rr][Vv][Ee][Dd])\.?'
    registry=r'(?:\s*(?:浙|京|沪|粤)?(?:公网安备|ICP备)\s*[:：]?\s*[A-Za-z0-9-]+号?)?'
    pure=r'(?:'+mark+r'\s+'+owner+r'|'+owner+r'\s+'+mark+r')\.?\s+'+legal+registry
    if re.fullmatch(pure,t):return True
    if re.fullmatch(mark+r'\s*(?:[Aa]ll [Rr]ights [Rr]eserved\.?)?',t):return True
    if re.fullmatch(r'.{0,2}ICP备\s*[A-Za-z0-9-]+号?',t):return True
    if re.fullmatch(r'[<>\s\d]+(?:GO|下一页|上一页)',t,re.I):return True
    return False

def document_source(url,metadata=None):
    from urllib.parse import urlsplit,unquote,parse_qs
    parts=urlsplit(url);path=unquote(unquote(parts.path)).casefold()
    if path.endswith('.pdf'):return True
    for key,values in parse_qs(parts.query).items():
        if key.casefold() in ('file','filename','document','download','format','type'):
            if any(unquote(v).casefold().endswith('.pdf') or v.casefold() in ('pdf','application/pdf') for v in values):return True
    m=metadata or {}
    if any('pdf' in str(m.get(k,'')).casefold() for k in ('content_type','content-type','mime_type')):return True
    if m.get('evidence_level')=='third_party_document_unverified' or m.get('source_type')=='third_party_document_unverified':return True
    classification=m.get('source_classification') or {}
    if isinstance(classification,dict) and classification.get('kind')=='third_party_document':return True
    return False

def capture_observations(capture):
    return [{'source_kind':'fetched_body_self_statement','source_url':page['url'],'observed_at':page['fetched_at'],'round_id':None}
            for page in capture.get('pages',[])[:12] if isinstance(page.get('url'),str) and isinstance(page.get('fetched_at'),str)]

def capture_evidence_level(capture):
    pages=capture.get('pages',[])
    if document_source(capture.get('input_url',''),capture) or any(document_source(p.get('url',''),p) for p in pages):return 'third_party_document_unverified'
    levels=[capture.get('evidence_level')]+[p.get('evidence_level') for p in pages]
    return next((x for x in levels if x and x!='website_self_statement'),'website_self_statement')

def content_lines(lines):
    return list(dict.fromkeys(x for x in lines if not noise_line(x)))

def categorize_lines(lines,title=''):
    substantive=[x[:300] for x in content_lines(lines) if x!=title.strip() and len(x)>=25 and (len(x.split())>=4 or len(re.findall(r'[\u4e00-\u9fff]',x))>=12)]
    substantive=list(dict.fromkeys(substantive))
    company=[x for x in substantive if re.search(r'company|founded|公司|成立',x,re.I)][:2]
    # Categories may share an exact source paragraph; Collector deduplicates evidence atoms.
    products=[x for x in substantive if re.search(r'product|cnc|machin|gear|fixture|产品|加工|齿轮|夹具',x,re.I)][:2]
    return company,products
