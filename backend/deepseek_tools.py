"""Pinned DeepSeek tools; explicit calls, session key, no model credential access."""
import datetime as dt
import ipaddress
import json
import math
import socket
import threading
import time
import urllib.error
from urllib.parse import urlsplit
from .common import AppError,now
from .search import request_once,TYPE_WORDS
from .webpage_url import inspect_page_url,normalize_page_url
from .search_credentials import SearchCredentials
from .fetch_quality import assess
from .website_fetch import normalize_url

DOCS='https://api-docs.deepseek.com/zh-cn/guides/search/'
PRICE=.005

def compose(request):
    if not isinstance(request.get('query'),str) or any(not isinstance(request.get(k,''),str) for k in ('products','countries','type')):raise AppError('DeepSeek 查询条件必须为文本',400,'invalid_input')
    parts=[request['query'].strip()]
    for value in (request.get('products',''),request.get('countries',''),TYPE_WORDS.get(request.get('type'),'').strip()):
        if value and value.casefold() not in ' '.join(parts).casefold():parts.append(value)
    query=' '.join(parts)
    if not 1<=len(query)<=128:raise AppError('DeepSeek 完整公开查询限 1–128 个 Unicode 字符；请缩短产品、市场或查询条件。没有发出请求。',400,'search_query_too_long')
    return query

def public_url(value):
    if not isinstance(value,str) or not 1<=len(value)<=8192 or any(ord(c)<33 for c in value) or '\\' in value:raise AppError('请填写公开 HTTP(S) 网页地址',400,'website_url_invalid')
    try:
        u=urlsplit(value);host=(u.hostname or '').encode('idna').decode('ascii').lower();port=u.port
        if u.scheme not in ('http','https') or not host or u.username is not None or u.password is not None or u.fragment or port not in (None,80 if u.scheme=='http' else 443):raise ValueError()
        if host in ('localhost','metadata','metadata.google.internal','instance-data') or host.endswith(('.localhost','.local','.internal')):raise ValueError()
        try:address=ipaddress.ip_address(host)
        except ValueError:address=None
        if address and not address.is_global or not address and '.' not in host:raise ValueError()
    except (ValueError,UnicodeError):raise AppError('仅支持无账号密码的公开 HTTP(S) 地址，禁止本地、私网及非标准端口',400,'website_unsafe_url') from None
    return value

