"""Local, explicit-confirmation factory documents. No network or model calls."""
import base64
import datetime as dt
import hashlib
import importlib.util
import json
import re
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from .common import AppError, dump, field, norm, now, paging, page_result

CATEGORIES=['product','capability','certification','case','sales_rule']
REVIEW_LABELS={'unverified':'需核实','source_checked':'已核对原文（企业自述）','confirmed':'用户确认（非独立证明）','rejected':'拒绝用于分析'}

def review_data(data):
    reason=field(data,'review_reason',2000,True)
    fields=data.get('reviewed_fields')
    if not isinstance(fields,list) or not 1<=len(fields)<=30 or any(not isinstance(x,str) or not x.strip() or len(x)>100 or any(ord(c)<32 for c in x) for x in fields):raise AppError('请列出已核对的具体字段，最多30项')
    source=data.get('review_source')
    if not isinstance(source,dict) or set(source)!={'filename','sha256','pdf_pages','printed_pages'}:raise AppError('原文核对需文件名、源校验值及页码')
    filename=field(source,'filename',200,True);sha=field(source,'sha256',64,True)
    if any(c in filename for c in '/\\:\x00') or not re.fullmatch('[a-f0-9]{64}',sha):raise AppError('原文来源文件名或校验值无效')
    pages=source['pdf_pages'];printed=source['printed_pages']
    if not isinstance(pages,list) or not 1<=len(pages)<=80 or any(type(p) is not int or not 1<=p<=80 for p in pages):raise AppError('PDF页码应为1至80的整数列表')
    if not isinstance(printed,list) or len(printed)>80 or any(not isinstance(p,str) or len(p)>100 for p in printed):raise AppError('印刷页码格式无效')
    return {'reviewed_fields':list(dict.fromkeys(x.strip() for x in fields)),'reason':reason,'source':dict(source,filename=filename,sha256=sha),'reviewed_at':now(),'evidence_level':'publisher_self_statement','independently_proven':False,'scope_notice':'只核对列明字段的原文；不证明技术能力、认证有效性、专利权属、兼容性或现时供货。'}

