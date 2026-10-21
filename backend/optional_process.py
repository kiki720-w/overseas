"""Python3.9 bridge: safe-fetch HTML only, existing WebsiteService worker boundary."""
import base64,hashlib,time,sys
from pathlib import Path
from .common import AppError,now
APP_ROOT=Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]
DEFAULT_PYTHON=str(APP_ROOT/'components'/'extraction'/'runtime'/'python.exe')
MAX_INPUT=256*1024
MAX_REQUEST=360*1024
MAX_OUTPUT=65536
TIMEOUT=8

def extract_controlled(raw,text,parser,url,deadline,python=DEFAULT_PYTHON,timeout=TIMEOUT):
    from .website import run_website_worker
    from .website_text import evidence_lines
    if raw is None:raise AppError('无原始HTML；不能从Markdown片段恢复缺失正文或链接',422,'raw_html_unavailable')
    if not isinstance(raw,bytes):raise AppError('需要安全获取的原始HTML字节',400,'invalid_input')
    if len(raw)>MAX_INPUT:raise AppError('可选提取输入超过256KiB',413,'optional_input_limit')
    if not Path(python).is_file():raise AppError('未配置可选Python3.10以上运行环境，使用原生提取',503,'optional_runtime_missing')
    # No second full HTML copy: child repeats the safe caller's explicit decoding.
    request={'raw':base64.b64encode(raw).decode('ascii'),'url':url,'fetched_at':now(),'source_sha256':hashlib.sha256(raw).hexdigest(),'encoding':getattr(parser,'_safe_charset','utf-8')}
    result=run_website_worker(request,python,Path(__file__).with_name('optional_worker.py'),min(TIMEOUT,timeout,max(.01,deadline-time.monotonic())),MAX_OUTPUT,True)
    from .optional_reply import validate
    output=validate(result)
    lines,_,_=evidence_lines(parser);visible={' '.join(x.split()) for x in lines};segments=output['segments']
    for s in segments:
        if not isinstance(s,dict) or not isinstance(s.get('text'),str):raise AppError('可选段落结构无效',502,'optional_worker_output_invalid')
        if s.get('source_html_sha256')!=request['source_sha256'] or s.get('source_url')!=url or s.get('text') not in visible or s.get('quote')!=s['text'][:300] or s['fetched_at']!=request['fetched_at']:raise AppError('可选结果未能绑定已抓取原文',502,'optional_worker_output_invalid')
    from urllib.parse import urljoin
    from .webpage_url import normalize_page_url
    observed=set()
    for href in parser.links:
        try:observed.add(normalize_page_url(urljoin(url,href)))
        except AppError:continue
    for item in output['link_discovery']['candidates']:
        if item['source_url']!=url or item['source_html_sha256']!=request['source_sha256'] or item['url'] not in observed:raise AppError('Link was not observed in source HTML',502,'optional_worker_output_invalid')
    failures={'dependency_missing':'optional_dependency_missing','extractor_error':'optional_extractor_error','body_missing':'optional_body_missing','input_too_large':'optional_input_limit'}
    if output['primary_status'] in failures:raise AppError('Optional primary unavailable; retaining native body',503,failures[output['primary_status']])
    if not segments:raise AppError('No usable optional aligned body; retaining native body',422,'optional_body_insufficient')
    output['worker_boundary']=result.get('_worker_boundary')
    return output