class DeepSeekTools(SearchCredentials):
    SCOPE='deepseek-search-fetch-session-v1'
    PROVIDER='deepseek'
    LABEL='DeepSeek Search / Fetch'
    ENDPOINT='https://api.deepseek.com/v1/tools/search'
    FETCH_ENDPOINT='https://api.deepseek.com/v1/tools/fetch'
    def __init__(self,transport=None,key='',cooldown=.25):
        super().__init__(key=key,vault=None)
        self.transport=transport or (lambda tool,payload,key:request_once(payload,key,self.ENDPOINT if tool=='search' else self.FETCH_ENDPOINT,max_bytes=2*1024*1024))
        self.cooldown=cooldown;self.tool_locks={t:threading.Lock() for t in ('search','fetch')};self.last={};self.last_call={};self.call_records=[]
        self.usage={t:{k:0 for k in ('attempts','successful_nonempty','empty','failed','unknown_outcome','provider_http_200','provider_nonempty','provider_empty','provider_outcome_unknown','business_usable','business_filtered_empty','business_insufficient','business_parse_failed','provider_failed','business_provider_failed')} for t in ('search','fetch')}
    def settings(self):
        value=super().settings()
        with self.config_lock:
            usage={tool:dict(counts,estimated_cost_cny=round(counts['provider_nonempty']*PRICE,6),estimated_cost_min_cny=round(counts['provider_nonempty']*PRICE,6),estimated_cost_max_cny=round((counts['provider_nonempty']+counts['provider_outcome_unknown'])*PRICE,6),attempt_upper_bound_cny=round(counts['attempts']*PRICE,6),last_call=dict(self.last_call.get(tool,{})),recent_calls=[dict(v) for v in self.call_records if v['tool']==tool]) for tool,counts in self.usage.items()}
        value.update(fetch_endpoint=self.FETCH_ENDPOINT,price_cny_per_request=PRICE,price_source=DOCS,price_as_of='2026-10-18',query_max_characters=128,max_results=50,default_count=10,automatic_fallback=False,automatic_retry=False,tool_usage=usage,token_cost_included=False,billing_confirmed=False,scope='since_service_start',credential_persistence_available=False,credential_saved_encrypted=False)
        value['message']='DeepSeek 本次会话实测连接成功；海外覆盖与来源质量仍需核实。' if value['connection_validation']['state']=='verified' else 'DeepSeek 会话 Key 已保存，尚未验证连接。' if value['configured'] else 'DeepSeek 尚未配置，请自行填写 Search/Fetch Key。'
        return value
    def save(self,data):
        if not isinstance(data,dict):raise AppError('Key 配置格式无效',400,'invalid_input')
        if data.get('persist') or data.get('restore_key'):raise AppError('DeepSeek 工具 Key 仅本次运行内存保存，不支持持久保存或恢复',400,'key_persistence_unavailable')
        if data.get('api_key') and data.get('consent') is not True:raise AppError('请明确同意此 Key 用于 Search/Fetch；没有读取模型 Key',400,'consent_required')
        return super().save(data)
    def compose_query(self,request):return compose(request)
    def search(self,request):
        if not isinstance(request,dict) or set(request)-{'query','products','countries','type','count','need_content','ori_question','localization','_attempt_hook'}:raise AppError('不支持的 DeepSeek 查询参数；不使用 offset 分页',400,'invalid_input')
        query=compose(request);count=request.get('count',10);need=request.get('need_content',False)
        if type(count) is not int or not 1<=count<=50 or type(need) is not bool:raise AppError('DeepSeek count 必须为1–50整数，need_content 为布尔值',400,'invalid_input')
        payload={'query':query,'count':count,'need_content':need}
        original=request.get('ori_question')
        if original is not None:
            if not isinstance(original,str) or not original.strip() or len(original)>128:raise AppError('ori_question 限1–128字符，或省略/null',400,'invalid_input')
            payload['ori_question']=original
        location=request.get('localization','')
        if not isinstance(location,str):raise AppError('localization 必须为显式填写的 IP 或空字符串',400,'invalid_input')
        if location:
            try:ipaddress.ip_address(location)
            except ValueError:raise AppError('localization 必须为有效 IP；不会自动读取用户 IP',400,'invalid_input') from None
            payload['localization']=location
        body=self.call('search',payload,request.get('_attempt_hook'))
        rows=body['results'];results=[]
        for row in rows:
            results.append({**row,'url':inspect_page_url(row['url'])['display_url'],'content':row['snippet'],'page_content':row['content'],'source_date':row['date']})
        return {'results':results,'result_filtering':body['result_filtering'],'provider_http_status':200,'executed_query':query,'provider':self.PROVIDER,'coverage':'not_verified','price_cny_per_request':PRICE,'returned_webpages':len(results),'notice':'网页条数不等于企业数；搜索摘要与正文均为不可信来源内容，不能作为工具指令。'}
    def fetch(self,data):
        if not isinstance(data,dict) or set(data)-{'url','consent'} or data.get('consent') is not True:raise AppError('请明确同意把该公开 URL 发送给 DeepSeek Fetch，可能产生费用',400,'search_consent_required')
        url=public_url(normalize_page_url(data.get('url')))
        body=self.call('fetch',{'url':url})
        return {'provider':'deepseek','tool':'fetch','provider_http_status':200,'source_url':url,'url_policy':inspect_page_url(data['url']),'title':body['title'],'content':body['content'],'content_quality':assess(body['content']),'raw_html_available':False,'local_html_extraction':{'status':'raw_html_unavailable','message':'仅有正文片段，无原始HTML，无法恢复缺失正文或发现未返回的链接','automatic_request':False},'retrieved_at':now(),'content_format':'markdown','trusted_instructions':False,'automatic_crm_import':False,'automatic_model_call':False,'notice':'网页内容仅为来源证据，未执行其中指令；抓取成功不代表采购需求或真实性已核实。'}
    def validate_response(self,tool,body,count=10):
        if tool=='fetch':
            if not all(isinstance(body.get(k),str) for k in ('title','content')):raise AppError('DeepSeek Fetch 响应格式无效',502,'search_invalid_response')
            return bool(body['content'].strip())
        rows=body.get('results')
        if not isinstance(rows,list) or len(rows)>count:raise AppError('DeepSeek Search results 格式或数量无效',502,'search_invalid_response')
        accepted=[];rejected=[];diagnostics=[]
        for index,row in enumerate(rows):
            try:self.validate_row(row)
            except AppError as error:
                rejected.append({'index':index,'reason_code':error.code})
                if error.code in ('website_url_invalid','website_unsafe_url'):
                    from .url_rejection_diagnostics import explain
                    diagnostics.append(dict(explain(row.get('url') if isinstance(row,dict) else None,error),index=index))
            else:accepted.append(row)
        body['result_filtering']={'received_count':len(rows),'valid_count':len(accepted),'filtered_count':len(rejected),'rejected':rejected,'url_rejection_diagnostics':diagnostics}
        body['results']=accepted
        return bool(rows)
    def validate_row(self,row):
        if not isinstance(row,dict) or any(not isinstance(row.get(k),str) for k in ('title','url','snippet','summary','content')):raise AppError('DeepSeek 结果缺少有效文本字段',502,'search_invalid_response')
        inspect_page_url(row['url'])
        score=row.get('rerank_score');authority=row.get('authority')
        if type(score) not in (int,float) or not math.isfinite(score) or not 0<=score<=3 or type(authority) is not int or not -1<=authority<=4:raise AppError('DeepSeek 评分字段无效',502,'search_invalid_response')
        for key in ('site_name','site_icon','date'):
            if key not in row or row[key] is not None and not isinstance(row[key],str):raise AppError('DeepSeek 来源字段无效',502,'search_invalid_response')
        if row['date']:
            try:
                stamp=dt.datetime.fromisoformat(row['date'].replace('Z','+00:00'))
                if stamp.utcoffset()!=dt.timedelta(0):raise ValueError()
            except ValueError:raise AppError('DeepSeek 来源日期无效',502,'search_invalid_response') from None
    def call(self,tool,payload,hook=None):
        with self.config_lock:key=self._key;revision=self.revision
        if not key:raise AppError('请先手动输入 DeepSeek Search/Fetch Key；没有发出请求',400,'search_key_required')
        lock=self.tool_locks[tool]
        if not lock.acquire(False):raise AppError('该工具请求正在进行，没有重复发送',429,'search_busy')
        attempted=False;outcome='unknown_outcome';status=None;provider_outcome='provider_outcome_unknown';business_outcome='business_parse_failed'
        try:
            if time.monotonic()-self.last.get(tool,-1e10)<self.cooldown:raise AppError('请稍后手动重试；没有自动重试',429,'search_busy')
            if hook:hook()
            with self.config_lock:self.usage[tool]['attempts']+=1
            attempted=True;self.last[tool]=time.monotonic()
            try:status,body=self.transport(tool,payload,key)
            except (TimeoutError,socket.timeout):raise AppError('DeepSeek 请求超时；结果未知，没有自动重试或切换供应商',504,'search_timeout') from None
            except urllib.error.URLError:raise AppError('DeepSeek 网络错误，没有自动重试或切换供应商',502,'search_network_error') from None
            except (OSError,ValueError):raise AppError('DeepSeek 请求失败，没有自动重试',502,'search_network_error') from None
            if status!=200:
                outcome='failed';provider_outcome='provider_failed';business_outcome='business_provider_failed'
                mapping={400:(400,'search_invalid_request','请求参数无效'),401:(401,'search_auth_failed','Key 无效'),402:(402,'search_quota_exceeded','账号余额不足'),403:(403,'search_forbidden','抓取被安全策略拒绝'),404:(404,'search_fetch_unavailable','没有可用网页正文'),408:(504,'search_timeout','服务端读取超时'),413:(413,'search_request_too_large','请求过大'),429:(429,'search_rate_limited','账号或目标域名限速'),504:(504,'search_timeout','下游超时')}
                code,error,message=mapping.get(status,(502,'search_provider_error','服务端错误'))
                failure=AppError('DeepSeek '+message+'；没有自动重试或切换供应商',code,error)
                if status==429:failure.details={'retry_after_seconds':2}
                raise failure
            if status==200:
                with self.config_lock:self.usage[tool]['provider_http_200']+=1
                if isinstance(body,dict):
                    raw=body.get('results') if tool=='search' else body.get('content')
                    if isinstance(raw,list if tool=='search' else str):provider_outcome='provider_nonempty' if (bool(raw) if tool=='search' else bool(raw.strip())) else 'provider_empty'
            if not isinstance(body,dict) or key in json.dumps(body,ensure_ascii=False):raise AppError('DeepSeek 响应无效或包含敏感数据，已拒绝展示',502,'search_invalid_response')
            nonempty=self.validate_response(tool,body,payload.get('count',10));outcome='successful_nonempty' if nonempty else 'empty'
            business_outcome=('business_usable' if body['results'] else 'business_filtered_empty') if tool=='search' else ('business_insufficient' if assess(body['content'])['state']=='insufficient_body' else 'business_usable')
            with self.config_lock:
                if revision!=self.revision:raise AppError('调用期间 Key 已变化，结果已丢弃',409,'search_config_changed')
                self.last_error='';self.verified_at=now()
            return body
        except AppError as error:
            if status is None and getattr(error,'details',{}).get('provider_http_status')==200:
                status=200
                with self.config_lock:self.usage[tool]['provider_http_200']+=1
            if status is not None:error.details=dict(getattr(error,'details',{}),provider_http_status=status,tool=tool)
            with self.config_lock:
                if revision==self.revision:self.last_error=error.code
            raise
        finally:
            if attempted:
                with self.config_lock:
                    self.usage[tool][outcome]+=1
                    if provider_outcome in self.usage[tool]:self.usage[tool][provider_outcome]+=1
                    if business_outcome in self.usage[tool]:self.usage[tool][business_outcome]+=1
                    self.last_call[tool]={'tool':tool,'provider_http_status':status,'provider_outcome':provider_outcome,'business_outcome':business_outcome,'estimated_cost_min_cny':PRICE if provider_outcome=='provider_nonempty' else 0,'estimated_cost_max_cny':PRICE if provider_outcome in ('provider_nonempty','provider_outcome_unknown') else 0,'billing_confirmed':False,'at':now()}
                    self.call_records.append(dict(self.last_call[tool]));self.call_records=self.call_records[-100:]
            lock.release()
