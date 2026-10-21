"""Optional offline ranking, authoritative canonical preview, isolated staging only."""
import copy
import ctypes
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
from .common import AppError
from .context_knowledge import project_knowledge
from .model_analysis import evidence_catalog

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / 'rag_adapter'
sys.path[:0] = [str(ADAPTER/'integration-next'), str(ADAPTER/'answerability-next'), str(ADAPTER)]
from canonical import CanonicalSnapshot
from assessment import make_plan, assess
from rag.cleaned_v2 import adapt_semantic_v2, semantic_search

spec = importlib.util.spec_from_file_location('staging_preview_api', ADAPTER/'preview-next/api.py')
api_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api_module)
EnhancedPreviewAPI = api_module.EnhancedPreviewAPI
PreviewError = api_module.PreviewError
governance_issue = api_module.governance_issue

class LocalModelError(RuntimeError):
    def __init__(self,code,detail):
        super().__init__(code);self.code=code;self.detail=detail

def available_ram_mib():
    if sys.platform!='win32':return None
    class MemoryStatus(ctypes.Structure):
        _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong),('total_phys',ctypes.c_ulonglong),
            ('avail_phys',ctypes.c_ulonglong),('total_pagefile',ctypes.c_ulonglong),('avail_pagefile',ctypes.c_ulonglong),
            ('total_virtual',ctypes.c_ulonglong),('avail_virtual',ctypes.c_ulonglong),('avail_extended',ctypes.c_ulonglong)]
    state=MemoryStatus();state.length=ctypes.sizeof(state)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):return None
    return round(state.avail_phys/1024**2,2)

def local_rank_environment(cache):
    cache=Path(cache).resolve()
    directories={name:cache/name for name in ('tmp','profile','torch','inductor','hub','transformers')}
    for directory in directories.values():directory.mkdir(parents=True,exist_ok=True)
    env={key:os.environ[key] for key in ('SystemRoot','WINDIR','PATH') if key in os.environ}
    env.update(TEMP=str(directories['tmp']),TMP=str(directories['tmp']),USERPROFILE=str(directories['profile']),
        USERNAME='overseas_local_rank',TORCH_HOME=str(directories['torch']),TORCHINDUCTOR_CACHE_DIR=str(directories['inductor']),
        HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',HF_HUB_DISABLE_IMPLICIT_TOKEN='1',
        HF_HOME=str(cache),HF_HUB_CACHE=str(directories['hub']),TRANSFORMERS_CACHE=str(directories['transformers']),
        RAG_LOCAL_CACHE=str(cache),TOKENIZERS_PARALLELISM='false',PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1',
        OMP_NUM_THREADS='2',MKL_NUM_THREADS='2')
    return env

