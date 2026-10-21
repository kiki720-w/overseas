"""On-demand public-query plans. Each explicit next action makes at most one request."""
import copy
import hashlib
import json
import re
import secrets
import threading
import time
from .common import AppError,field,now,norm,TYPES
from .search_ledger import SearchLedger,dimensions,fingerprint as wire_fingerprint
from .search import compose_query
from . import search_workflow,buyer_search,entity_candidates
from .search_coverage import expand,fingerprint,proposal
from .search_options import deepseek_options

MAX_QUERIES=8
MAX_PLANS=2
MAX_RETAINED_PLANS=20
PLAN_TTL=1800

def plan_id(data):
    value=field(data,'plan_id',32,True)
    if not re.fullmatch('[a-f0-9]{32}',value):raise AppError('搜索计划标识无效')
    return value

def strings(data,key):
    value=data.get(key,[])
    if not isinstance(value,list) or len(value)>4:raise AppError(key+' 最多4条公开条件')
    result=[]
    for item in value:
        item=field({'value':item},'value',100,True)
        if norm(item) not in {norm(x) for x in result}:result.append(item)
    return result

def connection(service):
    settings=service.search_provider.settings() if hasattr(service.search_provider,'settings') else {}
    return {'provider':settings.get('provider','tavily'),'engine_type':settings.get('engine_type',''),'price_cny_per_request':settings.get('price_cny_per_request')}


def budget(plan):
    return {'search_connection':plan['search_connection'],'estimated_cost_cny':round(plan['attempts']*(plan['search_connection'].get('price_cny_per_request') or 0),6),'billing_confirmed':False,'estimate_basis':'attempt_upper_bound; not an invoice; Search and Fetch billed separately; model tokens excluded','max_provider_requests':plan['maximum'],'provider_requests_attempted':plan['attempts'],
            'remaining':max(0,plan['maximum']-plan['attempts']),'preview_queries_pending':max(0,len(plan['queries'])-plan['next']),'max_results_per_request':plan['requests'][0].get('count',8) if plan['requests'] else 8,'max_provider_requests_per_click':1,'initial_preview_queries':plan['initial_preview_queries'],'total_request_limit':None,'authorization_scope':'one_request_per_explicit_click'}

def public(plan):
    batches=copy.deepcopy(plan['batches'])
    for batch in batches:batch['entity_summary']=entity_candidates.summarize(batch['items'],annotate=True)
    return {'protocol':'search-v2','plan_id':plan['plan_id'],'state':plan['state'],'created_at':plan['created_at'],
            'expires_in':max(0,int(plan['expires']-time.monotonic())),'queries':copy.deepcopy(plan['queries']),
            'buyer_intent':copy.deepcopy(plan['buyer_intent']),'budget':budget(plan),'next_query_index':plan['next'],'has_more':plan['state']=='ready' and not plan['pending'] and not plan.get('_busy') and plan['next']<len(plan['queries']) and plan['attempts']<plan['maximum'],
            'entity_summary':entity_candidates.summarize([c for b in batches for c in b['items']]),'batches':batches,'stop_reason':plan.get('stop_reason',''),'pending':bool(plan['pending'] or plan.get('_busy')),'ledger':copy.deepcopy(plan.get('ledger_rows',[])),'last_outcome':plan.get('last_outcome','not_started'),'conditions':copy.deepcopy(plan['conditions']),
            'can_extend':plan['state']=='complete' and bool(plan['coverage']) and not plan.get('_busy'),
            'extension':proposal(plan) if plan['state']=='complete' or (plan['state']=='stopped' and plan['next']>=len(plan['queries'])) else None,
            'coverage_remaining':bool(plan['coverage']),
            'can_resume':plan['state']=='stopped' and not plan['pending'] and not plan.get('_busy') and (plan['next']<len(plan['queries']) or bool(plan['coverage'])) and plan.get('stop_reason') in ('user_stopped','provider_failure'),
            'retry_after_seconds':max(0,int(plan.get('retry_at',0)-time.monotonic())+1) if plan.get('retry_at',0)>time.monotonic() else 0,
            'consecutive_zero_company_batches':plan['zero_streak'],'needs_zero_acknowledgement':plan['zero_streak']>=2,
            'notice':'每次明确点击最多请求一次，最多8个网页结果。初始预览不是总批次上限；可按公开条件扩展新的查询角度，覆盖耗尽需调整条件。数量与新增结果不保证，不代表全网穷尽或采购意向。'}


