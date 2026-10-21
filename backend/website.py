from .website_text import capture_evidence_level,capture_observations
"""Customer-scoped previews and transactional, idempotent confirmations."""
import json
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from .common import AppError,field,dump,now
from .webpage_url import normalize_page_url

_WORKER_SLOTS=threading.BoundedSemaphore(2)

def run_website_worker(request,python=None,script=None,timeout=22,output_limit=65536,controlled=False,stage=None):
    """Shared existing communicate/kill boundary; optional CPU worker reuses OCR Job structs."""
    stage=stage or ('website' if 'url' in request and 'raw' not in request else 'optional' if controlled else 'website')
    if stage not in ('website','optional'):raise ValueError('Invalid worker stage')
    optional_stage=stage=='optional'
    if not _WORKER_SLOTS.acquire(False):raise AppError('Worker slots busy',429,'optional_worker_busy' if optional_stage else 'website_busy')
    proc=None;job=None;kernel=None
    try:
        if controlled:
            from .ocr_process import k,EXT,w,c
            kernel=k
            if kernel is None:raise AppError('Windows受控进程边界不可用',503,'optional_isolation_unavailable' if optional_stage else 'website_unavailable')
            job=k.CreateJobObjectW(None,None);limits=EXT();limits.BasicLimitInformation.LimitFlags=0x2000|0x200|0x8
            limits.BasicLimitInformation.ActiveProcessLimit=4;limits.JobMemoryLimit=256*1024**2
            if not job or not k.SetInformationJobObject(job,9,c.byref(limits),c.sizeof(limits)):raise AppError('无法设置提取进程资源上限',503,'optional_isolation_unavailable' if optional_stage else 'website_unavailable')
        kwargs=dict(stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        if controlled:
            import os
            env={key:os.environ[key] for key in ('SystemRoot','WINDIR','COMSPEC','TEMP','TMP') if key in os.environ}
            env['PYTHONIOENCODING']='utf-8';kwargs.update(env=env,creationflags=0x08000000|0x4)
        from .worker_entry import command as worker_command
        command=worker_command('website-fetch') if python is None and script is None else [str(python or sys.executable),str(script or Path(__file__).with_name('website_worker.py'))]
        proc=subprocess.Popen(command,**kwargs)
        if controlled:
            if not kernel.AssignProcessToJobObject(job,w.HANDLE(proc._handle)):raise AppError('无法绑定提取进程资源上限',503,'optional_isolation_unavailable' if optional_stage else 'website_unavailable')
            native=c.WinDLL('ntdll');native.NtResumeProcess.argtypes=[w.HANDLE]
            if native.NtResumeProcess(w.HANDLE(proc._handle))!=0:raise AppError('无法启动受控提取进程',503,'optional_isolation_unavailable' if optional_stage else 'website_unavailable')
        raw,_=proc.communicate(json.dumps(request).encode('utf8'),timeout=timeout)
        if len(raw)>output_limit:raise AppError('提取结果超过字节上限',502,'optional_worker_output_limit' if optional_stage else 'website_worker_output_limit')
        if proc.returncode:raise AppError('工作进程未返回有效结果',502,'optional_worker_failed' if optional_stage else 'website_worker_failed')
        try:result=json.loads(raw)
        except ValueError:raise AppError('工作进程结果格式无效',502,'optional_worker_output_invalid' if optional_stage else 'website_worker_output_invalid')
        if not isinstance(result,dict):raise AppError('工作进程结果结构无效',502,'optional_worker_output_invalid' if optional_stage else 'website_worker_output_invalid')
        if controlled:
            measured=bool(kernel.QueryInformationJobObject(job,9,c.byref(limits),c.sizeof(limits),None))
            result['_worker_boundary']={'timeout_seconds':timeout,'output_bytes':len(raw),'output_limit_bytes':output_limit,'job_commit_limit_bytes':256*1024**2,'peak_job_commit_bytes':limits.PeakJobMemoryUsed if measured else None,'job_query_succeeded':measured,'memory_measurement':'Windows PeakJobMemoryUsed raw historical counter; not current successful commit or RSS bound','peak_within_configured_limit':limits.PeakJobMemoryUsed<=limits.JobMemoryLimit if measured else None,'error_stage':stage,'job_process_limit':4,'request_concurrency_limit':2,'hard_kill_on_timeout':True}
        return result
    except subprocess.TimeoutExpired:
        if job:kernel.CloseHandle(job);job=None
        if proc:proc.kill();proc.communicate()
        raise AppError('Optional extraction timed out; worker terminated' if optional_stage else 'Website collection timed out; worker terminated',504,'optional_worker_timeout' if optional_stage else 'website_timeout')
    except OSError as error:raise AppError('可选提取运行环境不可用（Windows错误 '+str(getattr(error,'winerror',None))+'）',503,'optional_runtime_missing' if optional_stage else 'website_unavailable')
    finally:
        if job:kernel.CloseHandle(job)
        if proc and proc.poll() is None:proc.kill();proc.communicate()
        _WORKER_SLOTS.release()

class WebsiteStore:
    def init_website(self,db):
        db.executescript('CREATE TABLE IF NOT EXISTS website_captures(id TEXT PRIMARY KEY,customer_id TEXT NOT NULL,data TEXT NOT NULL);CREATE INDEX IF NOT EXISTS website_customer ON website_captures(customer_id);')
    def website_captures(self,cid):
        self.customer(cid)
        with self.db() as db:items=[json.loads(r[0]) for r in db.execute('SELECT data FROM website_captures WHERE customer_id=? ORDER BY rowid DESC LIMIT 20',(cid,))]
        return {'items':items,'notice':'官网资料是自述，不是已证实采购意向。最多显示最近20次。'}

class WebsiteService:
    def __init__(self,store,collector=None,optional_body=False,extraction_python=None):
        if not isinstance(optional_body,bool):raise TypeError("optional_body must be bool")
        self.optional_body=optional_body;self.extraction_python=extraction_python;self.store=store;self.collector=collector;self.jobs={};self.lock=threading.Lock();self.slots=threading.BoundedSemaphore(2)
    def collect(self,url):
        from .website_diagnostics import annotate,public_error
        started=time.monotonic();request_id=secrets.token_hex(8)
        try:
            result=self._collect(url)
            result["request_id"]=request_id
            for page in result.get("pages",[]):
                optional=page.get("optional_body",{})
                if "diagnostic" in optional:optional["diagnostic"].update(request_id=request_id,elapsed_ms=round((time.monotonic()-started)*1000))
            return result
        except AppError as error:raise public_error(annotate(error,started,self.optional_body,"worker",request_id,emit=True))

    def _collect(self,url):
        if self.collector:return self.collector.collect(url)
        request={'url':url}
        if self.optional_body:
            request['optional_body']=True
            if self.extraction_python:request['extraction_python']=str(self.extraction_python)
        result=run_website_worker(request,controlled=self.optional_body,stage='website')
        if 'error' in result:
            error=AppError(result['error'],result['status'],result['code']);error.details=result.get('details',{})
            raise error
        try:return result['result']
        except (TypeError,KeyError):raise AppError('采集结果格式无效',502,'website_unavailable')
    def preview(self,data):
        cid=field(data,'customer_id',32,True);customer=self.store.customer(cid);url=normalize_page_url(field(data,'url',500,True))
        if data.get('consent') is not True:raise AppError('请明确确认访问该公开官网；不会发送给模型',code='website_consent_required')
        with self.lock:
            self.jobs={k:v for k,v in self.jobs.items() if v['expires']>time.monotonic()}
            if len(self.jobs)>=5:raise AppError('待确认预览过多，请先取消或等待过期',429,'website_busy')
            jid=secrets.token_hex(16);self.jobs[jid]={'expires':time.monotonic()+600,'pending':True,'customer_id':cid}
        if not self.slots.acquire(False):
            with self.lock:self.jobs.pop(jid,None)
            raise AppError('已有采集正在进行，请稍后重试',429,'website_busy')
        try:
            result=self.collect(url);job={'id':jid,'customer_id':cid,'customer_name':customer['name'],'customer_version':customer['version'],'expires':time.monotonic()+600,'capture':result}
            with self.lock:self.jobs[jid]=job
            return dict(result,job_id=jid,customer_id=cid,customer_name=customer['name'],expires_in=600,saved=False)
        except Exception:
            with self.lock:self.jobs.pop(jid,None)
            raise
        finally:self.slots.release()
    def discard(self,data):
        jid=field(data,'job_id',32,True);cid=field(data,'customer_id',32,True)
        with self.lock:
            job=self.jobs.get(jid)
            if job and job['customer_id']!=cid:raise AppError('预览不属于该客户',409,'website_customer_mismatch')
            if job and job.get('pending'):raise AppError('采集尚未完成',409,'website_busy')
            self.jobs.pop(jid,None)
        return {'discarded':True}
    def confirm(self,data):
        jid=field(data,'job_id',32,True);cid=field(data,'customer_id',32,True)
        if data.get('consent') is not True:raise AppError('请在检查来源后明确确认保存',code='website_consent_required')
        with self.lock:
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE');existing=db.execute('SELECT customer_id,data FROM website_captures WHERE id=?',(jid,)).fetchone()
                if existing:
                    if existing[0]!=cid:raise AppError('资料不属于该客户',409,'website_customer_mismatch')
                    return dict(json.loads(existing[1]),replayed=True)
                job=self.jobs.get(jid)
                if not job or job['expires']<=time.monotonic() or job.get('pending'):raise AppError('预览已过期或尚未完成，请重新采集',410,'website_preview_expired')
                if job['customer_id']!=cid:raise AppError('预览不属于该客户',409,'website_customer_mismatch')
                row=db.execute('SELECT data FROM customers WHERE id=?',(cid,)).fetchone()
                if not row:raise AppError('客户不存在',404,'not_found')
                if json.loads(row[0])['version']!=job['customer_version']:raise AppError('客户资料已变更，请重新采集并核对关联',409,'website_customer_changed')
                capture=dict(job['capture'],id=jid,customer_id=cid,confirmed_at=now(),evidence_level=capture_evidence_level(job['capture']),independently_proven=False)
                lines=['客户公开官网背景资料（官网自述；非采购意向；不是独立证明）','人工确认仅表示来源与客户关联；不推断规模或决策人。']
                for page in capture['pages']:
                    lines.extend(['来源URL：'+page['url'],'抓取时间：'+page['fetched_at'],'页面标题：'+page['title']])
                    lines.extend('官网原文：'+x['quote'] for x in page['evidence'])
                text='\n'.join(lines)
                if len(text)>12000:raise AppError('证据超过保存上限',413,'website_too_large')
                record={'id':secrets.token_hex(8),'customer_id':cid,'kind':'反馈','text':text,'next_date':'','created_at':now(),'website_capture_id':jid,'evidence_level':capture_evidence_level(job['capture']),'source_observations':capture_observations(job['capture'])}
                capture['record_id']=record['id'];db.execute('INSERT INTO records VALUES(?,?,?)',(record['id'],cid,dump(record)));db.execute('INSERT INTO website_captures VALUES(?,?,?)',(jid,cid,dump(capture)))
            self.jobs.pop(jid,None)
        return dict(capture,replayed=False)