class EnhancedService:
    def __init__(self, store, local_config=None, model_runner=None, owner_scope=None, jobs_root=None, snapshot=None, production=False, read_policy=None):
        self.store=store
        self.jobs_root=Path(jobs_root) if jobs_root else ROOT/'isolation/jobs'
        self.production=production
        self.config = local_config or json.loads((ROOT/('enhanced-local.json' if production else 'staging-local.json')).read_text(encoding='utf-8'))
        if set(self.config) != {'local_python','embedding_dir','rerank_dir','local_model_cache','local_timeout_seconds'}:
            raise ValueError('Only explicit local model settings allowed; credentials are unsupported')
        timeout=self.config['local_timeout_seconds']
        if type(timeout) is not int or not 10 <= timeout <= 180:raise ValueError('Invalid bounded local model timeout')
        for key in ['local_python','embedding_dir','rerank_dir','local_model_cache']:
            if not isinstance(self.config[key],str) or not self.config[key]:raise ValueError('Explicit local path required')
        from .live_knowledge import LiveKnowledgeSnapshot
        if snapshot is not None:self.snapshot=snapshot
        else:
            if read_policy is None and production:read_policy=lambda document: True  # Single local owner app only.
            if read_policy is None:raise ValueError('dynamic index requires explicit trusted read policy')
            self.snapshot=LiveKnowledgeSnapshot(store,read_policy)
        self.corpus=self.snapshot.corpus() if hasattr(self.snapshot,'corpus') else json.loads((ADAPTER/'corpus/semantic-chunks.json').read_text(encoding='utf-8'))
        self.lock=threading.Lock();self.busy=False;self.last_fallback='';self.last_mode='not_run';self.preview_owners={};self.last_failure=None
        self.runner=model_runner or self.run_local
        def advisory(query,rows,selections):
            plan,unknown=make_plan(query,self.snapshot.registry)
            result=assess(plan,rows,selections,unknown)
            return {'experimental_coverage':result['evidence_answerability'],'unresolved_scope':result['unresolved_scope'],
                'field_states':[{'field':o['field'],'state':o['state']} for o in result['outcomes']],
                'not_an_answer_validity_verdict':True}
        self.api=EnhancedPreviewAPI(self.retrieve,self.snapshot,store.knowledge_selection,project_knowledge,evidence_catalog,advisory,enabled=False)

    def model_available(self):
        # Check only the explicitly configured executable and two model manifests.
        # Artifact/license hashes are verified by the frozen local provider before load.
        return Path(self.config['local_python']).is_file() and all(
            (Path(self.config[k])/'rag_model_manifest.json').is_file() for k in ['embedding_dir','rerank_dir'])

    def status(self):
        available=self.model_available()
        mode=self.last_mode if self.last_mode!='not_run' else ('local_components_present_not_verified' if available else 'keyword_fallback')
        notice='可选增强检索默认关闭；结果需要人工审阅，不代表答案已证实。'
        if not available:notice='本地检索模型未安装或路径不可用；使用关键词降级，不下载模型。'
        elif self.last_fallback:notice='本地模型本次不可用，已明确降级到关键词检索。'
        if self.last_fallback=='local_memory_low':notice='可用内存不足，已降级为关键词检索；释放本地应用内存后可手动重试，不会自动重跑。'
        return {'enabled':self.api.enabled,'default_enabled':False,'status':'enabled' if self.api.enabled else 'feature_disabled',
            'model_available':available,'retrieval_mode':mode,'notice':notice,'busy':self.busy,
            'dependency_import_verified':self.last_mode=='local_bm25_dense_rrf_rerank',
            'vector_inference_verified':self.last_mode=='local_bm25_dense_rrf_rerank',
            'last_fallback_code':self.last_fallback,'cloud_requests_disabled':not self.production,'web_search_disabled':not self.production,
            'enhanced_cloud_requests_disabled':True,
            'last_local_failure':self.last_failure,
            'generation_allowed':False,'selection_limit':6,'query_max_chars':200,'preview_ttl_seconds':900,
            'canonical_scope':'local_factory_knowledge' if self.production else 'isolated_staging_only','warm_worker':False}

    def settings(self,data):
        if not isinstance(data,dict) or set(data)!={'enabled'} or type(data['enabled']) is not bool:
            raise AppError('只能显式设置 enabled 布尔值',400,'invalid_input')
        if not self.lock.acquire(False):raise AppError('本地检索正在运行，请稍后再切换',409,'enhanced_busy')
        try:
            self.api.enabled=data['enabled']
            if not data['enabled']:self.api.previews.clear();self.preview_owners.clear()
            return self.status()
        finally:self.lock.release()

    def invoke(self,method,data,session_id):
        if not isinstance(session_id,str) or len(session_id)!=32:raise AppError('需要本地浏览器会话，请刷新页面',403,'staging_session_required')
        if method=='context_preview' and self.api.enabled:
            if not isinstance(data,dict) or set(data)!={'preview_id','selections'}:
                raise AppError('需要 preview_id 和显式 selections',400,'invalid_selection')
            pid=data['preview_id'];selections=data['selections']
            if not isinstance(pid,str) or not re.fullmatch('[a-f0-9]{32}',pid):
                raise AppError('preview_id 必须为有效预览标识',400,'invalid_selection')
            if not isinstance(selections,list) or not 1<=len(selections)<=6:
                raise AppError('请显式选择 1 至 6 个知识片段',400,'invalid_selection')
            for selection in selections:
                if not isinstance(selection,dict) or set(selection)!={'document_id','version','chunk_id'}:
                    raise AppError('知识选择必须使用原三元组',400,'invalid_selection')
                if type(selection['version']) is not int or selection['version']<1 or any(
                        not isinstance(selection[k],str) or not re.fullmatch('[a-f0-9]{16}',selection[k]) for k in ['document_id','chunk_id']):
                    raise AppError('知识选择标识或版本无效',400,'invalid_selection')
        if not self.lock.acquire(False):raise AppError('本地检索正在运行，请稍后手动重试',409,'enhanced_busy')
        self.busy=True
        try:
            if method=='context_preview' and isinstance(data,dict) and isinstance(data.get('preview_id'),str):
                owner=self.preview_owners.get(data['preview_id'])
                if owner is not None and owner!=session_id:raise AppError('预览属于其他本地会话，请重新搜索',403,'preview_session_mismatch')
            try:result=getattr(self.api,method)(data)
            except PreviewError as exc:
                status=410 if exc.code=='preview_expired' else 409 if exc.code=='knowledge_changed' else 503 if exc.code=='feature_disabled' else 400
                raise AppError(str(exc),status,exc.code)
            except (ValueError,KeyError,TypeError):
                raise AppError('本地权威知识校验未通过，未返回正文；请重新预览',409,'knowledge_changed')
            self.preview_owners={pid:owner for pid,owner in self.preview_owners.items() if pid in self.api.previews}
            if result.get('preview_id'):self.preview_owners[result['preview_id']]=session_id
            result['service']=dict(self.status(),busy=False)
            if self.production and 'governance' in result:result['governance']['canonical_scope']='local_factory_knowledge'
            return result
        finally:self.busy=False;self.lock.release()

    def search(self,data,session_id):return self.invoke('search',data,session_id)
    def context_preview(self,data,session_id):return self.invoke('context_preview',data,session_id)

    def lexical(self,query,limit):
        rows=self.snapshot();by_id={r.candidate_id:r for r in rows};candidates=[]
        for raw in (self.snapshot.corpus() if hasattr(self.snapshot,'corpus') else self.corpus):
            r=by_id.get(raw['candidate_chunk_id'])
            if not r or governance_issue(r):continue
            candidate=copy.deepcopy(raw);candidate['text']=r.text;candidate['_index_version']=r.version
            candidate['review']=dict(candidate['review'],enabled=r.enabled,review_status=r.review_status)
            parameters=self.snapshot.details[r.candidate_id]['chunk'].get('metadata',{}).get('structured_parameters',{})
            if parameters:
                candidate['_model_cells']=parameters['cells'];candidate['_primary_model_verbatim']=parameters['model_verbatim']
            candidates.append(candidate)
        result=semantic_search(adapt_semantic_v2(candidates),query,top_k=limit)
        return {'items':[{'candidate_chunk_id':i['candidate_chunk_id'],'score':i['score']} for i in result['items']],
            'method':'local_keyword_bm25_fallback','provenance':'actual keyword ranking on current authorized canonical staging KB; no embedding/rerank inference'}

    def retrieve(self,query,limit):
        self.last_fallback='';self.last_failure=None
        if not self.model_available():
            self.last_mode='keyword_fallback';self.last_fallback='local_models_missing'
            if hasattr(self.snapshot,'last_index_error'):self.snapshot.last_index_error=self.last_fallback
            return self.lexical(query,limit)
        try:result=self.runner(query,limit)
        except LocalModelError as exc:
            self.last_mode='keyword_fallback';self.last_fallback=exc.code;self.last_failure=exc.detail
            if hasattr(self.snapshot,'last_index_error'):self.snapshot.last_index_error=self.last_fallback
            return self.lexical(query,limit)
        except subprocess.TimeoutExpired:
            self.last_mode='keyword_fallback';self.last_fallback='local_model_timeout'
            if hasattr(self.snapshot,'last_index_error'):self.snapshot.last_index_error=self.last_fallback
            return self.lexical(query,limit)
        except (OSError,ValueError,RuntimeError,subprocess.SubprocessError):
            self.last_mode='keyword_fallback';self.last_fallback='local_model_unavailable'
            if hasattr(self.snapshot,'last_index_error'):self.snapshot.last_index_error=self.last_fallback
            return self.lexical(query,limit)
        self.last_mode='local_bm25_dense_rrf_rerank'
        return result

    def run_local(self,query,limit):
        jobs=self.jobs_root;jobs.mkdir(parents=True,exist_ok=True)
        job=jobs/uuid.uuid4().hex;job.mkdir()
        queries=job/'queries.json';output=job/'predictions.json'
        queries.write_text(json.dumps([{'case_id':'STAGING-MANUAL-PREVIEW','query':query,'state_fixture':None}],ensure_ascii=False),encoding='utf-8')
        # Only KB tables are copied into immutable per-job DB. Concurrent staging CRUD
        # cannot invalidate the runner's sealed hash; API rechecks live authority afterwards.
        from .live_knowledge import write_current_job
        if not hasattr(self.snapshot,'can_read'):raise LocalModelError('dynamic_index_unavailable',{'process_started':False})
        dbpath,corpus,mapping,job_snapshot,job_rows=write_current_job(self.store,job,self.snapshot.can_read)
        ready={'final':True,'production_import':False,'receipt_rolled_back':False,'database':str(dbpath),
            'canonical_chunk_map':str(mapping),'database_sha256':hashlib.sha256(dbpath.read_bytes()).hexdigest()}
        (job/'FINAL_READY.json').write_text(json.dumps(ready),encoding='utf-8')
        owner={'protocol':'local-owner-isolation-v1','principal':'local_owner','scope':'isolated_evaluation_only','database':ready['database'],
            'read_document_ids':job_snapshot.owner['read_document_ids'],'send_document_ids':[]}
        (job/'owner.json').write_text(json.dumps(owner),encoding='utf-8')
        cfg=json.loads((ADAPTER/'integration-next/config.json').read_text(encoding='utf-8'))
        cfg.update(embedding_dir=self.config['embedding_dir'],rerank_dir=self.config['rerank_dir'])
        (job/'config.json').write_text(json.dumps(cfg),encoding='utf-8')
        cache=Path(self.config['local_model_cache']).resolve();cache.mkdir(parents=True,exist_ok=True)
        env=local_rank_environment(cache)
        command=[self.config['local_python'],str(ROOT/'backend/offline_runner.py'),str(ADAPTER/'integration-next/run_qa_pipeline_next.py'),
            '--queries',str(queries),'--output',str(output),'--db',ready['database'],
            '--final-ready',str(job/'FINAL_READY.json'),'--lineage',ready['canonical_chunk_map'],
            '--owner-scope',str(job/'owner.json'),'--corpus',str(corpus),
            '--config',str(job/'config.json'),'--phase','e5']
        # Sequential CPU phases; process timeout and offline providers bound local work.
        phases=[]
        for phase,cmd in [('e5',command),('rerank',[command[0],str(ROOT/'backend/offline_runner.py'),str(ADAPTER/'preview-next/rerank_local.py'),'--input',str(output.with_suffix('.e5-candidates.json')),
                '--output',str(output),'--config',str(job/'config.json'),'--limit',str(limit)])]:
            phase_start=time.monotonic()
            ram=available_ram_mib();required=cfg['minimum_start_ram_mib']
            if ram is not None and ram<required:
                detail={'phase':phase,'available_ram_mib':ram,'required_start_ram_mib':required,'process_started':False}
                phases.append(dict(detail,status='rejected_preflight',error_code='local_memory_low',start_monotonic=phase_start,end_monotonic=time.monotonic()))
                (job/'PHASES.json').write_text(json.dumps(phases,indent=2),encoding='utf-8')
                with (job/'local-run.log').open('a',encoding='utf-8') as log:log.write(json.dumps({'error_code':'local_memory_low',**detail})+'\n')
                raise LocalModelError('local_memory_low',detail)
            try:
                completed=subprocess.run(cmd,check=False,cwd=str(ADAPTER),env=env,stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=self.config['local_timeout_seconds'],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            except subprocess.TimeoutExpired as exc:
                with (job/'local-run.log').open('ab') as log:log.write((exc.stdout or b'')+(exc.stderr or b''))
                detail={'phase':phase,'process_started':True,'timeout_seconds':self.config['local_timeout_seconds']}
                phases.append(dict(detail,status='timeout',error_code='local_model_timeout',start_monotonic=phase_start,end_monotonic=time.monotonic()))
                (job/'PHASES.json').write_text(json.dumps(phases,indent=2),encoding='utf-8')
                raise LocalModelError('local_model_timeout',detail)
            except OSError:
                detail={'phase':phase,'process_started':False}
                phases.append(dict(detail,status='launch_failed',error_code='local_runtime_unavailable',start_monotonic=phase_start,end_monotonic=time.monotonic()))
                (job/'PHASES.json').write_text(json.dumps(phases,indent=2),encoding='utf-8')
                with (job/'local-run.log').open('a',encoding='utf-8') as log:log.write(json.dumps({'error_code':'local_runtime_unavailable',**detail})+'\n')
                raise LocalModelError('local_runtime_unavailable',detail)
            raw=completed.stdout+completed.stderr
            with (job/'local-run.log').open('ab') as log:log.write(raw)
            phases.append({'phase':phase,'start_monotonic':phase_start,'end_monotonic':time.monotonic(),'cpu_only':True,'outbound_network_denied':True,
                'available_ram_before_mib':ram,'exit_code':completed.returncode,'status':'ok' if completed.returncode==0 else 'failed'})
            (job/'PHASES.json').write_text(json.dumps(phases,indent=2),encoding='utf-8')
            if completed.returncode:
                output_text=raw.decode('utf-8',errors='replace')
                code='local_memory_low' if 'RAM insufficient' in output_text or 'RAM reserve insufficient' in output_text else 'local_dependency_missing' if 'ModuleNotFoundError:' in output_text else 'local_model_integrity' if 'model artifact hash mismatch' in output_text else 'local_model_process_failed'
                detail={'phase':phase,'process_started':True,'exit_code':completed.returncode}
                if code=='local_memory_low':
                    detail['available_ram_after_process_mib']=available_ram_mib()
                    detail['required_running_ram_mib' if 'RAM reserve insufficient' in output_text else 'required_start_ram_mib']=cfg['minimum_running_ram_mib'] if 'RAM reserve insufficient' in output_text else required
                phases[-1]['error_code']=code
                (job/'PHASES.json').write_text(json.dumps(phases,indent=2),encoding='utf-8')
                raise LocalModelError(code,detail)
        if output.stat().st_size>512*1024:raise ValueError('bounded local ranking output required')
        intermediate=json.loads(output.with_suffix('.e5-candidates.json').read_text(encoding='utf-8'))
        touched=[i['candidate_chunk_id'] for c in intermediate['cases'] for i in c['candidates']]
        self.snapshot.mark_vectors(touched,job_rows)
        return json.loads(output.read_text(encoding='utf-8'))