class SearchPlans:
    def __init__(self,service):self.service=service;self.plans={};self.lock=threading.RLock();self.ledger=SearchLedger(service.store,secrets.token_hex(16))
    def load(self,pid):
        current=self.plans.get(pid)
        if current and current['pending']:return current
        plan=self.ledger.fetch(pid);plan['query_builder']=getattr(self.service.search_provider,'compose_query',compose_query)
        used=self.ledger.used();provider=plan['search_connection']['provider']+':'+plan['search_connection'].get('engine_type','')
        def available(request):return wire_fingerprint(provider,plan['query_builder'](request),request.get('count',8),request) not in used
        prefix=plan['requests'][:plan['next']];tail=[(r,plan['queries'][i]['angle']) for i,r in enumerate(plan['requests']) if i>=plan['next'] and available(r)]
        plan['coverage']=[(r,a) for r,a in plan['coverage'] if available(r)]
        while len(tail)<max(0,plan['maximum']-plan['attempts']) and plan['coverage']:
            tail.append(plan['coverage'].pop(0))
        plan['requests']=prefix+[r for r,a in tail]
        plan['queries']=plan['queries'][:plan['next']]+[{'index':plan['next']+i,'query':r['query'],'executed_query':plan['query_builder'](r),'angle':a} for i,(r,a) in enumerate(tail)]
        plan['ledger_rows']=self.ledger.rows(pid)
        states={r['query_index']:r['state'] for r in plan['ledger_rows']}
        for q in plan['queries']:q.update(dimensions=dimensions(q['executed_query'],plan['conditions'],plan['buyer_intent']),query_state=states.get(q['index'],'queued'))
        if plan['state']=='ready' and plan['next']>=len(plan['queries']):plan.update(state='complete',stop_reason='budget_reached' if plan['coverage'] else 'queries_complete')
        self.plans[pid]=plan
        for batch in plan['batches']:
            for candidate in batch['items']:
                if candidate['candidate_id'] not in self.service.jobs:
                    c=copy.deepcopy(candidate);c['expires']=time.monotonic()+600;self.service.jobs[c['candidate_id']]=c
        for c in self.ledger.sources():
            if c['candidate_id'] not in self.service.jobs:c['expires']=time.monotonic()+600;self.service.jobs[c['candidate_id']]=c
        return plan
    def create(self,data):
        query=field(data,'query',500,True);products=field(data,'products',500);countries=field(data,'countries',200);kind=field(data,'type',30)
        if kind and kind not in TYPES:raise AppError('客户类型无效')
        maximum=data.get('max_queries',6)
        if type(maximum) is not int or not 1<=maximum<=MAX_QUERIES:raise AppError('查询预算必须为1至8的整数')
        regions=strings(data,'regions');industries=strings(data,'industries');synonyms=strings(data,'product_synonyms')
        builder=getattr(self.service.search_provider,'compose_query',compose_query)
        original={'query':query,'products':products,'countries':countries,'type':kind}
        if 'buyer_intent' not in data and hasattr(self.service,'buyer_settings'):
            data=dict(data,buyer_intent=self.service.buyer_settings.read()['config'])
        intent=buyer_search.resolve(dict(data,**original))
        base=buyer_search.request(original,intent)
        options=deepseek_options(data,original['query']) if connection(self.service)['provider']=='deepseek' else {}
        base.update(options);query=base['query'];requests=[];angles=[]
        def add(angle,**changes):
            request=dict(base,**changes)
            if len(request['query'])>500:return
            try:executed=builder(request)
            except AppError:
                if not requests:raise
                return
            if executed not in {builder(x) for x in requests}:requests.append(request);angles.append(angle)
        add('买家加工用途：'+intent['process_terms'][0] if intent['enabled'] else '原始公开条件')
        for region in regions:add('用户地域条件：'+region,query=query+' | region: '+region)
        for industry in industries:add('用户行业条件：'+industry,query=query+' | industry: '+industry)
        if intent['enabled']:
            for term in intent['process_terms'][1:]:
                add('Buyer process: '+term,**buyer_search.request(original,intent,term))
            for request,angle in buyer_search.localized_requests(base,intent,countries):
                add(angle,**request)
        else:
            for synonym in synonyms:add('用户产品同义词：'+synonym,query=query+' | product synonym: '+synonym,products=synonym)
            text=norm(products or query)
            if '齿轮' in text or 'gear' in text:
                add('产品英文检索词：gear workholding fixture',query=query+' | gear workholding fixture',products='gear workholding fixture')
                add('产品英文检索词：gear machining fixture',query=query+' | gear machining fixture',products='gear machining fixture')
            if 'cnc' in text or '夹具' in text or 'fixture' in text:
                add('产品英文检索词：CNC machining workholding',query=query+' | CNC machining workholding',products='CNC machining workholding')
            if kind=='终端加工厂':
                add('加工应用角度：precision machining shop',query=query+' precision machining shop')
                add('加工应用角度：gear machining factory',query=query+' gear machining factory')
            add('公开企业页面角度',query=query+' company official website')
            add('公开加工能力角度',query=query+' machining capabilities')
        inferred=[] if intent['enabled'] else [r['products'] for r in requests if r['products']!=products]
        seen={fingerprint(r) for r in requests}
        for request,angle in expand(base,regions,industries,[] if intent['enabled'] else synonyms,inferred):
            key=fingerprint(request)
            try:builder(request)
            except AppError:continue
            if key not in seen:seen.add(key);requests.append(request);angles.append(angle)
        omitted=max(0,len(requests)-maximum);coverage=list(zip(requests[maximum:],angles[maximum:]));requests=requests[:maximum];angles=angles[:maximum]
        conditions={'query':original['query'],'products':original['products'],'countries':original['countries'],'type':original['type'],'regions':regions,'industries':industries,'product_synonyms':synonyms,'buyer_intent':intent,'search_options':options}
        scope=hashlib.sha256(json.dumps([conditions,connection(self.service),maximum],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        existing=self.ledger.scope(scope)
        if existing:
            with self.lock:return public(self.load(existing))
        with self.lock:
            self.plans={k:v for k,v in self.plans.items() if v['expires']>time.monotonic() or v['pending']}
            self.capacity()
            inactive=[p for p in self.plans.values() if p['state']!='ready' and not p['pending']]
            while len(self.plans)>=MAX_RETAINED_PLANS and inactive:
                oldest=min(inactive,key=lambda p:p['expires']);inactive.remove(oldest);self.plans.pop(oldest['plan_id'])
            plan={'plan_id':secrets.token_hex(16),'state':'ready','created_at':now(),'expires':time.monotonic()+PLAN_TTL,
                  'conditions':conditions,'buyer_intent':intent,'query_builder':builder,'search_connection':connection(self.service),'requests':requests,'queries':[{'index':i,'query':r['query'],'executed_query':builder(r),'angle':angles[i]} for i,r in enumerate(requests)],
                  'maximum':maximum,'initial_preview_queries':maximum,'coverage':coverage,'zero_streak':0,'attempts':0,'next':0,'pending':None,'outcomes':{},'candidate_keys':set(),'batches':[]}
            pid=self.ledger.register(plan,scope);self.plans[pid]=plan if pid==plan['plan_id'] else self.load(pid);result=public(self.load(pid));result['omitted_queries']=omitted;return result
    def get(self,data):
        with self.lock:return public(self.load(plan_id(data) if data.get('plan_id') else self.ledger.latest()))
    def capacity(self,excluding=None):
        if sum(p['plan_id']!=excluding and (bool(p['pending']) or (p['state']=='ready' and p['expires']>time.monotonic())) for p in self.plans.values())>=MAX_PLANS:
            raise AppError('同时进行的搜索计划已达2个。完成或停止一个计划后可创建；历史计划不占活跃槽。',429,'search_plan_capacity')
    def stop(self,data):
        with self.lock:
            plan=self.load(plan_id(data))
            if plan['state'] in ('ready','complete'):plan.update(state='stopped',stop_reason='user_stopped',expires=time.monotonic()+PLAN_TTL)
            self.ledger.save(plan)
            return public(plan)
    def next(self,data):
        pid=plan_id(data);rid=field(data,'request_id',64,True)
        with self.lock:self.load(pid)
        self.ledger.acquire(pid,rid)
        try:return self._next(data)
        finally:
            with self.lock:self.ledger.save(self.plans[pid],release=True)
    def _next(self,data):
        pid=plan_id(data);rid=field(data,'request_id',64,True)
        if not re.fullmatch('[a-zA-Z0-9_-]{8,64}',rid):raise AppError('request_id 必须为8至64位字母数字、下划线或短横线')
        if data.get('consent') is not True:raise AppError('请明确同意执行计划中的本次公开查询',code='search_consent_required')
        with self.lock:
            plan=self.load(pid)
            if rid in plan['outcomes']:
                outcome=plan['outcomes'][rid]
                if outcome['ok']:return dict(copy.deepcopy(outcome['result']),replayed=True)
                e=AppError(outcome['message'],outcome['status'],outcome['code']);e.details=dict(copy.deepcopy(outcome['details']),replayed=True);raise e
            if plan['pending']:raise AppError('该计划已有一个查询正在进行',409,'search_plan_busy')
            if plan['search_connection']!=connection(self.service):raise AppError('搜索供应商或档位已变更，请重新预览计划；没有发出请求',409,'search_plan_config_changed')
            recoverable=plan['state']=='stopped' and plan.get('stop_reason') in ('user_stopped','provider_failure')
            extendable=plan['state']=='complete' or (recoverable and plan['next']>=len(plan['queries']))
            if plan['zero_streak']>=2 and data.get('acknowledge_zero_results') is not True:
                raise AppError('连续两批或更多没有新增企业。请调整条件，或明确确认仍继续本次查询。',409,'search_zero_ack_required')
            if recoverable:
                if data.get('resume') is not True:raise AppError('计划已暂停，需明确确认恢复或重试本次查询',409,'search_plan_closed')
                if plan.get('retry_at',0)>time.monotonic():
                    e=AppError('服务返回的等待窗口尚未结束，请稍后手动重试',429,'search_retry_wait');e.details={'retry_after_seconds':max(1,int(plan['retry_at']-time.monotonic())+1)};raise e
            if extendable:
                offer=proposal(plan)
                if not offer:raise AppError('当前公开条件的可用查询角度已用完，请调整产品、地区或行业条件',409,'search_coverage_exhausted')
                if data.get('extension_token')!=offer['extension_token']:raise AppError('请先核对新的公开查询预览再明确执行',409,'search_plan_closed')
            if plan['state']!='ready' and not recoverable and not extendable:raise AppError('计划已停止或完成，不会再发出请求',409,'search_plan_closed')
            self.capacity(excluding=pid)
            if extendable:
                request,angle=plan['coverage'].pop(0);plan['requests'].append(request);plan['queries'].append({k:v for k,v in offer.items() if k!='extension_token'})
            plan.update(state='ready',stop_reason='',maximum=max(plan['maximum'],plan['attempts']+1),expires=time.monotonic()+PLAN_TTL)
            if plan['next']>=len(plan['queries']):raise AppError('没有待执行查询',409,'search_plan_closed')
            index=plan['next'];request=copy.deepcopy(plan['requests'][index]);plan['pending']=rid
        called=False
        def attempt():
            nonlocal called
            with self.lock:
                if plan['state']!='ready':raise AppError('计划已停止，不会开始新的请求',409,'search_plan_closed')
                if called:raise AppError('单次查询不能重复调用服务',409,'search_plan_closed')
                if plan['attempts']>=plan['maximum']:raise AppError('搜索预算已用尽',409,'search_plan_closed')
                self.ledger.attempt(plan,index,rid);called=True
        try:
            response=search_workflow.search(self.service,dict(request,consent=True),_batch_limit=16,_attempt_hook=attempt,_rolling_completed=True)
            # An injected provider may not implement the transport hook; a success still counts as one request.
            if not called:attempt()
            items=[];duplicates=0
            with self.lock:
                with self.service.lock:
                    items,duplicates,new_sources,new_entities=self.ledger.evidence(plan,rid,response['items'])
                    kept={c['candidate_id'] for c in items}
                    for candidate in response['items']:
                        if candidate['candidate_id'] not in kept:self.service.jobs.pop(candidate['candidate_id'],None)
                        else:plan['candidate_keys'].add(candidate['identity_key'])
                    from .source_relations import attach
                    previous=[c for batch in plan['batches'] for c in batch['items']]
                    for candidate in items:
                        attach(candidate,previous+[c for c in items if c['candidate_id']!=candidate['candidate_id']])
                        if candidate.get('source_relation_warnings') and candidate['candidate_id'] in self.service.jobs:
                            self.service.jobs[candidate['candidate_id']]['source_relation_warnings']=copy.deepcopy(candidate['source_relation_warnings'])
                            self.service.jobs[candidate['candidate_id']]['match_hint']=copy.deepcopy(candidate['match_hint'])
                batch={k:response[k] for k in ['query','executed_query','searched_at','raw_result_count','filtered_count','deduplicated_count','excluded_existing_count','excluded_existing','notice']}
                batch.update(entity_summary=entity_candidates.summarize(items),url_rejection_diagnostics=response.get('url_rejection_diagnostics',[]),result_summary=response.get('result_summary',''),valid_result_count=response.get('valid_result_count'),filter_reasons=response.get('filter_reasons',[]),count_unit='source_pages_not_verified_companies',verified_company_count=0,provider_http_status=response.get('provider_http_status'),provider=response.get('provider','tavily'),engine_type=response.get('engine_type'),index=index,cross_batch_duplicate_count=duplicates,new_company_count=new_entities,new_source_page_count=new_sources,new_evidence_count=len(items),unresolved_company_page_count=sum(c.get('source_classification',{}).get('kind')=='company' and not c.get('entity_group') for c in items),new_candidate_count=len(items),directory_count=sum(c.get('source_classification',{}).get('kind')=='directory' for c in items),media_count=sum(c.get('source_classification',{}).get('kind')=='media' for c in items),unknown_source_count=sum(c.get('source_classification',{}).get('kind')=='unknown' for c in items),items=items)
                self.ledger.outcome(plan,rid,'succeeded',new_entities);plan['last_outcome']='new_entities' if new_entities else 'no_new_entities'
                plan['batches'].append(batch);plan['next']+=1;plan['pending']=None;plan['zero_streak']=0 if batch['new_company_count'] else plan['zero_streak']+1;plan['expires']=time.monotonic()+PLAN_TTL
                if plan['state']=='ready' and plan['next']>=len(plan['queries']):
                    plan.update(state='complete',stop_reason='budget_reached' if plan['coverage'] else 'queries_complete')
                result={'protocol':'search-v2','plan_id':pid,'request_id':rid,'replayed':False,'batch':batch,
                        'state':plan['state'],'buyer_intent':copy.deepcopy(plan['buyer_intent']),'budget':budget(plan),'next_query_index':plan['next'],'has_more':plan['state']=='ready'}
                plan['outcomes'][rid]={'ok':True,'result':copy.deepcopy(result)};return result
        except AppError as e:
            with self.lock:
                plan['pending']=None
                if e.code not in ('search_busy','website_busy','search_plan_closed'):plan.update(state='stopped',stop_reason='provider_failure')
                if called:
                    self.ledger.outcome(plan,rid,'failed');plan['next']=max(plan['next'],index+1);plan['last_outcome']='request_failed'
                if called:plan['retry_at']=time.monotonic()+max(0,getattr(e,'details',{}).get('retry_after_seconds',0))
                details=dict(getattr(e,'details',{}),plan_id=pid,plan_state=plan['state'],budget=budget(plan))
                e.details=details
                # Local busy does not consume a request: let the same button ID retry manually later.
                if e.code not in ('search_busy','website_busy'):plan['outcomes'][rid]={'ok':False,'message':str(e),'status':e.status,'code':e.code,'details':copy.deepcopy(details)}
            raise
        except Exception:
            with self.lock:
                if called:self.ledger.outcome(plan,rid,'outcome_unknown');plan['next']=max(plan['next'],index+1)
                plan.update(pending=None,state='stopped',stop_reason='provider_failure',last_outcome='outcome_unknown')
            raise
