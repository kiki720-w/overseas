"""Business results only. Never persist transport credentials or reasoning traces."""
import json
import re
import secrets
from .common import AppError, dump, field, now, paging, page_result
from .model_analysis import build_context, CUSTOMER_FIELDS, FACTORY_FIELDS, RECORD_FIELDS, EVIDENCE_REASONS

MODULES=('development','deals')
ERRORS={
    'invalid_evidence':'模型原文引用未通过本机校验，未保存生成正文。',
    'invalid_response':'模型结构或响应格式无效，未保存生成正文。',
    'unsafe_output':'模型内容未通过商业承诺校验，未保存生成正文。',
    'context_changed':'请求期间输入已修改，请核对历史或重新预览。',
    'profile_changed':'请求期间连接配置改变，未采用旧连接输出。',
    'provider_timeout':'供应商请求超时，未确认生成结果。',
    'provider_auth':'供应商认证失败，请在本机检查连接配置。',
    'provider_rate_limit':'供应商限流或额度限制，未生成业务正文。',
}

def identity(data):
    module=field(data,'module',20) or 'deals'
    if module not in MODULES:raise AppError('业务模块无效')
    request_id=field(data,'request_id',80) or secrets.token_hex(16)
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,80}',request_id):raise AppError('请求标识无效')
    return module,request_id

