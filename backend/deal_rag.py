"""Explicit prepare -> consented existing analyze. No transport or credential access."""
import copy
import datetime as dt
import hashlib
import json
import re
import secrets
import threading
import time
from .common import AppError
from .deal_scope import question_value,reviewed_knowledge

def digest(rows):
    return hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

class DealRAGService:
    def __init__(self,store,models,enhanced,clock=None,preview_api=None):
        self.store=store;self.models=models;self.enhanced=enhanced;self.clock=clock or time.monotonic
        self.pending={};self.lock=threading.RLock()
        # Separate automatic path: does not toggle the advanced/manual feature switch.
        self.api=preview_api or type(enhanced.api)(enhanced.retrieve,enhanced.snapshot,store.knowledge_selection,
            enhanced.api.project_knowledge,enhanced.api.evidence_catalog,advisory=None,enabled=True)
    def session(self,session):
        if not isinstance(session,str) or not re.fullmatch('[a-f0-9]{32}',session):raise AppError('请刷新本地会话',403,'session_required')
    def manifest(self,selections):
        rows=list(self.enhanced.snapshot());by_triple={(r.document_id,r.version,r.chunk_id):r for r in rows}
        marks=[]
        for s in selections:
            r=by_triple.get((s['document_id'],s['version'],s['chunk_id']))
            if not r or not r.canonical or not r.read_authorized or not r.enabled or r.version!=r.current_version or r.review_status not in ('confirmed','source_checked'):
                raise AppError('知识身份、权限、发布或审核状态已改变',409,'deal_sources_changed')
            if r.expires_on:
                try:expired=dt.date.fromisoformat(r.expires_on)<dt.date.today()
                except ValueError:expired=True
                if expired:raise AppError('资料已过期或有效期无效',409,'deal_sources_changed')
            marks.append({'selection':s,'revision':r.revision,'text_sha256':hashlib.sha256(r.text.encode()).hexdigest(),
                'review':r.review_status,'fields':[vars(f) for f in r.fields],'conflict':r.conflict,'source_company':r.source_company})
        return digest(marks)
    def clarification(self,question,items):
        registry=self.enhanced.snapshot.registry;known={m.casefold():m for m in registry}
        mentioned=re.findall(r'(?<![A-Za-z0-9])[A-Za-z]+\d+[A-Za-z]*(?![A-Za-z0-9])',question)
        # Standards and units are not equipment model requests.
        missing=[m for m in mentioned if m.casefold() not in known and not re.match(r'^(ISO|DIN|IEC|EN)\d',m,re.I)]
        if missing:return {'reason':'model_not_in_published_sources','asked_models':missing,'options':[]}
        exact=[m for m in mentioned if m.casefold() in known]
        if len(set(m.casefold() for m in exact))>4:return {'reason':'model_scope_exceeds_context_budget','options':exact[:12]}
        variants={}
        for item in items:
            selection=item['selection']
            detail=next((d for d in self.enhanced.snapshot.details.values() if d['document']['id']==selection['document_id'] and d['chunk']['chunk_id']==selection['chunk_id']),None)
            parameters=detail['chunk'].get('metadata',{}).get('structured_parameters',{}) if detail else {}
            primary=parameters.get('model_verbatim')
            if primary and parameters.get('cells'):
                variants.setdefault(primary.casefold(),[]).append((digest(parameters['cells']),selection))
        enumerate_rows=bool(re.search(r'两行|多行|分别|对比|compare|both rows|each row|row [12]',question,re.I))
        ambiguous={m:[s for _,s in values] for m,values in variants.items() if len({h for h,_ in values})>1}
        if ambiguous and not enumerate_rows:return {'reason':'model_parameter_variant_ambiguous','options':ambiguous}
        families=re.findall(r'\b(?:DLK|DLH|DLSK|DLNK|DLEK)\b',question,re.I)
        if families and not mentioned:
            options=sorted(m for m in registry if any(m.casefold().startswith(f.casefold()) for f in families))[:12]
            if len(options)>1:return {'reason':'model_family_ambiguous','options':options}
        if any(i.get('source_constraints',{}).get('conflict') for i in items):
            return {'reason':'published_sources_conflict','options':[i['selection'] for i in items]}
        return None
    def prepare(self,data,session):
        self.session(session)
        if not isinstance(data,dict) or set(data)-{'customer_id','question','knowledge_selections'}:raise AppError('prepare只接受客户、问题和可选手选资料',400,'invalid_input')
        cid=data.get('customer_id');self.store.customer(cid);question=question_value(data.get('question'))
        if question is None:raise AppError('请输入成交推进问题',400,'invalid_deal_question')
        manual='knowledge_selections' in data
        if not self.enhanced.lock.acquire(False):raise AppError('本地检索繁忙，请稍后重试',409,'enhanced_busy')
        try:
            if manual:
                selections=data['knowledge_selections'];raw=self.store.knowledge_selection(selections)
                self.manifest(selections)
                from .context_knowledge import project_knowledge
                items=[{'selection':s,'source':project_knowledge(r),'source_constraints':{'conflict':r.get('conflict',False)}} for s,r in zip(selections,raw)]
                retrieval={'mode':'manual','fallback_code':''}
            else:
                preview=self.api.search({'q':question,'limit':6});items=preview['items']
                retrieval={'mode':preview['retrieval']['method'],'fallback_code':self.enhanced.last_fallback}
            legal=[];size=0;omitted=0
            for item in items:
                scoped=reviewed_knowledge(item['source'])
                if scoped is None:
                    if manual:raise AppError('手选资料审核范围不明确，不能静默替换范围',409,'deal_review_scope_missing')
                    omitted+=1;continue
                n=len(scoped['text'].encode())
                if size+n>12000 or len(legal)>= (6 if manual else 4):
                    if manual:raise AppError('手选知识超出上下文预算，请减少片段',413,'deal_knowledge_budget')
                    omitted+=1;continue
                legal.append(item);size+=n
            # A retrieved but unpublished/unreviewed source is a publication gap,
            # not evidence that the user's equipment model is unknown.
            clarification=None if not legal and omitted else self.clarification(question,legal)
            from .deal_evidence import request,assess
            finite_request=bool(request(question)['fields'])
            # Unparsed questions are review requests, not proof of missing sources.
            # Existing customer context may suffice for a communication preview.
            selections=[i['selection'] for i in legal]
            mark=self.manifest(selections)
            context=self.models.context(cid,selections,deal_question=question)
            if self.manifest(selections)!=mark:raise AppError('准备期间资料改变，请重试',409,'deal_sources_changed')
            from .deal_semantic_contract import generation_permitted
            coverage=context['answer_coverage']
            if not generation_permitted(coverage):
                return {'status':'insufficient_evidence','question':question,'ready_to_generate':False,
                    'knowledge_selections':selections,'model_context':context,'retrieval':retrieval,
                    'answer_coverage':coverage,'clarification':clarification,'cloud_calls':0,
                    'limitations':['纯字段请求缺少已核实证据；保留未知与出处，可转为人工核验或沟通请求。']}
            pid=secrets.token_hex(16)
            job={'session':session,'cid':cid,'question':question,'selections':copy.deepcopy(selections),'manifest':mark,
                'token':context['context_token'],'expires':self.clock()+900,'revision':context['revision'],
                'answer_coverage':copy.deepcopy(context['answer_coverage'])}
            with self.lock:
                self.pending={k:v for k,v in self.pending.items() if v['expires']>self.clock()}
                if len(self.pending)>=100:raise AppError('待确认任务过多',429,'deal_prepare_capacity')
                self.pending[pid]=job
            return {'status':'ready','prepare_id':pid,'question':question,'ready_to_generate':True,
                'knowledge_selections':selections,'model_context':context,'retrieval':retrieval,'cloud_calls':0,
                'answer_coverage':context['answer_coverage'],'clarification':clarification,
                'limitations':['检索分数不代表事实可靠性；缺参数或独立证据必须保留未知。'],
                'omitted_by_review_or_budget':omitted,'advanced_manual_available':True}
        finally:self.enhanced.lock.release()
    def generate(self,data,session):
        self.session(session)
        if not isinstance(data,dict) or set(data)!={'customer_id','prepare_id','context_token','revision','request_id','consent','provider'}:
            raise AppError('generate必须使用已预览的受控范围',400,'invalid_input')
        if data['consent'] is not True or data['provider']!='model':raise AppError('需要明确点击生成并同意本次模型请求',400,'consent_required')
        with self.lock:job=copy.deepcopy(self.pending.get(data['prepare_id']))
        if not job or job['expires']<=self.clock():raise AppError('预览过期，请重新检索',410,'deal_prepare_expired')
        if job['session']!=session or job['cid']!=data['customer_id']:raise AppError('预览不属于当前客户/会话',403,'deal_scope_mismatch')
        if data['context_token']!=job['token'] or data['revision']!=job['revision']:raise AppError('问题或模型预览已改变',409,'context_changed')
        if self.manifest(job['selections'])!=job['manifest']:raise AppError('资料发生改变，请重新预览',409,'deal_sources_changed')
        from .deal_semantic_contract import generation_permitted
        if not generation_permitted(job.get('answer_coverage',{})):
            raise AppError('所问纯字段证据缺失，请先查看未知事项与出处',409,'deal_evidence_insufficient')
        result=self.models.analyze(job['cid'],{'module':'deals','request_id':data['request_id'],'consent':True,
            'revision':data['revision'],'context_token':job['token'],'knowledge_selections':job['selections'],'deal_question':job['question']},review_session=session,review_prepare_id=data['prepare_id'])
        return dict(result,rag={'question':job['question'],'knowledge_selections':job['selections'],
            'answer_coverage':job['answer_coverage'],
            'draft_unsent':True,'sources_validated_by_existing_model_protocol':True})
