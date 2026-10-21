from pathlib import Path
"""Bounded public-site reader. DNS results are validated and the socket is pinned."""
import http.client
import hashlib
import ipaddress
import re
import socket
import ssl
import time
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit, urljoin, unquote
from urllib.robotparser import RobotFileParser
from .common import AppError, now
from .model_transport import is_public

AGENT = 'OverseasResearchBot/1.0'
MAX_BYTES = 256 * 1024

def normalize_url(value):
    if not isinstance(value,str) or not value or len(value)>500 or any(ord(c)<33 for c in value) or '\\' in value:
        raise AppError('请输入不超过500字符的公开HTTP(S)网址',code='website_url_invalid')
    try:
        u=urlsplit(value);host=(u.hostname or '').encode('idna').decode('ascii').lower();port=u.port
    except (ValueError,UnicodeError):raise AppError('网址格式无效',code='website_url_invalid')
    if u.scheme not in ('http','https') or not host or u.username is not None or u.password is not None or port not in (None,80 if u.scheme=='http' else 443) or u.query or u.fragment:
        raise AppError('只接受默认端口、无账号/参数/片段的HTTP(S)网址',code='website_url_invalid')
    if host in ('localhost','metadata','metadata.google.internal','instance-data') or host.endswith(('.localhost','.local','.internal')):
        raise AppError('禁止访问本机、私网或元数据地址',code='website_unsafe_url')
    try:
        if not is_public(ipaddress.ip_address(host)):raise AppError('禁止非公网地址',code='website_unsafe_url')
    except ValueError:
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?',host) or '.' not in host:raise AppError('需要公开域名',code='website_url_invalid')
    if '%' in host or any(x in ('.','..') for x in u.path.split('/')):raise AppError('网址路径无效',code='website_url_invalid')
    rendered='['+host+']' if ':' in host else host
    try:(u.path or '/').encode('ascii')
    except UnicodeError:raise AppError('请使用浏览器复制的编码网址',code='website_url_invalid')
    return urlunsplit((u.scheme,rendered,u.path or '/','',''))

from .webpage_url import normalize_page_url

def public_ips(url,resolver=socket.getaddrinfo):
    u=urlsplit(url);port=443 if u.scheme=='https' else 80
    try:rows=resolver(u.hostname,port,type=socket.SOCK_STREAM)
    except OSError:raise AppError('官网DNS解析失败',502,'website_dns_failed')
    ips=[]
    for row in rows:
        raw=row[4][0]
        try:addr=ipaddress.ip_address(raw)
        except ValueError:raise AppError('DNS返回无效地址',400,'website_unsafe_url')
        if not is_public(addr):raise AppError('DNS含非公网地址，已拒绝采集',400,'website_unsafe_url')
        if raw not in ips:ips.append(raw)
    if not ips:raise AppError('DNS没有可用地址',502,'website_dns_failed')
    return ips

class PinnedConnection(http.client.HTTPConnection):
    def __init__(self,url,ip):
        u=urlsplit(url);self.secure=u.scheme=='https';self.ip=ip
        super().__init__(u.hostname,443 if self.secure else 80,timeout=4)
    def connect(self):
        sock=socket.create_connection((self.ip,self.port),self.timeout)
        try:self.sock=ssl.create_default_context().wrap_socket(sock,server_hostname=self.host) if self.secure else sock
        except Exception:sock.close();raise

def fetch_public(url,deadline,resolver=socket.getaddrinfo,connection= PinnedConnection):
    """One request, no proxy/cookie/auth/retry; redirects are handled by Collector."""
    url=normalize_page_url(url)
    ips=public_ips(url,resolver);conn=connection(url,ips[0])
    try:
        if time.monotonic()>deadline:raise AppError('采集超过时间限制',504,'website_timeout')
        parts=urlsplit(url);target=urlunsplit(('', '', parts.path or '/',parts.query,''))
        conn.request('GET',target,headers={'User-Agent':AGENT,'Accept':'text/html,text/plain','Accept-Encoding':'identity'})
        r=conn.getresponse();headers={k.lower():v for k,v in r.getheaders()}
        if r.status in (301,302,303,307,308):return r.status,headers,b''
        if headers.get('content-encoding','identity').lower() not in ('identity',''):raise AppError('不读取压缩响应',502,'website_type_blocked')
        length=headers.get('content-length','')
        if length and (not length.isdigit() or int(length)>MAX_BYTES):raise AppError('页面超过256KiB限制',413,'website_too_large')
        chunks=[];size=0
        while True:
            if time.monotonic()>deadline:raise AppError('采集超过时间限制',504,'website_timeout')
            part=r.read1(min(16384,MAX_BYTES+1-size))
            if not part:break
            chunks.append(part);size+=len(part)
            if size>MAX_BYTES:raise AppError('页面超过256KiB限制',413,'website_too_large')
        return r.status,headers,b''.join(chunks)
    except AppError:raise
    except (TimeoutError,socket.timeout):raise AppError('官网响应超时',504,'website_timeout')
    except (OSError,ssl.SSLError,http.client.HTTPException):raise AppError('无法安全读取官网；未绕过网站限制',502,'website_unavailable')
    finally:conn.close()