class KnowledgeStore:
    def init_knowledge(self,db):
        db.executescript('CREATE TABLE IF NOT EXISTS kb_documents(id TEXT PRIMARY KEY,data TEXT);CREATE TABLE IF NOT EXISTS kb_versions(document_id TEXT,version INTEGER,data TEXT,original BLOB,PRIMARY KEY(document_id,version));CREATE TABLE IF NOT EXISTS kb_chunks(id TEXT PRIMARY KEY,document_id TEXT,version INTEGER,data TEXT);CREATE INDEX IF NOT EXISTS kb_chunk_version ON kb_chunks(document_id,version);CREATE TABLE IF NOT EXISTS kb_receipts(id TEXT PRIMARY KEY,data TEXT);')
        db.executescript('CREATE TABLE IF NOT EXISTS kb_reviews(id TEXT PRIMARY KEY,document_id TEXT NOT NULL,version INTEGER NOT NULL,revision INTEGER NOT NULL,data TEXT NOT NULL);CREATE INDEX IF NOT EXISTS kb_reviews_document ON kb_reviews(document_id,version);CREATE TRIGGER IF NOT EXISTS kb_reviews_immutable_update BEFORE UPDATE ON kb_reviews BEGIN SELECT RAISE(ABORT,"review audit is immutable");END;CREATE TRIGGER IF NOT EXISTS kb_reviews_immutable_delete BEFORE DELETE ON kb_reviews BEGIN SELECT RAISE(ABORT,"review audit is immutable");END;')
        for did,raw in db.execute('SELECT id,data FROM kb_documents').fetchall():
            if not db.execute('SELECT 1 FROM kb_reviews WHERE document_id=?',(did,)).fetchone():
                d=json.loads(raw);self.knowledge_audit(db,d,None,'migration_snapshot',{'notice':'保留旧状态的迁移快照；不是新增核实或提高证据等级。'})
    def knowledge_audit(self,db,d,old,action,review=None):
        aid=secrets.token_hex(16);r={'id':aid,'document_id':d['id'],'version':d['version'],'revision':d['revision'],'created_at':now(),'action':action,'before':{'review_status':old['review_status'],'enabled':old['enabled'],'version':old['version'],'revision':old['revision']} if old else None,'after':{'review_status':d['review_status'],'enabled':d['enabled']},'review':review,'independently_proven':False}
        db.execute('INSERT INTO kb_reviews VALUES(?,?,?,?,?)',(aid,d['id'],d['version'],d['revision'],dump(r)))
    def knowledge_documents(self,q):
        p,s=paging(q);term=norm(field(q,'q',200));category=field(q,'category',30)
        if category and category not in CATEGORIES:raise AppError('资料分类无效')
        review=field(q,'review_status',30)
        if review and review not in REVIEW_LABELS:raise AppError('核对状态筛选无效')
        with self.db() as db:all_items=[json.loads(r[0]) for r in db.execute('SELECT data FROM kb_documents ORDER BY rowid DESC')]
        items=[self.knowledge_warnings(d,all_items) for d in all_items if (q.get('include_disabled')=='1' or d['enabled']) and (not category or d['category']==category) and (not review or review==d['review_status']) and (not term or term in norm(d['title']+' '+d['filename']))]
        return page_result(items[(p-1)*s:p*s],len(items),p,s)
    def knowledge_warnings(self,d,documents=None):
        d=dict(d);d['current_version']=d['version'];d['expired']=bool(d.get('expires_on') and d['expires_on']<dt.date.today().isoformat())
        d['review_label']=REVIEW_LABELS.get(d['review_status'],'需核实');d['independently_proven']=False
        d.setdefault('review_info',None)
        if documents is None:
            with self.db() as db:documents=[json.loads(r[0]) for r in db.execute('SELECT data FROM kb_documents')]
        d['conflict']=any(x['id']!=d['id'] and x['enabled'] and x['review_status']!='rejected' and x['category']==d['category'] and norm(x['title'])==norm(d['title']) for x in documents)
        d['warnings']=(['资料尚未核实'] if d['review_status']=='unverified' else [])+(['资料已过期'] if d['expired'] else [])+(['同名同类资料可能冲突，请人工核对'] if d['conflict'] else [])
        if d['review_status']=='source_checked':d['warnings'].append('仅已核对列明原文；仍是企业自述，不是能力、认证或权属证明')
        if d['review_status']=='confirmed':d['warnings'].append('用户确认不等于独立有效证明')
        return d
    def knowledge_document(self,did,version=None):
        with self.db() as db:
            row=db.execute('SELECT data FROM kb_documents WHERE id=?',(did,)).fetchone()
            if not row:raise AppError('资料不存在',404,'not_found')
            doc=json.loads(row[0]);v=doc['version'] if version is None else version
            row=db.execute('SELECT data FROM kb_versions WHERE document_id=? AND version=?',(did,v)).fetchone()
            if not row:raise AppError('资料版本不存在',404,'not_found')
            content=json.loads(row[0]);chunks=[json.loads(r[0]) for r in db.execute('SELECT data FROM kb_chunks WHERE document_id=? AND version=? ORDER BY rowid',(did,v))]
            versions=[json.loads(r[0]) for r in db.execute('SELECT data FROM kb_versions WHERE document_id=? ORDER BY version DESC',(did,))]
            reviews=[json.loads(r[0]) for r in db.execute('SELECT data FROM kb_reviews WHERE document_id=? AND version=? ORDER BY rowid DESC',(did,v))]
        return dict(self.knowledge_warnings(doc),selected_version=v,content=content,chunks=chunks,review_history=reviews,versions=[{k:x[k] for k in ['version','title','created_at','filename']} for x in versions])
    def knowledge_original(self,did,v):
        self.knowledge_document(did,v)
        with self.db() as db:r=db.execute('SELECT original,data FROM kb_versions WHERE document_id=? AND version=?',(did,v)).fetchone()
        return r[0],json.loads(r[1])['filename']
    def knowledge_status(self,data):
        did=field(data,'document_id',32,True)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT data FROM kb_documents WHERE id=?',(did,)).fetchone()
            if not row:raise AppError('资料不存在',404,'not_found')
            d=json.loads(row[0])
            old=dict(d)
            if type(data.get('expected_version')) is not int or data['expected_version']!=d['version'] or ('expected_revision' in data and data['expected_revision']!=d['revision']):raise AppError('资料已更新，请刷新核对',409,'knowledge_changed')
            if 'enabled' not in data and 'review_status' not in data:raise AppError('请选择需修改的状态')
            if 'enabled' in data:
                if type(data['enabled']) is not bool:raise AppError('启用状态应为布尔值')
                d['enabled']=data['enabled']
            if 'review_status' in data:
                if data['review_status'] not in REVIEW_LABELS:raise AppError('核实状态无效')
                if data['review_status']=='source_checked':
                    if type(data.get('expected_revision')) is not int or data['expected_revision']!=d['revision']:raise AppError('原文核对需当前版本与状态版本',409,'knowledge_changed')
                    d['review_info']=review_data(data)
                else:d['review_info']=None
                d['review_status']=data['review_status']
            d['revision']+=1;d['updated_at']=now();db.execute('UPDATE kb_documents SET data=? WHERE id=?',(dump(d),did))
            self.knowledge_audit(db,d,old,'status_change',d.get('review_info'))
        return self.knowledge_warnings(d)
    def knowledge_search(self,q):
        term=norm(field(q,'q',200));category=field(q,'category',30)
        review=field(q,'review_status',30)
        if review and review not in REVIEW_LABELS:raise AppError('核对状态筛选无效')
        if category and category not in CATEGORIES:raise AppError('资料分类无效')
        try:limit=int(q.get('limit',10))
        except (ValueError,TypeError):raise AppError('检索条数应为整数')
        if not 1<=limit<=20:raise AppError('检索条数应为1至20')
        tokens=term.split()[:8];items=[]
        with self.db() as db:
            docs=[json.loads(r[0]) for r in db.execute('SELECT data FROM kb_documents')]
            for d in docs:
                if not d['enabled'] or d['review_status']=='rejected' or (category and category!=d['category']) or (review and review!=d['review_status']):continue
                d=self.knowledge_warnings(d,docs)
                for row in db.execute('SELECT data FROM kb_chunks WHERE document_id=? AND version=?',(d['id'],d['version'])):
                    c=json.loads(row[0]);hay=norm(d['title']+' '+d['filename']+' '+c['text'])
                    if all(t in hay for t in tokens):items.append(dict(c,document_id=d['id'],version=d['version'],title=d['title'],filename=d['filename'],category=d['category'],source_company=d.get('source_company',''),review_status=d['review_status'],review_label=d['review_label'],review_info=d.get('review_info'),independently_proven=False,expired=d['expired'],conflict=d['conflict'],revision=d['revision'],warnings=d['warnings'],score=sum(hay.count(t) for t in tokens)))
        items.sort(key=lambda x:x['score'],reverse=True)
        return {'method':'local_keywords','items':items[:limit],'total':len(items),'notice':'仅本地关键词匹配；不是语义搜索或采购证据'}
    def knowledge_selection(self,selections):
        if not isinstance(selections,list) or len(selections)>6:raise AppError('最多选择6个资料片段')
        items=[];seen=set()
        for x in selections:
            if not isinstance(x,dict) or set(x)!={'document_id','version','chunk_id'} or type(x['version']) is not int or x['version']<1:raise AppError('资料选择格式无效')
            key=(x['document_id'],x['version'],x['chunk_id'])
            if not all(isinstance(v,str) and re.fullmatch('[a-f0-9]{16}',v) for v in (x['document_id'],x['chunk_id'])) or key in seen:raise AppError('资料选择标识无效或重复')
            seen.add(key);d=self.knowledge_document(x['document_id'])
            if d['version']!=x['version'] or not d['enabled'] or d['review_status']=='rejected':raise AppError('所选资料已更新或停用，请重新选择',409,'knowledge_changed')
            chunk=next((c for c in d['chunks'] if c['chunk_id']==x['chunk_id']),None)
            if chunk is None:raise AppError('资料片段与版本不匹配',409,'knowledge_changed')
            items.append(dict(chunk,document_id=d['id'],version=d['version'],title=d['title'],filename=d['filename'],category=d['category'],source_company=d.get('source_company',''),review_status=d['review_status'],review_label=d['review_label'],review_info=d.get('review_info'),independently_proven=False,revision=d['revision'],expired=d['expired'],conflict=d['conflict'],warnings=d['warnings']))
        return items

