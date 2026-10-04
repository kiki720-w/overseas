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
    def __init__(self,store,mode,transport=None,resolver=None):
        self.store=store;self.mode=mode;self.lock=threading.RLock()
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
    def settings(self):
        with self.lock:
            return dict(self.profile,**model_catalog.settings_fields(self.profile),configured=bool(self.key),persist=False,key_storage='memory',last_status=self.last_status,last_error=self.last_error,models_status=self.models_status,generation_status=self.generation_status,models_checked_at=self.models_checked_at,generation_checked_at=self.generation_checked_at)
    def save(self,data):
        if 'option_id' in data:
            item=model_catalog.resolve(data)
            if any(k in data for k in ('provider','base_url','model','credentialScope','requestAdapter')):
                raise AppError('预置模型由服务端映射，不能覆盖供应商或请求参数',400,'catalog_option_invalid')
            data=dict(data,**{k:item[k] for k in ('provider','base_url','model')})
        provider=field(data,'provider',30,True)
        if provider not in PRESETS:raise AppError('供应商无效')
        if data.get('persist',False) is not False:raise AppError('本轮仅支持服务端内存密钥，未提供密钥持久化',400,'key_persistence_unavailable')
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
            profile={'provider':provider,'base_url':url,'model':model,'revision':self.profile['revision']+1,'name':name if name is not None else self.profile['name']}
            if next_key and next_key in dump(profile):raise AppError('密钥不能出现在模型 ID 或端点配置中',400,'invalid_input')
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
        addresses=public_addresses(snapshot.base_url,self.resolver)
        value=self.transport(snapshot.base_url,'models' if task=='models' else 'chat/completions',snapshot.key,payload,addresses,task)
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
    def context(self,cid,selections=None):
        c,context,total,size,token=build_context(self.store,cid,selections);settings=self.settings()
        allowed=self.mode=='local' and not c.get('demo',False)
        return {'customer_id':cid,'customer_name':c['name'],'customer_version':c['version'],'revision':settings['revision'],'provider':settings['provider'],'base_url':settings['base_url'],'model':settings['model'],'configured':settings['configured'],'real_allowed':allowed,'reason':'' if allowed else '演示工作区或演示客户不能发送到真实模型，请使用实际资料工作区。','fields':{'customer':CUSTOMER_FIELDS,'factory':FACTORY_FIELDS,'records':RECORD_FIELDS},'record_count':len(context['records']),'records_total':total,'input_bytes':size,'evidence_reference_count':len(context['evidence_catalog']),'context_token':token,'knowledge':{'items':context.get('knowledge',[]),'warnings':list(dict.fromkeys(w for x in context.get('knowledge',[]) for w in x['warnings'])),'selection_token':token}}
    def analyze(self,cid,data):
        from .analysis_history import identity, replay
        module,request_id=identity(data)
        old=self.store.analysis_lookup(cid,module,request_id)
        if old:
            snap=self.snapshot(data)  # Explicit consent and current connection still required.
            if data.get('knowledge_selections',[])!=old.get('input_scope',{}).get('knowledge_selections',[]) or data.get('context_token')!=old['context_token'] or snap.revision!=old['profile_revision']:
                raise AppError('该请求标识已用于另一输入或连接版本；请查看历史，不会重发',409,'request_conflict')
            if old['status']=='ok' and self.store.analysis_view(old)['historical']:
                raise AppError('该结果已成为历史，请重新预览当前输入；不会重发',409,'context_changed')
            return replay(old)
        snap=self.snapshot(data)
        c,context,total,size,token=build_context(self.store,cid,data.get('knowledge_selections',[]))
        if self.mode!='local' or c.get('demo',False):raise AppError('演示数据不能发送到真实模型',400,'demo_data_forbidden')
        if data.get('context_token')!=token:raise AppError('客户、记录或工厂资料已变更，请重新检查发送范围',409,'context_changed')
        entry,created=self.store.analysis_begin(cid,dict(data,module=module,request_id=request_id),{'provider':snap.provider,'model':snap.model,'revision':snap.revision},'model')
        if not created:return replay(entry)
        try:
            module_instruction='本次模块：客户开发，重点评估来源、匹配依据与首次切入。' if module=='development' else '本次模块：成交推进，结合已保存沟通解释阶段、卡点和下一轮跟进。'
            payload=model_catalog.payload(vars(snap),[{'role':'system','content':INSTRUCTION+'\n'+module_instruction},{'role':'user','content':'UNTRUSTED_INPUT_JSON\n'+dump(context)+'\nEND_UNTRUSTED_INPUT'}],'analyze')
            result=validate_analysis(json_output(completion_text(self.request(snap,'analyze',payload))),context)
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
