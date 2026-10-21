"""Bounded Tavily Bearer search. Independent user key; no automatic restore."""
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from .common import AppError,now
from .search_credentials import SearchCredentials

ENDPOINT='https://api.tavily.com/search'
MAX_BYTES=262144
TYPE_WORDS={'终端加工厂':'machine shop manufacturer','机床厂家':'machine tool manufacturer','经销商':'industrial distributor','设备集成商':'industrial equipment system integrator'}

def compose_query(request):
    parts=[request['query']]
    for label,key in [('Products','products'),('Market','countries')]:
        if request.get(key):parts.append(label+': '+request[key])
    if request.get('type'):parts.append('Customer type: '+TYPE_WORDS[request['type']])
    return ' | '.join(parts)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def request_once(payload,key,endpoint=ENDPOINT,max_bytes=MAX_BYTES):
    if endpoint not in (ENDPOINT,'https://cloud-iqs.aliyuncs.com/search/unified','https://api.deepseek.com/v1/tools/search','https://api.deepseek.com/v1/tools/fetch'):raise ValueError('Unsupported search endpoint')
    if not isinstance(key,str) or not key:raise AppError('请填写独立搜索 API Key',400,'search_key_required')
    request=urllib.request.Request(endpoint,data=json.dumps(payload).encode('utf8'),method='POST',
        headers={'Content-Type':'application/json','Authorization':'Bearer '+key,'X-Client-Source':'overseas-local'})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    try:
        response=opener.open(request,timeout=20)
    except urllib.error.HTTPError as e:response=e
    with response:
        status=response.code;raw=response.read(max_bytes+1)
    if len(raw)>max_bytes:
        error=AppError('搜索响应超过大小限制',502,'search_invalid_response')
        error.details={'provider_http_status':status};raise error
    try:body=json.loads(raw.decode('utf8'))
    except (ValueError,UnicodeError):
        if status!=200:body={}
        else:
            error=AppError('搜索服务返回非 JSON 响应',502,'search_invalid_response')
            error.details={'provider_http_status':status};raise error
    return status,body

class TavilySearch(SearchCredentials):
    def __init__(self,transport=None,cooldown=5,key='',vault=None):
        super().__init__(key,vault)
        self.transport=transport or request_once;self.cooldown=cooldown;self.lock=threading.Lock();self.last=None
    def payload(self,query):
        return {'query':query,'search_depth':'basic','topic':'general','max_results':8,
                'include_answer':False,'include_raw_content':False,'include_images':False,'auto_parameters':False}
    def normalize(self,body,query):
        if not isinstance(body.get('results'),list):raise AppError('搜索响应缺少结果列表',502,'search_invalid_response')
        return {'results':body['results'][:8],'executed_query':query,'provider':self.PROVIDER}
    def search(self,request):
        with self.config_lock:key=self._key;revision=self.revision
        if not key:raise AppError('尚未填写独立 '+self.LABEL+' Key；没有发出搜索请求',400,'search_key_required')
        if not self.lock.acquire(False):raise AppError('搜索正在进行，请稍后再试',429,'search_busy')
        try:
            if self.last is not None and time.monotonic()-self.last<self.cooldown:
                raise AppError('请稍候再发起搜索，避免重复请求',429,'search_busy')
            self.last=time.monotonic()
            query=compose_query(request)
            payload=self.payload(query)
            try:
                if request.get('_attempt_hook'):request['_attempt_hook']()
                status,body=self.transport(payload,key)
            except AppError:raise
            except (TimeoutError,socket.timeout):raise AppError('搜索请求超时，没有自动重试',504,'search_timeout')
            except urllib.error.URLError as e:
                if isinstance(e.reason,(TimeoutError,socket.timeout)):raise AppError('搜索请求超时，没有自动重试',504,'search_timeout')
                raise AppError('无法连接搜索服务，没有自动重试',502,'search_network_error')
            except (OSError,ValueError):raise AppError('搜索网络或响应错误，没有自动重试',502,'search_network_error')
            if not isinstance(body,dict):raise AppError('搜索响应格式无效',502,'search_invalid_response')
            if key in json.dumps(body,ensure_ascii=False):raise AppError('搜索响应含敏感内容，已拒绝展示',502,'search_invalid_response')
            with self.config_lock:
                if revision!=self.revision:raise AppError('请求期间搜索凭证已变更，结果已丢弃',409,'search_config_changed')
            if status!=200:
                envelope=body.get('error') if isinstance(body.get('error'),dict) else {}
                if status==401:raise AppError(self.LABEL+' 认证失败（401），请核对独立搜索 Key',401,'search_auth_failed')
                if status==403:raise AppError(self.LABEL+' 拒绝访问（403），请核对搜索账号权限',403,'search_forbidden')
                if status in (402,432,433):raise AppError(self.LABEL+' 搜索额度已用尽；没有自动重试或购买额度',429,'search_quota_exceeded')
                if status==429:
                    error=AppError(self.LABEL+' 限流（429）；请稍后重试，没有自动重试',429,'search_rate_limited')
                    detail={}
                    for key in ['code','window']:
                        value=envelope.get(key)
                        if isinstance(value,str):detail['provider_code' if key=='code' else key]=value[:100]
                    retry=envelope.get('retry_after_seconds')
                    if type(retry) is int and 0<=retry<=604800:detail['retry_after_seconds']=retry
                    error.details=detail;raise error
                raise AppError('搜索服务返回错误，没有自动重试',502,'search_provider_error')
            result=self.normalize(body,query)
            with self.config_lock:
                if revision!=self.revision:raise AppError('调用期间搜索配置已改变，结果已丢弃',409,'search_config_changed')
                self.verified_at=now();self.last_error=''
            return result
        except AppError as error:
            with self.config_lock:
                if revision==self.revision:self.last_error=error.code
            raise
        finally:self.lock.release()

# Historical import name only: it also requires an independent key now.
TavilyKeylessSearch=TavilySearch