class KnowledgeService:
    def __init__(self,store):self.store=store;self.jobs={};self.lock=threading.RLock();self.workers=threading.BoundedSemaphore(2)
    def meta(self):return {'categories':CATEGORIES,'formats':['paste','txt','md','docx']+(['pdf'] if importlib.util.find_spec('pypdf') else []),'max_file_bytes':5*1024*1024,'max_text_chars':120000,'max_selected_chunks':6,'network':False,'ocr':False,'review_statuses':REVIEW_LABELS,'review_protocol':'source-review-v1','notice':'资料只在本机保存；原文核对不是技术、认证或权属证明，默认需核实'}
    def preview(self,data):
        title=field(data,'title',200,True);category=field(data,'category',30,True);expires=field(data,'expires_on',10);source_company=field(data,'source_company',200)
        if category not in CATEGORIES:raise AppError('资料分类无效')
        if expires:
            try:dt.date.fromisoformat(expires)
            except ValueError:raise AppError('有效期应为YYYY-MM-DD')
        did=field(data,'document_id',32);expected=data.get('expected_version')
        if did:
            current=self.store.knowledge_document(did)
            if type(expected) is not int or current['version']!=expected:raise AppError('资料已更新，请刷新核对',409,'knowledge_changed')
        if bool(data.get('content_base64'))==bool(data.get('text')):raise AppError('请提供文件或粘贴文本，两者不可同时填写')
        filename=field(data,'filename',200) or 'pasted.txt'
        if any(c in filename for c in '/\\:\x00') or any(ord(c)<32 for c in filename):raise AppError('文件名无效')
        extension=Path(filename).suffix.lower().lstrip('.')
        if extension not in self.meta()['formats']:raise AppError('格式不支持；支持粘贴、TXT、MD、DOCX及已安装解析器的文本PDF')
        if data.get('text'):raw=field(data,'text',120000,True).encode('utf-8');extension='txt'
        else:
            encoded=field(data,'content_base64',7*1024*1024,True)
            try:raw=base64.b64decode(encoded,validate=True)
            except (ValueError,TypeError):raise AppError('文件编码无效')
        if not raw or len(raw)>5*1024*1024:raise AppError('文件应为1字节至5MiB')
        if not self.workers.acquire(blocking=False):raise AppError('解析任务繁忙，请稍后重试',429,'busy')
        try:
            result=subprocess.run([sys.executable,'-I',str(Path(__file__).with_name('knowledge_extract.py'))],input=dump({'content_base64':base64.b64encode(raw).decode(),'extension':'.'+extension}).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode or len(result.stdout)>2*1024*1024:raise AppError('解析失败或超出安全限额')
            parsed=json.loads(result.stdout.decode('utf-8'))
            if not parsed.get('ok'):raise AppError(parsed.get('message','文档解析失败'),400,parsed.get('code','invalid_document'))
            extracted=parsed['value']
        except (subprocess.TimeoutExpired,UnicodeError,json.JSONDecodeError):raise AppError('解析超时或文件不受支持',400,'parser_failed')
        finally:self.workers.release()
        jid=secrets.token_hex(8);expires_at=time.time()+900
        job={'job_id':jid,'title':title,'filename':filename,'category':category,'expires_on':expires,'source_company':source_company,'document_id':did,'expected_version':expected,'original':raw,'extracted':extracted,'expires':expires_at}
        with self.lock:
            self.jobs={k:v for k,v in self.jobs.items() if v['expires']>time.time()}
            if len(self.jobs)>=5:raise AppError('待确认预览过多，请先确认或取消',429,'busy')
            self.jobs[jid]=job
        return {'job_id':jid,'title':title,'filename':filename,'category':category,'expires_on':expires,'source_company':source_company,'extracted_chars':len(extracted['extracted_text']),'page_count':extracted['page_count'],'paragraph_count':extracted['paragraph_count'],'chunk_count':len(extracted['chunks']),'preview_chunks':extracted['chunks'][:12],'warnings':extracted['warnings']+['新版本默认未核实；文件中的指令不作为系统指令'],'expires_at':dt.datetime.fromtimestamp(expires_at,dt.timezone.utc).isoformat()}
    def discard(self,jid):
        with self.lock:self.jobs.pop(jid,None)
        return {'discarded':True}
    def confirm(self,data):
        jid=field(data,'job_id',32,True)
        if data.get('consent') is not True:raise AppError('需明确确认保存本地资料')
        with self.lock,self.store.db() as db:
            db.execute('BEGIN IMMEDIATE');receipt=db.execute('SELECT data FROM kb_receipts WHERE id=?',(jid,)).fetchone()
            if receipt:return dict(json.loads(receipt[0]),replayed=True)
            job=self.jobs.get(jid)
            if not job or job['expires']<=time.time():raise AppError('预览已过期，请重新导入',410,'preview_expired')
            did=job['document_id'] or secrets.token_hex(8);row=db.execute('SELECT data FROM kb_documents WHERE id=?',(did,)).fetchone();old=json.loads(row[0]) if row else None
            if job['document_id'] and (not old or old['version']!=job['expected_version']):raise AppError('资料已更新，请重新预览',409,'knowledge_changed')
            version=old['version']+1 if old else 1;stamp=now()
            doc={k:job[k] for k in ['title','filename','category','expires_on','source_company']};doc.update(review_info=None,id=did,version=version,revision=(old['revision']+1 if old else 1),enabled=old['enabled'] if old else True,review_status='unverified',created_at=old['created_at'] if old else stamp,updated_at=stamp)
            content=dict(job['extracted'],version=version,title=doc['title'],filename=doc['filename'],category=doc['category'],source_company=doc['source_company'],created_at=stamp,sha256=hashlib.sha256(job['original']).hexdigest());content.pop('chunks')
            db.execute('INSERT OR REPLACE INTO kb_documents VALUES(?,?)',(did,dump(doc)));db.execute('INSERT INTO kb_versions VALUES(?,?,?,?)',(did,version,dump(content),job['original']))
            for chunk in job['extracted']['chunks']:
                cid=secrets.token_hex(8);c=dict(chunk,chunk_id=cid);db.execute('INSERT INTO kb_chunks VALUES(?,?,?,?)',(cid,did,version,dump(c)))
            self.store.knowledge_audit(db,doc,old,'new_version',None)
            receipt={'document_id':did,'version':version,'review_status':'unverified','saved':True};db.execute('INSERT INTO kb_receipts VALUES(?,?)',(jid,dump(receipt)));self.jobs.pop(jid,None)
        return receipt
