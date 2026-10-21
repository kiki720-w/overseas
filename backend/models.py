# -*- coding: utf-8 -*-
import json
import re
import socket
import threading
from dataclasses import dataclass, field as secret_field
from .common import AppError, dump, field, now
from .model_transport import HTTPSJSONTransport, normalize_endpoint, public_addresses
from .model_analysis import build_context, completion_text, json_output, validate_analysis, INSTRUCTION, CUSTOMER_FIELDS, FACTORY_FIELDS, RECORD_FIELDS
from . import model_catalog

PRESETS={'deepseek':'https://api.deepseek.com','kimi-cn':'https://api.moonshot.cn/v1','kimi-global':'https://api.moonshot.ai/v1','custom':''}
PRESETS.update({item['provider']:item['base_url'] for item in model_catalog.ITEMS})
MODEL_ID=re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}')

@dataclass(frozen=True)
class Snapshot:
    provider:str
    base_url:str
    model:str
    revision:int
    key:str=secret_field(repr=False)

class ModelService:
    def __init__(self,store,mode,transport=None,resolver=None,credential_vault=None):
        self.store=store;self.mode=mode;self.lock=threading.RLock();self.credential_vault=credential_vault
        self.transport=transport or HTTPSJSONTransport();self.resolver=resolver or socket.getaddrinfo
        with store.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS model_config(id INTEGER PRIMARY KEY,data TEXT)')
            row=db.execute('SELECT data FROM model_config WHERE id=1').fetchone()
        self.profile=json.loads(row[0]) if row else {'provider':'deepseek','base_url':PRESETS['deepseek'],'model':'','revision':0}
        self.profile.setdefault('name','默认连接')
        self.key='';self.reset_status()
    def reset_status(self):
        self.last_status='not_tested' if self.key else 'key_required';self.last_error=''
        self.models_status=self.generation_status='not_tested'
        self.models_checked_at=self.generation_checked_at=''
    def credential_status(self):
        from .credential_vault import model_scope
        vault=getattr(self,'credential_vault',None)
        return {'credential_persistence_available':bool(vault and vault.available),'credential_saved_encrypted':bool(vault and vault.contains(model_scope(self.profile))),'credential_protection':'windows_current_user_dpapi' if vault and vault.available else 'unavailable','automatic_key_restore':False}
    def settings(self):
        with self.lock:
            return dict(self.profile,**model_catalog.settings_fields(self.profile),**self.credential_status(),configured=bool(self.key),persist=False,key_storage='memory',last_status=self.last_status,last_error=self.last_error,models_status=self.models_status,generation_status=self.generation_status,models_checked_at=self.models_checked_at,generation_checked_at=self.generation_checked_at)
    def save(self,data):
        if 'option_id' in data:
            item=model_catalog.resolve(data)
            if any(k in data for k in ('provider','base_url','model','credentialScope','requestAdapter')):
                raise AppError('预置模型由服务端映射，不能覆盖供应商或请求参数',400,'catalog_option_invalid')
            data=dict(data,**{k:item[k] for k in ('provider','base_url','model')})
        provider=field(data,'provider',30,True)
        if provider not in PRESETS:raise AppError('供应商无效')
        if type(data.get('persist',False)) is not bool or type(data.get('restore_key',False)) is not bool:raise AppError('密钥保存和恢复选项必须为布尔值')
        vault=getattr(self,'credential_vault',None)
        if data.get('persist') and not (vault and vault.available):raise AppError('本机无法使用用户级加密；未保存明文',400,'key_persistence_unavailable')
        if data.get('restore_key') and (data.get('consent') is not True or data.get('persist') or data.get('clear_key') or data.get('api_key')):raise AppError('请单独明确确认恢复本机加密密钥',400,'consent_required')
        if type(data.get('clear_key',False)) is not bool:raise AppError('clear_key 必须为布尔值')
        url=normalize_endpoint(field(data,'base_url',500) or PRESETS[provider])
        from urllib.parse import urlsplit
        u=urlsplit(url)
        if provider!='custom':
            expected=urlsplit(PRESETS[provider])
            if u.hostname!=expected.hostname or provider=='deepseek' and u.path not in ('','/v1') or provider!='deepseek' and u.path!=expected.path:
                raise AppError('预设供应商地址不匹配；其他兼容地址请选择自定义',400,'endpoint_invalid')
        public_addresses(url,self.resolver)
        model=field(data,'model',200)
        name=field(data,'name',80) if 'name' in data else None
        if model and not MODEL_ID.fullmatch(model):raise AppError('模型 ID 只能包含字母、数字和 . _ : / + -')
        key=data.get('api_key')
        if key is not None and (not isinstance(key,str) or len(key)>4096 or any(ord(c)<33 or ord(c)>126 for c in key)):
            raise AppError('API Key 必须是无空白的有效文本',400,'invalid_input')
        if key=='' :key=None
        if key and data.get('clear_key'):raise AppError('不能同时设置和清除密钥')
        with self.lock:
            if 'revision' in data and (type(data['revision']) is not int or data['revision']!=self.profile['revision']):
                raise AppError('连接配置已变更，请刷新设置后重试',409,'profile_changed')
            target={'provider':provider,'base_url':url,'model':model}
            changed=provider!=self.profile['provider'] or url!=self.profile['base_url'] or model_catalog.credential_scope(target)!=model_catalog.credential_scope(self.profile)
            next_key='' if changed or data.get('clear_key') else self.key
            if key is not None:next_key=key
            from .credential_vault import model_scope
            scope=model_scope(target)
            if data.get('restore_key'):
                if changed:raise AppError('连接已变化，请先保存目标连接再明确恢复密钥',409,'profile_changed')
                if not vault:raise AppError('本机没有启用用户级密钥恢复',400,'key_persistence_unavailable')
                next_key=vault.load(scope)
            if data.get('persist') and not next_key:raise AppError('请先填写当前连接的 API Key',400,'key_required')
            profile={'provider':provider,'base_url':url,'model':model,'revision':self.profile['revision']+1,'name':name if name is not None else self.profile['name']}
            if next_key and next_key in dump(profile):raise AppError('密钥不能出现在模型 ID 或端点配置中',400,'invalid_input')
            # Explicit user writes only; settings/startup never decrypt credentials.
            if data.get('persist'):vault.save(scope,next_key)
            if data.get('clear_key') and vault:vault.delete(scope)
            with self.store.db() as db:db.execute('INSERT OR REPLACE INTO model_config VALUES(1,?)',(dump(profile),))
            self.profile=profile;self.key=next_key;self.reset_status()
            return self.settings()
    def snapshot(self,data,need_model=True):
        if data.get('consent') is not True:raise AppError('请明确同意本次供应商请求',400,'consent_required')
        with self.lock:
            if type(data.get('revision')) is not int or data['revision']!=self.profile['revision']:
                raise AppError('连接或模型已变更，请重新确认',409,'profile_changed')
            if not self.key:raise AppError('当前服务端内存没有 API Key；重启后需重新填写',400,'key_required')
            if need_model and not self.profile['model']:raise AppError('请发现后选择或手填模型 ID 并保存',400,'model_required')
            return Snapshot(**{k:self.profile[k] for k in ('provider','base_url','model','revision')},key=self.key)
    def ensure_current(self,snapshot):
        with self.lock:
            if self.profile['revision']!=snapshot.revision:
                raise AppError('请求期间连接或模型已变更，旧结果已丢弃',409,'profile_changed')
    def request(self,snapshot,task,payload=None):
        base_url=snapshot.base_url
        if task=='structured_deal_strict':
            from .structured_tool_adapter import check_profile,BETA_BASE,Rejection
            try:check_profile(snapshot.provider,snapshot.base_url,snapshot.model)
            except Rejection:raise AppError('Strict tools are unavailable for this provider/model; no fallback request was sent',400,'strict_provider_unsupported')
            base_url=BETA_BASE
        addresses=public_addresses(base_url,self.resolver)
        value=self.transport(base_url,'models' if task=='models' else 'chat/completions',snapshot.key,payload,addresses,task)
        if not isinstance(value,dict) or len(dump(value).encode('utf-8'))>256*1024:
            raise AppError('供应商响应无效或过大',502,'invalid_response')
        # The upstream must not echo header credentials in IDs, model text or errors.
        if snapshot.key and snapshot.key in dump(value):raise AppError('供应商响应包含敏感内容，已拒绝展示',502,'invalid_response')
        self.ensure_current(snapshot)
        return value
    def record_status(self,snapshot,task,error=None):
        status='failed' if error else 'ok';code=error.code if error else ''
        with self.lock:
            if self.profile['revision']==snapshot.revision:
                self.last_status='failed' if error else 'models_ok' if task=='models' else 'generation_ok'
                self.last_error=str(error) if error else ''
                if task=='models':self.models_status='unsupported' if code=='models_unsupported' else status;self.models_checked_at=now()
                elif task=='generation_test':self.generation_status=status;self.generation_checked_at=now()
        self.store.call_log(task,status,{'provider':snapshot.provider,'model':snapshot.model},code)
    def models(self,data):
        snap=self.snapshot(data,False)
        try:
            value=self.request(snap,'models');raw=value.get('data')
            if not isinstance(raw,list) or len(raw)>500:raise AppError('模型列表结构无效',502,'invalid_response')
            items=[];seen=set()
            for item in raw:
                if not isinstance(item,dict):raise AppError('模型列表项无效',502,'invalid_response')
                mid=item.get('id')
                if not isinstance(mid,str) or not MODEL_ID.fullmatch(mid):raise AppError('模型 ID 格式无效',502,'invalid_response')
                if mid in seen:continue
                seen.add(mid);entry={'id':mid}
                if isinstance(item.get('name'),str) and len(item['name'])<=200:entry['name']=item['name']
                items.append(entry)
            if not items:raise AppError('供应商返回空模型列表，可手填已知模型 ID',502,'invalid_response')
            self.record_status(snap,'models')
            return {'models':items,'status':'ok','revision':snap.revision,'notice':'仅认证与模型列表已验证；生成能力尚需单独测试。'}
        except AppError as error:self.record_status(snap,'models',error);raise
    def test(self,data):
        snap=self.snapshot(data)
        try:
            payload=model_catalog.payload(vars(snap),[{'role':'system','content':'Return only the word OK. This is a synthetic connectivity test.'},{'role':'user','content':'Synthetic test: reply OK.'}],'generation_test')
            response=self.request(snap,'generation_test',payload)
            if completion_text(response).strip()!='OK':raise AppError('合成生成测试未得到预期短文本；请检查模型兼容性',502,'invalid_response')
            self.record_status(snap,'generation_test')
            return {'status':'ok','message':'合成短文本生成测试通过；不是客户业务分析或质量保证。','revision':snap.revision,'provider':snap.provider,'model':snap.model}
        except AppError as error:self.record_status(snap,'generation_test',error);raise
    def review_draft_manager(self):
        with self.lock:
            if not hasattr(self,'_review_drafts'):
                from .review_drafts import ReviewDrafts
                def current(cid,selections,question):
                    c,context,total,size,token=build_context(self.store,cid,selections,question)
                    return {'customer_version':c['version'],'profile_revision':self.settings()['revision'],'context_token':token}
                self._review_drafts=ReviewDrafts(current)
            return self._review_drafts

    def context(self,cid,selections=None,deal_question=None):
        c,context,total,size,token=build_context(self.store,cid,selections,deal_question);settings=self.settings()
        allowed=self.mode=='local' and not c.get('demo',False)
        output={'customer_id':cid,'customer_name':c['name'],'customer_version':c['version'],'revision':settings['revision'],'provider':settings['provider'],'base_url':settings['base_url'],'model':settings['model'],'configured':settings['configured'],'real_allowed':allowed,'reason':'' if allowed else '演示工作区或演示客户不能发送到真实模型，请使用实际资料工作区。','fields':{'customer':CUSTOMER_FIELDS,'factory':FACTORY_FIELDS,'records':RECORD_FIELDS},'record_count':len(context['records']),'records_total':total,'input_bytes':size,'evidence_reference_count':len(context['evidence_catalog']),'context_token':token,'knowledge':{'items':context.get('knowledge',[]),'warnings':list(dict.fromkeys(w for x in context.get('knowledge',[]) for w in x['warnings'])),'selection_token':token}}
        if deal_question:output['answer_coverage']=context['deal_request']['answer_coverage']
        return output
    def analyze(self,cid,data,review_session=None,review_prepare_id=None):
        from .analysis_history import identity, replay
        module,request_id=identity(data)
        if module!='deals' and 'deal_question' in data:
            raise AppError('deal_question 仅支持 deals 模块；development 请求请移除此参数',400,'invalid_parameter_combination')
        data=dict(data,module=module,request_id=request_id)
        if module=='deals' and 'deal_question' in data:
            if data['deal_question'] is None:
                raise AppError('显式deal_question不能为null；普通分析请省略此字段',400,'invalid_deal_question')
            from .deal_scope import question_value
            data['deal_question']=question_value(data['deal_question'])
        review_path=module=='deals' and bool(data.get('deal_question'))
        if review_path:
            from .review_drafts import valid_session
            valid_session(review_session)
            self.snapshot(data)  # Preserve existing consent/current profile checks on replay.
            previous=self.review_draft_manager().replay(cid,review_session,data)
            if previous:return previous
        old=self.store.analysis_lookup(cid,module,request_id)
        if old:
            snap=self.snapshot(data)  # Explicit consent and current connection still required.
            if data.get('deal_question')!=old.get('input_scope',{}).get('deal_question') or data.get('knowledge_selections',[])!=old.get('input_scope',{}).get('knowledge_selections',[]) or data.get('context_token')!=old['context_token'] or snap.revision!=old['profile_revision']:
                raise AppError('该请求标识已用于另一输入或连接版本；请查看历史，不会重发',409,'request_conflict')
            if old['status']=='ok' and self.store.analysis_view(old)['historical']:
                raise AppError('该结果已成为历史，请重新预览当前输入；不会重发',409,'context_changed')
            return replay(old)
        snap=self.snapshot(data)
        c,context,total,size,token=build_context(self.store,cid,data.get('knowledge_selections',[]),data.get('deal_question'))
        if data.get('deal_question'):
            from .deal_semantic_contract import generation_permitted
            if not generation_permitted(context.get('deal_request',{}).get('answer_coverage',{})):
                raise AppError('所问纯字段证据缺失，请先查看未知事项与出处',409,'deal_evidence_insufficient')

        if self.mode!='local' or c.get('demo',False):raise AppError('演示数据不能发送到真实模型',400,'demo_data_forbidden')
        if data.get('context_token')!=token:raise AppError('客户、记录或工厂资料已变更，请重新检查发送范围',409,'context_changed')
        entry,created=self.store.analysis_begin(cid,dict(data,module=module,request_id=request_id),{'provider':snap.provider,'model':snap.model,'revision':snap.revision},'model')
        if not created:return replay(entry)
        try:
            module_instruction='本次模块：客户开发，重点评估来源、匹配依据与首次切入。' if module=='development' else '本次模块：成交推进，结合已保存沟通解释阶段、卡点和下一轮跟进。'
            if data.get('deal_question'):
                from .deal_scope import DEAL_INSTRUCTION
                module_instruction+='\n'+DEAL_INSTRUCTION
                from .deal_evidence import INSTRUCTION as EVIDENCE_INSTRUCTION
                module_instruction+='\n'+EVIDENCE_INSTRUCTION
            from .reply_quality import outreach_instruction
            module_instruction+='\n'+outreach_instruction(context)
            payload=model_catalog.payload(vars(snap),[{'role':'system','content':INSTRUCTION+'\n'+module_instruction},{'role':'user','content':'UNTRUSTED_INPUT_JSON\n'+dump(context)+'\nEND_UNTRUSTED_INPUT'}],'analyze')
            response=json_output(completion_text(self.request(snap,'analyze',payload)))
            result=validate_analysis(response,context,allow_unreviewed=review_path)
            if result.get('_needs_review') and not review_path:
                raise AppError('待审草稿内部结果不能作为普通模块分析保存',409,'invalid_review_scope')
            if review_path and result.get('_needs_review'):
                with self.lock:self.ensure_current(snap)
                receipt=self.review_draft_manager().create(cid,review_session,data,
                    {'customer_version':c['version'],'profile_revision':snap.revision,'context_token':token},
                    result['original'],result['review'],review_prepare_id)
                # The existing history stores only a generic non-business marker,
                # never the draft or its new session-only access privileges.
                marker=AppError('未审核草稿仅限当前会话',409,'analysis_needs_review')
                marker.contract_adaptation=result['review'].get('contract_adaptation')
                self.store.analysis_finish(entry,error=marker)
                self.store.call_log('analyze','needs_review',{'provider':snap.provider,'model':snap.model},cid=cid)
                return receipt
            with self.lock:
                self.ensure_current(snap)
                completed=self.store.analysis_finish(entry,result)
            self.store.call_log('analyze','ok',{'provider':snap.provider,'model':snap.model},cid=cid)
            if completed['historical_at_completion']:
                raise AppError('分析期间输入已变更；结果仅保留为历史，未覆盖当前资料',409,'context_changed')
            return replay(completed)
        except AppError as error:
            if not locals().get('completed'):
                self.store.analysis_finish(entry,error=error)
                self.store.call_log('analyze','failed',{'provider':snap.provider,'model':snap.model},error.code,cid)
            raise
        except Exception:
            error=AppError('分析未完成，请核对本地历史；不会自动重发',500,'analysis_failed')
            self.store.analysis_finish(entry,error=error)
            self.store.call_log('analyze','failed',{'provider':snap.provider,'model':snap.model},error.code,cid)
            raise error

    def set_structured_schema(self,schema):
        """Trusted local schema configured by StructuredDeals; no credential/profile mutation."""
        from .structured_tool_adapter import provider_schema,audit_schema,Rejection
        import copy
        try:projected,changes=provider_schema(schema);audit_schema(projected)
        except Rejection:raise AppError('Local contract cannot be projected safely',503,'strict_schema_unsupported')
        with self.lock:
            self._structured_provider_schema=copy.deepcopy(projected)
            self._structured_schema_sha256=__import__('hashlib').sha256(dump(projected).encode('utf-8')).hexdigest()

    def structured(self,cid,data,instruction):
        """Use the existing in-memory connection; never expose Snapshot or Key."""
        snap=self.snapshot(data)
        c,context,total,size,token=build_context(self.store,cid,data.get('knowledge_selections',[]),data.get('deal_question'))
        if self.mode!='local' or c.get('demo',False):raise AppError('演示客户不能发送到真实模型',400,'demo_data_forbidden')
        if data.get('context_token')!=token:raise AppError('资料变化，请重新预览',409,'context_changed')
        for item in context['evidence_catalog']:
            item['structured_reference']={'source_id':item['ref_id'],'version':1,'start':0,'end':len(item['quote'])}
        try:
            from .structured_tool_adapter import check_profile,payload as tool_payload,arguments,Rejection,raw_arguments_for_evidence
            try:check_profile(snap.provider,snap.base_url,snap.model)
            except Rejection:raise AppError('Strict tools are unavailable for this provider/model; no fallback request was sent',400,'strict_provider_unsupported')
            schema=getattr(self,'_structured_provider_schema',None)
            if schema is None:raise AppError('Strict local contract is not configured',503,'strict_schema_unavailable')
            request=tool_payload(schema,[{'role':'system','content':instruction},{'role':'user','content':'UNTRUSTED_INPUT_JSON\n'+dump(context)+'\nEND_UNTRUSTED_INPUT'}])
            response=self.request(snap,'structured_deal_strict',request)
            try:raw=arguments(response)
            except Rejection as rejected:
                error=AppError('Invalid strict tool response; no fallback or retry',502,'strict_response_invalid')
                error.diagnostics=[{'reason':rejected.reason,'path':'$'}]
                error.model_output_raw=raw_arguments_for_evidence(response)
                raise error
            self.ensure_current(snap)
            if build_context(self.store,cid,data.get('knowledge_selections',[]),data.get('deal_question'))[-1]!=token:
                raise AppError('调用期间资料变化，结果已丢弃',409,'context_changed')
            self.store.call_log('structured_deal','ok',{'provider':snap.provider,'model':snap.model},cid=cid)
            return raw
        except AppError as error:
            self.store.call_log('structured_deal','failed',{'provider':snap.provider,'model':snap.model},error.code,cid)
            raise