class AnalysisHistory:
    def init_history(self,db):
        db.executescript('CREATE TABLE IF NOT EXISTS analyses(id TEXT PRIMARY KEY,customer_id TEXT NOT NULL,module TEXT NOT NULL,request_id TEXT NOT NULL,data TEXT NOT NULL,UNIQUE(customer_id,module,request_id));CREATE INDEX IF NOT EXISTS analyses_customer ON analyses(customer_id,module);')

    def analysis_lookup(self,cid,module,request_id):
        self.customer(cid)
        with self.db() as db:row=db.execute('SELECT data FROM analyses WHERE customer_id=? AND module=? AND request_id=?',(cid,module,request_id)).fetchone()
        return json.loads(row[0]) if row else None

    def analysis_begin(self,cid,data,profile,mode):
        module,request_id=identity(data)
        c,context,total,size,token=build_context(self,cid,data.get('knowledge_selections',[]),data.get('deal_question'))
        scope={'deal_question':data.get('deal_question'),'knowledge_selections':data.get('knowledge_selections',[]),'knowledge':context.get('knowledge',[]),'customer_fields':CUSTOMER_FIELDS,'factory_fields':FACTORY_FIELDS,'record_fields':RECORD_FIELDS,'record_ids':[r['id'] for r in context['records']],'record_count':len(context['records']),'records_total':total,'input_bytes':size,'evidence_protocol':'catalog-v1','evidence_reference_count':len(context['evidence_catalog']),'evidence_provenance':context['evidence_provenance'],'supplier_capabilities':context['supplier_capabilities']}
        r=dict(id=secrets.token_hex(16),customer_id=cid,module=module,request_id=request_id,created_at=now(),completed_at='',status='pending',mode=mode,provider=profile['provider'],model=profile['model'],profile_revision=profile.get('revision',0),customer_version=c['version'],context_token=token,input_scope=scope,input_stage=c['stage'],input_blocker=c.get('blocker',''),result=None,error_code='',error_summary='',edited_draft=None,draft_version=0,draft_updated_at='')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT data FROM analyses WHERE customer_id=? AND module=? AND request_id=?',(cid,module,request_id)).fetchone()
            if old:
                previous=json.loads(old[0])
                if previous.get('input_scope',{}).get('deal_question')!=data.get('deal_question') or previous.get('input_scope',{}).get('knowledge_selections',[])!=data.get('knowledge_selections',[]):raise AppError('同一请求标识的知识范围不一致',409,'request_conflict')
                return previous,False
            if build_context(self,cid,data.get('knowledge_selections',[]),data.get('deal_question'))[4]!=token or data.get('context_token',token)!=token:raise AppError('输入已改变，请重新预览',409,'context_changed')
            db.execute('INSERT INTO analyses VALUES(?,?,?,?,?)',(r['id'],cid,module,request_id,dump(r)))
        return r,True

    def analysis_finish(self,r,result=None,error=None):
        # Explicit allowlist: reject raw provider envelopes, hidden reasoning and secrets.
        keys=('match','angle','next_step','draft','unknowns','stage_blocker','notice','facts','hypotheses','questions')
        r=dict(r,status='failed' if error else 'ok',completed_at=now(),result={k:result.get(k,[] if k in ('facts','hypotheses','questions') else '') for k in keys} if result is not None and not error else None,error_code=error.code if error else '',error_summary=ERRORS.get(error.code,'请求未完成或供应商拒绝响应；未保存生成正文。') if error else '')
        from .analysis_contract import public_issue
        output_issue=public_issue(getattr(error,'output_issue',None))
        if output_issue:r['output_issue']=output_issue
        from .analysis_contract import public_adaptation
        adaptation=public_adaptation(result.get('contract_adaptation')) if isinstance(result,dict) else public_adaptation(getattr(error,'contract_adaptation',None))
        if adaptation:r['contract_adaptation']=adaptation
        from .reply_quality import public_outreach_review
        quality=public_outreach_review(result.get('draft_quality')) if isinstance(result,dict) and not error else None
        if quality:r['draft_quality']=quality
        issue=getattr(error,'validation_issue',None)
        if error and isinstance(issue,dict) and issue.get('reason') in EVIDENCE_REASONS and all(type(issue.get(k)) is int and 0<=issue[k]<12 for k in ('fact_index','reference_index')):
            r['validation_issue']={k:issue[k] for k in ('reason','fact_index','reference_index')}
            r['error_summary']+='原因：'+EVIDENCE_REASONS[issue['reason']]+f"（第{issue['fact_index']+1}项事实 / 第{issue['reference_index']+1}个引用）。"
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            try:r['historical_at_completion']=build_context(self,r['customer_id'],r.get('input_scope',{}).get('knowledge_selections',[]),r.get('input_scope',{}).get('deal_question'))[4]!=r['context_token']
            except AppError:r['historical_at_completion']=True
            db.execute('UPDATE analyses SET data=? WHERE id=? AND customer_id=? AND module=?',(dump(r),r['id'],r['customer_id'],r['module']))
        return r

    def analysis_view(self,r,token=None):
        if token is None:
            try:token=build_context(self,r['customer_id'],r.get('input_scope',{}).get('knowledge_selections',[]),r.get('input_scope',{}).get('deal_question'))[4]
            except AppError:token=''
        return dict(r,historical=token!=r['context_token'],unsent=True)

    def analyses(self,cid,q):
        self.customer(cid);module=field(q,'module',20)
        if module not in MODULES:raise AppError('请选择业务模块')
        p,s=paging(q)
        with self.db() as db:
            total=db.execute('SELECT COUNT(*) FROM analyses WHERE customer_id=? AND module=?',(cid,module)).fetchone()[0]
            rows=[json.loads(x[0]) for x in db.execute('SELECT data FROM analyses WHERE customer_id=? AND module=? ORDER BY rowid DESC LIMIT ? OFFSET ?',(cid,module,s,(p-1)*s))]
        try:token=build_context(self,cid)[4]
        except AppError:token=''
        return page_result([self.analysis_view(r) for r in rows],total,p,s)

    def analysis_draft(self,data):
        cid=field(data,'customer_id',32,True);self.customer(cid)
        aid=field(data,'analysis_id',64,True);module=field(data,'module',20,True)
        text=field(data,'draft',12000,True)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT data FROM analyses WHERE id=? AND customer_id=? AND module=?',(aid,cid,module)).fetchone()
            if not row:raise AppError('该客户或模块不存在此分析',404,'not_found')
            r=json.loads(row[0])
            if r['status']!='ok':raise AppError('失败或未完成的分析没有可编辑草稿')
            if type(data.get('draft_version')) is not int:raise AppError('草稿版本无效')
            if data['draft_version']!=r['draft_version']:
                if data['draft_version']==r['draft_version']-1 and r['edited_draft']==text:return self.analysis_view(r)
                raise AppError('草稿已在其他页面修改，请刷新历史再核对；本次未覆盖',409,'draft_conflict')
            r.update(edited_draft=text,draft_version=r['draft_version']+1,draft_updated_at=now())
            db.execute('UPDATE analyses SET data=? WHERE id=?',(dump(r),aid))
        return self.analysis_view(r)

def replay(r):
    if r['status']=='pending':raise AppError('该次请求尚未确认完成，请刷新分析历史；不会重复调用供应商',409,'analysis_pending')
    if r['status']=='failed':raise AppError(r['error_summary'],502,r['error_code'])
    result=dict(r['result'],mode=r['mode'],provider=r['provider'],model=r['model'],revision=r['profile_revision'],customer_id=r['customer_id'],customer_version=r['customer_version'],context_token=r['context_token'],analysis_id=r['id'],module=r['module'])
    if r.get('contract_adaptation'):result['contract_adaptation']=r['contract_adaptation']
    if r.get('draft_quality'):result['draft_quality']=r['draft_quality']
    return result