from .website_text import VisibleHTML, evidence_lines, content_lines, categorize_lines, noise_line

def restricted(text):return bool(re.search(r'\b(?:none|noindex|noarchive|nosnippet|noai|noimageai)\b',text.lower()))

class Collector:
    def __init__(self,fetch=fetch_public,optional_body=False,extraction_python=None,extraction_timeout=8):
        if not isinstance(optional_body,bool):raise TypeError("optional_body must be bool")
        self.fetch=fetch;self.optional_body=optional_body;self.extraction_python=extraction_python;self.extraction_timeout=extraction_timeout
    def collect(self,value):
        from .website_diagnostics import annotate
        started=time.monotonic();self._diagnostic_stage="robots";self._upstream_status=None
        try:return self._collect(value)
        except AppError as error:
            error=annotate(error,started,self.optional_body,self._diagnostic_stage)
            if self._upstream_status is not None:error.details["website_diagnostic"]["upstream_http_status"]=self._upstream_status
            raise error

    def _collect(self,value):
        start=normalize_page_url(value);origin=urlsplit(start);deadline=time.monotonic()+18;requests=0
        def read(url,check_robots=False):
            nonlocal requests
            for step in range(3):
                url=normalize_page_url(url);u=urlsplit(url)
                if u.hostname!=origin.hostname or origin.scheme=='https' and u.scheme!='https':raise AppError('重定向到其他域名或降级HTTP，已停止；请人工核对最终官网',400,'website_redirect_blocked')
                if check_robots and not robot.can_fetch(AGENT,url):raise AppError('robots禁止此页或重定向目的页',403,'website_robots_blocked')
                requests+=1
                if requests>10 or time.monotonic()>deadline:raise AppError('采集超过请求/时间限制',504,'website_timeout')
                self._upstream_status=None
                status,headers,raw=self.fetch(url,deadline)
                self._upstream_status=status
                if status not in (301,302,303,307,308):return url,status,headers,raw
                if not headers.get('location'):raise AppError('重定向缺少地址',502,'website_redirect_blocked')
                url=urljoin(url,headers['location'])
            raise AppError('重定向链过长，已停止',502,'website_redirect_blocked')
        robots_url=urlunsplit((origin.scheme,origin.netloc,'/robots.txt','',''))
        final,status,h,raw=read(robots_url)
        robot=RobotFileParser();robot.set_url(final)
        if status==404:robot.parse([]);robots_status='robots.txt 404；无规则文件'
        elif status==200:
            if len(raw)>65536 or h.get('content-type','').split(';')[0].strip().lower()!='text/plain':raise AppError('无法可靠读取robots规则，已停止',502,'website_robots_blocked')
            robot.parse(raw.decode('utf8','replace').splitlines());robots_status='已读取robots.txt'
            if robot.crawl_delay(AGENT) or robot.request_rate(AGENT):raise AppError('网站要求采集间隔；本版停止自动读取，请人工查看',403,'website_robots_blocked')
        else:raise AppError('robots.txt不可读取，已停止；不绕过网站限制',403,'website_robots_blocked')
        pages=[];queue=[start];seen=set();warnings=[]
        while queue and len(pages)<3:
            url=queue.pop(0)
            if url in seen:continue
            seen.add(url)
            if not robot.can_fetch(AGENT,url):
                if not pages:raise AppError('robots.txt不允许采集此页',403,'website_robots_blocked')
                warnings.append('robots禁止读取：'+url);continue
            try:
                self._diagnostic_stage="fetch"
                final,status,h,raw=read(url,True)
                if not robot.can_fetch(AGENT,final):raise AppError('robots禁止重定向后的页面',403,'website_robots_blocked')
                if status!=200:raise AppError('官网返回HTTP '+str(status)+'；未绕过限制',502,'website_http_error')
                if h.get('content-type','').split(';')[0].strip().lower() not in ('text/html','application/xhtml+xml'):raise AppError('只读取HTML公开页面',415,'website_type_blocked')
                if restricted(h.get('x-robots-tag','')):raise AppError('网站禁止索引或保留摘要，已停止',403,'website_robots_blocked')
                charset=re.search(r'charset\s*=\s*["\']?([\w-]+)',h.get('content-type',''),re.I)
                try:text=raw.decode(charset.group(1) if charset else 'utf-8','replace')
                except LookupError:raise AppError('页面编码不支持',415,'website_type_blocked')
                self._diagnostic_stage="parse"
                p=VisibleHTML();p.feed(text);p._safe_charset=charset.group(1) if charset else 'utf-8'
                if any(restricted(x) for x in p.restrictions):raise AppError('页面禁止索引/保留摘要，已停止',403,'website_robots_blocked')
                lines,quotes,extraction_method=evidence_lines(p)
                optional_result=None
                optional_failure=None
                if self.optional_body:
                    self._diagnostic_stage="extraction"
                    from .optional_process import extract_controlled,DEFAULT_PYTHON
                    try:
                        optional_result=extract_controlled(raw,text,p,final,deadline,self.extraction_python or DEFAULT_PYTHON,self.extraction_timeout)
                        lines=[s['text'] for s in optional_result['segments']];quotes=[s['quote'] for s in optional_result['segments']]
                        extraction_method='optional_trafilatura_controlled_worker'
                    except AppError as failure:
                        from .website_diagnostics import safe_code,safe_status
                        failure.code=safe_code(failure.code);failure.status=safe_status(failure.status)
                        failure.args=("Optional extraction did not complete; native extraction retained.",)
                        optional_failure={'diagnostic':{'failure_stage':'extraction','error_type':failure.code,'http_status':failure.status,'extractor_enabled':True,'fallback_extractor':'native_visible_parser'},'status':'native_fallback_optional_failed','failure_code':failure.code,'failure_message':str(failure),'trafilatura_succeeded':False,'feature_enabled':True}
                        warnings.append('可选提取未成功（'+failure.code+'）；已回退原生网页提取：'+str(failure))
                if not quotes:raise AppError('未提取到可用正文；不执行脚本或登录',422,'website_no_text')
                lines=content_lines(lines);quotes=[q for q in quotes if not noise_line(q)]
                company,products=categorize_lines(lines,' '.join(p.title))
                emails=list(dict.fromkeys(re.findall(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '\n'.join(p.lines()[0]+lines))))[:6]
                quotes=list(dict.fromkeys(products+company+quotes))[:8]
                pages.append({'url':final,'requested_url':url,'fetched_at':now(),'title':' '.join(p.title)[:200],'company_information':company,'product_information':products,'evidence':[{'quote':q[:300],'source_url':final} for q in quotes],'public_emails':emails,'extraction':{'method':extraction_method,'body_lines':len(lines),'raw_html_retained':False,'body_excerpt_retained':True,'source_html_sha256':hashlib.sha256(raw).hexdigest()},'body_excerpt':'\n'.join(lines)[:2000],'evidence_level':'third_party_document_unverified' if unquote(urlsplit(final).path).lower().endswith('.pdf') else 'website_self_statement','independently_proven':False})
                if optional_result is not None:
                    pages[-1]['optional_body']=optional_result
                    pages[-1]['observed_link_candidates']=optional_result['link_discovery']
                    segments={s['quote']:s for s in optional_result['segments']}
                    for evidence in pages[-1]['evidence']:
                        evidence['extraction_provenance']={k:v for k,v in segments[evidence['quote']].items() if k not in ('text','quote')}
                    if optional_result['fallback_count']:warnings.append('Optional extraction used explicitly low-trust structured product supplements; source and identity review required.')
                if optional_failure is not None:
                    pages[-1]['optional_body']=optional_failure
                    for evidence in pages[-1]['evidence']:
                        index=next((i for i,line in enumerate(lines,1) if line.startswith(evidence['quote'])),None)
                        evidence['extraction_provenance']={'extractor':'native_visible_parser','extractor_version':hashlib.sha256(Path(__file__).with_name('website_text.py').read_bytes()).hexdigest(),'position':{'native_line_index':index,'dom_position_available':False},'source_html_sha256':hashlib.sha256(raw).hexdigest(),'source_url':final,'fetched_at':pages[-1]['fetched_at'],'fallback':True,'fallback_reason':optional_failure['failure_code'],'confidence':'website_self_statement_unverified','identity_verified':False,'actual_compatibility':'unknown','purchase_intent':'unknown'}
                if 'nofollow' not in h.get('x-robots-tag','').lower() and not any('nofollow' in x for x in p.restrictions):
                    for link in p.links:
                        target=urljoin(final,link)
                        try:target=normalize_page_url(target)
                        except AppError:continue
                        if urlsplit(target).hostname==origin.hostname and re.search(r'about|contact|product|company|capabilit|service|machining|gearing',urlsplit(target).path,re.I) and target not in seen and target not in queue:queue.append(target)
                    def priority(value):
                        path=urlsplit(value).path.lower()
                        return 0 if re.search(r'capabilit|service|machining|gearing',path) else 1 if 'product' in path else 2 if re.search(r'about|company',path) else 3
                    queue=sorted(queue,key=priority)[:2]
            except AppError as e:
                if not pages:raise
                from .website_diagnostics import safe_code
                warnings.append('Additional page collection did not complete ('+safe_code(e.code)+').')
        return {'input_url':start,'fetched_at':now(),'pages':pages,'warnings':warnings,'robots_status':robots_status,'requests':requests,'notice':'官网原文/公开联系信息仅为网站自述；不证明采购意向、规模、决策人或联系方式现时有效。未调用模型。'}
