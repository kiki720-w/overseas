"""Allowlisted diagnostics: no URLs, exception messages or request bodies."""
import secrets,time,json,logging
from .common import AppError
STAGES={'robots','fetch','parse','extraction','worker'}
CODES={'website_robots_blocked','website_timeout','website_dns_failed','website_http_error','website_type_blocked','website_redirect_blocked','website_too_large','website_no_text','website_unavailable','website_busy','optional_body_insufficient','optional_worker_failed','optional_worker_timeout','optional_runtime_missing','optional_reply_invalid'}
CODES.update({'website_worker_failed','website_worker_output_invalid','website_worker_output_limit','website_url_invalid','website_unsafe_url'})
def safe_code(value):
    return value if isinstance(value,str) and value in CODES else 'website_unavailable'

def safe_status(value):
    return value if isinstance(value,int) and not isinstance(value,bool) and 400<=value<=599 else 502
def diagnostic(code,status,started,enabled,stage='worker',request_id=None):
    return {'request_id':request_id or secrets.token_hex(8),'failure_stage':stage if isinstance(stage,str) and stage in STAGES else 'worker','error_type':safe_code(code),'http_status':safe_status(status),'elapsed_ms':max(0,round((time.monotonic()-started)*1000)),'extractor_enabled':bool(enabled),'fallback_extractor':None}
def annotate(error,started,enabled,stage='worker',request_id=None,emit=False):
    details=getattr(error,'details',{})
    current=details.get('website_diagnostic')
    safe=diagnostic(error.code,error.status,started,enabled,stage,request_id)
    if isinstance(current,dict):
        if isinstance(current.get('failure_stage'),str) and current['failure_stage'] in STAGES:safe['failure_stage']=current['failure_stage']
    upstream=current.get('upstream_http_status') if isinstance(current,dict) else None
    if isinstance(upstream,int) and 100<=upstream<=599:safe['upstream_http_status']=upstream
    error.code=safe['error_type'];error.status=safe['http_status']
    error.details={'website_diagnostic':safe,'request_id':safe['request_id']}
    if emit:logging.getLogger('website.diagnostic').warning(json.dumps(safe,separators=(',',':')))
    return error
def public_error(error):
    number=error.details['request_id']
    message={'website_robots_blocked':'网站访问规则阻止采集','website_timeout':'官网采集超时','website_no_text':'未取得可用正文','website_busy':'采集任务繁忙'}.get(error.code,'官网采集未完成，失败原因请依据诊断记录核实')
    error.args=(message+'（关联编号 '+number+'）',)
    return error
