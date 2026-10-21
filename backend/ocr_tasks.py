"""Bounded local PDF staging. Reuses KnowledgeService.confirm; never auto-publishes."""
import concurrent.futures,copy,datetime as dt,hashlib,json,pathlib,re,secrets,threading,time
from .common import AppError,field
from .knowledge import CATEGORIES

class OCRTasks:
    MAX_BYTES=40*1024*1024;MAX_JOBS=4;TTL=7200;MAX_RESULT=16*1024*1024;MAX_STAGING=256*1024*1024
    def __init__(self,knowledge,root,runner=None):
        self.knowledge=knowledge;self.root=pathlib.Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.closed=False;self.close_complete=False;self.close_lock=threading.Lock();self.owner=None
        self.runner=runner;self.lock=threading.RLock();self.idle=threading.Condition(self.lock)
        self.executor=concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.jobs={};self.events={};self.uploading=set();self.running=set()
        # The default unavailable facade neither owns nor mutates an enabled
        # service's staging files. Multiple disabled HTTP servers remain compatible.
        if runner is None:return
        # One service owns a staging root; reservations cannot race across processes.
        self.owner=(self.root/'.owner.lock').open('a+b')
        try:
            self.owner.seek(0)
            if not self.owner.read(1):self.owner.write(b'0');self.owner.flush()
            self.owner.seek(0)
            import os
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.owner.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.owner.close();raise AppError('Staging directory already has an owner',503,'staging_busy')
        try:
            for folder in self.root.iterdir():
                if folder.is_dir() and re.fullmatch('[a-f0-9]{16}',folder.name) and (folder/'task.json').is_file():
                    task=json.loads((folder/'task.json').read_text(encoding='utf8'))
                    if task['state'] in ('uploading','queued','running'):
                        task.update(state='failed',error={'code':'worker_interrupted','message':'服务重启中断任务，请重新上传；未入库'})
                    self.jobs[folder.name]=task;self.events[folder.name]=threading.Event();self._save(task)
        except Exception:
            self.owner.close();self.executor.shutdown(wait=True)
            raise
    def meta(self):
        available=bool(not self.closed and self.runner and self.runner.available())
        return dict(protocol='ocr-upload-review-v1',available=available,reason='' if available else '未配置可用的本地PDFium/Docling运行时与离线模型；原有文本导入仍可用',
          max_file_bytes=self.MAX_BYTES,max_active_jobs=self.MAX_JOBS,max_pages_per_task=1,formats=['pdf'],ttl_seconds=self.TTL,
          worker_count=1,cloud_ocr=False,auto_publish=False,source_review_is_independent_proof=False)
    def _save(self,task):
        folder=self.root/task['job_id'];folder.mkdir(exist_ok=True)
        payload=json.dumps(task,ensure_ascii=False).encode('utf8')
        if len(payload)>self.MAX_RESULT:raise AppError('任务持久化草稿超限',422,'output_limit')
        pending=folder/'task.json.tmp';pending.write_bytes(payload);pending.replace(folder/'task.json')
    def _capacity(self,extra_pdf=0,extra_output=0):
        """Actual files plus remaining durable/in-flight reservations, under the service lock."""
        pdf=total=0;sizes={}
        for path in self.root.rglob('*'):
            if not path.is_file():continue
            try:size=path.stat().st_size
            except FileNotFoundError:continue
            total+=size
            relative=path.relative_to(self.root)
            is_pdf=len(relative.parts)==2 and re.fullmatch('[a-f0-9]{16}',relative.parts[0]) and path.name in ('source.pdf','source.pdf.part')
            if is_pdf:pdf+=size
            folder=path.relative_to(self.root).parts[0]
            slot=sizes.setdefault(folder,[0,0]);slot[0 if is_pdf else 1]+=size
        for jid,task in self.jobs.items():
            active=(task['expires']>time.time() and task['state'] in ('awaiting_upload','uploading','queued','running','needs_review','reviewed')) or jid in self.uploading or jid in self.running
            if not active:continue
            actual_pdf,actual_output=sizes.get(jid,[0,0])
            pdf_delta=max(0,task.get('reserved_pdf_bytes',self.MAX_BYTES)-actual_pdf)
            # Worker<=MAX_RESULT, task JSON<=MAX_RESULT, old/new atomic task files
            # may overlap; the fourth unit covers request/receipt and diagnostics.
            output_delta=max(0,task.get('reserved_output_bytes',4*self.MAX_RESULT)-actual_output)
            pdf+=pdf_delta;total+=pdf_delta+output_delta
        return dict(pdf_bytes=pdf+extra_pdf,staging_bytes=total+extra_pdf+extra_output)
    def _task(self,jid,allow_expired=False):
        if self.closed:raise AppError('OCR service is closed',503,'service_closed')
        if not isinstance(jid,str) or not re.fullmatch('[a-f0-9]{16}',jid) or jid not in self.jobs:raise AppError('解析任务不存在',404,'task_not_found')
        task=self.jobs[jid]
        if not allow_expired and task['expires']<=time.time() and task['state']!='confirmed':raise AppError('解析任务已过期，请重新上传',410,'task_expired')
        return task
    def status(self,jid):
        with self.lock:
            t=self._task(jid)
            result=copy.deepcopy({k:t[k] for k in ('job_id','state','title','filename','pages','expires','error','source_sha256','source_bytes','revision','receipt','worker_receipt') if k in t})
            result.update(parse_state='succeeded' if t['state'] in ('needs_review','reviewed','confirmed') else t['state'],human_review_state='reviewed' if t['state'] in ('reviewed','confirmed') else 'pending',knowledge_review_status='unverified',publication_state='imported_unverified' if t['state']=='confirmed' else 'not_imported',index_state='not_requested',scope='selected_pages_only',whole_document_complete=False)
            return result
    def listing(self):
        with self.lock:
            results=[]
            for t in self.jobs.values():
                if t['expires']>time.time():results.append(self.status(t['job_id']))
                elif (self.root/t['job_id']/'source.pdf').exists():results.append({'job_id':t['job_id'],'state':'expired','can_cancel':True,'title':t['title']})
            return {'tasks':results[:20]}
    def create(self,data):
        if not self.meta()['available']:raise AppError(self.meta()['reason'],503,'parser_unavailable')
        title=field(data,'title',200,True);category=field(data,'category',30,True);filename=field(data,'filename',200,True)
        if category not in CATEGORIES:raise AppError('资料分类无效')
        if pathlib.Path(filename).suffix.lower()!='.pdf' or any(c in filename for c in '/\\:\x00') or any(ord(c)<32 for c in filename):raise AppError('仅支持安全文件名的PDF')
        pages=data.get('pages')
        if not isinstance(pages,list) or len(pages)!=1 or type(pages[0]) is not int or not 1<=pages[0]<=80:raise AppError('首版必须明确选择一个物理PDF页（1–80）')
        did=field(data,'document_id',32);expected=data.get('expected_version')
        if did and (type(expected) is not int or self.knowledge.store.knowledge_document(did)['version']!=expected):raise AppError('资料版本已改变',409,'knowledge_changed')
        expires_on=field(data,'expires_on',10)
        if expires_on:
            try:dt.date.fromisoformat(expires_on)
            except ValueError:raise AppError('有效期应为YYYY-MM-DD')
        with self.lock:
            if self.closed:raise AppError('OCR service is closed',503,'service_closed')
            active=[t for t in self.jobs.values() if t['expires']>time.time() and t['state'] not in ('confirmed','cancelled','failed')]
            # Retained originals also consume capacity; never silently delete a live task.
            capacity=self._capacity(self.MAX_BYTES,4*self.MAX_RESULT)
            if len(active)>=self.MAX_JOBS or capacity['pdf_bytes']>self.MAX_JOBS*self.MAX_BYTES or capacity['staging_bytes']>self.MAX_STAGING:raise AppError('暂存容量已满，请取消不需要的任务或由管理员归档已入库解析证据',429,'capacity')
            jid=secrets.token_hex(8)
            task=dict(job_id=jid,state='awaiting_upload',title=title,category=category,filename=filename,pages=pages,
              document_id=did,expected_version=expected,expires_on=expires_on,source_company=field(data,'source_company',200),expires=time.time()+self.TTL,revision=0,reserved_pdf_bytes=self.MAX_BYTES,reserved_output_bytes=4*self.MAX_RESULT)
            self.jobs[jid]=task;self.events[jid]=threading.Event()
            try:self._save(task)
            except Exception:self.jobs.pop(jid);self.events.pop(jid);raise
            return dict(self.status(jid),upload_url='/api/knowledge/parse/upload?job_id='+jid)
    def upload(self,jid,stream,length,set_timeout=None):
        if type(length) is not int or not 1<=length<=self.MAX_BYTES:raise AppError('PDF须为1字节至40MiB',413,'upload_too_large')
        with self.lock:
            task=self._task(jid)
            if task['state']!='awaiting_upload':raise AppError('任务不可重复上传',409,'task_state')
            task['reserved_pdf_bytes']=length
            capacity=self._capacity()
            if capacity['pdf_bytes']>self.MAX_JOBS*self.MAX_BYTES or capacity['staging_bytes']>self.MAX_STAGING:raise AppError('上传预算不足，请取消其他任务',429,'capacity')
            task['state']='uploading';self._save(task);self.uploading.add(jid)
        folder=self.root/jid;pending=folder/'source.pdf.part';digest=hashlib.sha256();remaining=length;deadline=time.monotonic()+120
        try:
            with pending.open('xb') as output:
                while remaining:
                    budget=deadline-time.monotonic()
                    if budget<=0:raise AppError('上传超过120秒',408,'upload_timeout')
                    if set_timeout:set_timeout(min(30,budget))
                    block=getattr(stream,'read1',stream.read)(min(65536,remaining))
                    if not block:raise AppError('上传未完成',400,'truncated_upload')
                    if self.events[jid].is_set():raise AppError('任务已取消',409,'cancelled')
                    output.write(block);digest.update(block);remaining-=len(block)
            with pending.open('rb') as source:
                if source.read(5)!=b'%PDF-':raise AppError('不是PDF文件',400,'invalid_pdf')
            with self.lock:
                task=self._task(jid)
                if self.events[jid].is_set():raise AppError('任务已取消',409,'cancelled')
                pending.replace(folder/'source.pdf');task.update(state='queued',source_sha256=digest.hexdigest(),source_bytes=length);self._save(task)
                self.executor.submit(self._run,jid)
            return self.status(jid)
        except Exception:
            pending.unlink(missing_ok=True)
            with self.lock:
                if task['state']!='cancelled':task.update(state='failed',error={'code':'upload_failed','message':'上传失败，未入库'});self._save(task)
            raise
        finally:
            with self.idle:self.uploading.discard(jid);self.idle.notify_all()
    def _run(self,jid):
        with self.lock:
            task=self.jobs[jid]
            if task['state']!='queued':return
            if task['expires']<=time.time():task.update(state='failed',error={'code':'task_expired','message':'排队任务已过期'});self._save(task);return
            task['state']='running';self._save(task);self.running.add(jid);request=copy.deepcopy(task)
        try:
            draft=self.runner(self.root/jid/'source.pdf',request,self.events[jid])
            if len(json.dumps(draft,ensure_ascii=False).encode())>self.MAX_RESULT:raise AppError('解析产物超限',422,'output_limit')
            if draft.get('source_sha256')!=task['source_sha256'] or draft.get('pdf_page')!=task['pages'][0] or draft.get('processing',{}).get('ocr_performed') is not True:raise AppError('解析来源或页码不匹配',422,'parser_identity')
            if draft.get('review_state')!='unverified' or draft.get('cloud_context_approved') is not False:raise AppError('解析审核状态无效',422,'parser_identity')
            if not re.fullmatch('[a-f0-9]{64}',str(draft.get('raw_artifact_sha256',''))) or not isinstance(draft.get('engine_version'),str):raise AppError('缺少原始解析哈希或版本',422,'parser_identity')
            self._raw_artifact(task,draft)
            draft['attempt_id']=jid
            blocks=self._blocks(draft)
            if not blocks:raise AppError('未识别到可复核文本，不视作确认空白页',422,'no_text_detected')
            with self.lock:
                if self.events[jid].is_set():return
                artifact_hash=hashlib.sha256(json.dumps(draft,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
                task.update(state='needs_review',draft=draft,artifact_sha256=artifact_hash,blocks=blocks,revision=task['revision']+1,worker_receipt=self._diagnostics(jid));self._save(task)
        except Exception as error:
            with self.lock:
                if self.events[jid].is_set():return
                for key in ('draft','blocks','review'):task.pop(key,None)
                task.update(state='failed',error={'code':error.code if isinstance(error,AppError) else 'parser_failed','message':str(error) if isinstance(error,AppError) else '本地解析失败；请检查组件版本或资源，未入库'},worker_receipt=self._diagnostics(jid));self._save(task)
        finally:
            if self.events[jid].is_set():self._remove_staged_source(jid)
            with self.idle:self.running.discard(jid);self.idle.notify_all()
    def _remove_staged_source(self,jid):
        try:(self.root/jid/'source.pdf').unlink(missing_ok=True)
        except OSError:pass
    def _diagnostics(self,jid):
        path=self.root/jid/'worker-receipt.json'
        if not path.is_file() or path.stat().st_size>12000:return {}
        try:receipt=json.loads(path.read_text(encoding='utf8'))
        except (OSError,ValueError):return {}
        return {key:receipt[key] for key in ('exit_code','wall_seconds','termination_reason','peak_job_commit_bytes','commit_cap_bytes') if key in receipt}
    def _invalidate(self,task,code):
        task.update(state='failed',revision=task['revision']+1,error={'code':code,'message':'Staged evidence changed; upload and review again'})
        task.pop('review',None);self._save(task)
        raise AppError('Staged evidence changed; upload and review again',409,code)
    def _source(self,task):
        try:
            with (self.root/task['job_id']/'source.pdf').open('rb') as handle:raw=handle.read(self.MAX_BYTES+1)
        except OSError:self._invalidate(task,'source_changed')
        if len(raw)!=task['source_bytes'] or hashlib.sha256(raw).hexdigest()!=task['source_sha256']:self._invalidate(task,'source_changed')
        return raw
    def _raw_artifact(self,task,draft):
        path=self.root/task['job_id']/'document.json'
        try:
            with path.open('rb') as handle:raw=handle.read(self.MAX_RESULT+1)
        except OSError:raise AppError('Raw parser artifact unavailable',422,'parser_identity')
        if len(raw)>self.MAX_RESULT or hashlib.sha256(raw).hexdigest()!=draft.get('raw_artifact_sha256'):raise AppError('Raw parser artifact changed',422,'parser_identity')
    def _evidence(self,task):
        raw=self._source(task)
        draft=task['draft']
        digest=hashlib.sha256(json.dumps(draft,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if digest!=task['artifact_sha256'] or self._blocks(draft)!=task['blocks']:self._invalidate(task,'draft_changed')
        try:self._raw_artifact(task,draft)
        except AppError:self._invalidate(task,'parser_artifact_changed')
        return raw
    def _blocks(self,draft):
        blocks=[]
        for item in draft.get('text_blocks',[]):
            if item.get('text_raw','').strip():blocks.append(dict(id=item['id'],text_raw=item['text_raw'],location=dict(page=draft['pdf_page'],bbox_pt=item.get('bbox_pt'),provenance_raw=item.get('provenance_raw')),type='text'))
        for table in draft.get('tables',[]):
            for cell in table['cells']:
                if cell['text_raw'].strip():blocks.append(dict(id=cell['id'],text_raw=cell['text_raw'],type='table_cell',location=dict(page=draft['pdf_page'],table_id=table['id'],row=cell['row'],col=cell['col'],row_span=cell['row_span'],col_span=cell['col_span'],bbox_pt=cell.get('bbox_pt'))))
        if len(blocks)>400 or len({b['id'] for b in blocks})!=len(blocks) or sum(len(b['text_raw']) for b in blocks)>120000:raise AppError('解析块超限或身份重复',422,'output_limit')
        return blocks
    def draft(self,jid):
        with self.lock:
            task=self._task(jid)
            if task['state'] not in ('needs_review','reviewed'):raise AppError('草稿尚不可复核',409,'task_state')
            self._evidence(task)
            return dict(self.status(jid),artifact_sha256=task['artifact_sha256'],draft=copy.deepcopy(task['draft']),blocks=copy.deepcopy(task['blocks']),review=copy.deepcopy(task.get('review')))
    def original(self,jid):
        with self.lock:
            task=self._task(jid);path=self.root/jid/'source.pdf'
            if task['state'] in ('confirmed','cancelled','awaiting_upload','uploading') or not path.is_file():raise AppError('暂存原文不可用；已入库版本请使用现有原文接口',404,'original_unavailable')
            # Return the exact checked snapshot, so replacement after validation cannot alter download.
            import io
            return io.BytesIO(self._source(task))
    def review(self,data):
        with self.lock:
            task=self._task(field(data,'job_id',32,True))
            if task['state'] not in ('needs_review','reviewed') or type(data.get('revision')) is not int or data['revision']!=task['revision'] or data.get('artifact_sha256')!=task['artifact_sha256']:raise AppError('草稿已改变，请重新复核',409,'draft_changed')
            self._evidence(task)
            if data.get('acknowledge_uncertainties') is not True:raise AppError('需确认不确定项仍未核实')
            accepted=data.get('accepted_block_ids');edits=data.get('edits',{})
            by_id={b['id']:b for b in task['blocks']}
            if not isinstance(accepted,list) or not accepted or len(accepted)>400 or len(set(accepted))!=len(accepted) or any(x not in by_id for x in accepted):raise AppError('复核块选择无效')
            if not isinstance(edits,dict) or any(k not in accepted or not isinstance(v,str) or not v.strip() or len(v)>12000 for k,v in edits.items()):raise AppError('人工修改无效')
            if sum(len(edits.get(k,by_id[k]['text_raw'])) for k in accepted)>120000:raise AppError('复核文本超限')
            token=secrets.token_hex(16)
            task.update(state='reviewed',revision=task['revision']+1,review=dict(accepted_block_ids=accepted,edits=edits,note=field(data,'note',1000,True),token=token,reviewed_at=time.time(),artifact_sha256=task['artifact_sha256'],parser_version=task['draft'].get('engine_version')))
            self._save(task);return dict(self.status(task['job_id']),review_token=token,notice='复核及解析成功均不代表知识核实；确认入库后仍为unverified')
    def confirm(self,data):
        jid=field(data,'job_id',32,True)
        if data.get('consent') is not True:raise AppError('需明确确认入库')
        with self.lock:
            task=self._task(jid)
            if task['state']=='confirmed':return dict(task['receipt'],replayed=True)
            if task['state']!='reviewed' or type(data.get('revision')) is not int or data['revision']!=task['revision'] or not isinstance(data.get('review_token'),str) or not secrets.compare_digest(data['review_token'],task['review']['token']):raise AppError('需先人工复核当前草稿',409,'review_required')
            original=self._evidence(task)
            by_id={b['id']:b for b in task['blocks']};review=task['review'];chunks=[]
            for bid in review['accepted_block_ids']:
                block=by_id[bid];text=review['edits'].get(bid,block['text_raw'])
                # A reviewed cell/block stays intact; do not hard-split model/value bindings.
                chunks.append(dict(text=text,location=block['location'],metadata=dict(parser_block_id=bid,source_sha256=task['source_sha256'],parser_artifact_sha256=task['artifact_sha256'],raw_parser_artifact_sha256=task['draft']['raw_artifact_sha256'],parser_version=task['draft'].get('engine_version'),parser_attempt_id=jid,text_origin='manual_transcription' if bid in review['edits'] else 'ocr_transcription',source_text_raw=block['text_raw'],requires_review=True,independently_proven=False)))
            extracted=dict(extracted_text='\n'.join(c['text'] for c in chunks),page_count=task['draft'].get('source_page_count'),paragraph_count=len(chunks),chunks=chunks,
              parser=task['draft'].get('engine','local_ocr'),processing={'ocr_performed':True,'requested_pages':task['pages'],'scope':'selected_pages_only'},parser_draft=task['draft'],review_manifest=review,warnings=['仅解析明确选择的物理页；未处理页未完成','OCR和人工复核均不构成独立产品事实证明'])
            legacy={k:task[k] for k in ('job_id','title','filename','category','expires_on','source_company','document_id','expected_version','expires')}
            legacy.update(original=original,extracted=extracted)
            with self.knowledge.lock:self.knowledge.jobs[jid]=legacy
            receipt=self.knowledge.confirm({'job_id':jid,'consent':True})
            task.update(state='confirmed',receipt=receipt);self._save(task)
            self._remove_staged_source(jid)
            return receipt
    def cancel(self,jid):
        with self.lock:
            task=self._task(jid,allow_expired=True)
            if task['state']=='confirmed':raise AppError('已入库版本不能用取消解析删除',409,'task_state')
            was_running=task['state']=='running'
            self.events[jid].set();task['state']='cancelled';task.pop('review',None);self._save(task)
            if not was_running:self._remove_staged_source(jid)
            if task['expires']<=time.time():return {'job_id':jid,'state':'cancelled','expired':True,'publication_state':'not_imported'}
            return self.status(jid)
    def close(self):
        # Serialize repeated/concurrent close. The OS lock stays held until all
        # asynchronous workers and upload writers have stopped touching this root.
        with self.close_lock:
            with self.lock:
                if self.close_complete:return
                self.closed=True
                errors=[]
                for jid,event in self.events.items():
                    event.set()
                    task=self.jobs[jid]
                    if task['state'] in ('uploading','queued','running'):
                        task.update(state='cancelled',revision=task['revision']+1)
                        task.pop('review',None)
                        try:self._save(task)
                        except Exception as error:errors.append(error)
                        if jid not in self.uploading and jid not in self.running:self._remove_staged_source(jid)
            self.executor.shutdown(wait=True)
            with self.idle:
                while self.uploading:self.idle.wait()
                if self.owner is not None and not self.owner.closed:self.owner.close()
                self.close_complete=True
            if errors:raise errors[0]
