from .website_text import capture_evidence_level,capture_observations
"""Independent candidates, explicit acceptance, provenance and duplicate protection."""
import copy
import hashlib
import json
import re
import secrets
import threading
import time
from urllib.parse import urlsplit
from .common import AppError,field,dump,now,norm,paging,page_result,customer_data,TYPES,STAGES
from .webpage_url import normalize_page_url
from . import search_workflow
from .search_plan import SearchPlans

class SearchProvider:
    """Future authorized provider boundary; model credentials never enter this API."""
    configured=False
    def search(self,request):
        raise AppError('尚未启用在线搜索；需单独选择并授权搜索服务，模型Key不能代替搜索Key',503,'search_not_connected')

def identifier(data):
    value=field(data,'candidate_id',32,True)
    if not re.fullmatch('[a-f0-9]{32}',value):raise AppError('候选编号无效')
    return value

def website_domain(url):
    try:
        u=urlsplit(url);host=(u.hostname or '').encode('idna').decode('ascii').lower().rstrip('.')
        return host[4:] if host.startswith('www.') else host
    except (ValueError,UnicodeError):return ''

def public_candidate(candidate):
    value=copy.deepcopy({k:v for k,v in candidate.items() if k not in ('expires','pending','request_signature')})
    if value.get('capture') or value.get('source_kind')!='search_result':value.pop('lead',None)
    return search_workflow.refresh_reference_label(value)

def discovery_defaults(factory):
    """Suppress the known acceptance seed only in search defaults, without editing factory data."""
    products=factory.get('focus') or factory.get('products','')
    suppressed=norm(products)==norm('合成 CNC 齿轮夹具')
    return {'products':'' if suppressed else products,'countries':factory.get('countries','')},suppressed

def record_text(capture):
    lines=['客户官网公开背景（网站自述；非采购意向；不是独立证明）','人工确认仅认可官网与客户关联，不推断企业规模、决策人或当前需求。']
    for page in capture['pages']:
        lines.extend(['来源URL：'+page['url'],'抓取时间：'+page['fetched_at'],'页面标题：'+page['title']])
        lines.extend('官网原文：'+e['quote'] for e in page['evidence'])
    result='\n'.join(lines)
    if len(result)>12000:raise AppError('候选证据超过保存限制',413,'website_too_large')
    return result

class DiscoveryStore:
    def init_discovery(self,db):
        db.executescript('CREATE TABLE IF NOT EXISTS discovery_candidates(id TEXT PRIMARY KEY,data TEXT NOT NULL);CREATE TABLE IF NOT EXISTS discovery_events(id TEXT PRIMARY KEY,candidate_id TEXT NOT NULL,data TEXT NOT NULL);CREATE INDEX IF NOT EXISTS discovery_event_candidate ON discovery_events(candidate_id);CREATE TRIGGER IF NOT EXISTS discovery_events_immutable_update BEFORE UPDATE ON discovery_events BEGIN SELECT RAISE(ABORT,"candidate audit is immutable");END;CREATE TRIGGER IF NOT EXISTS discovery_events_immutable_delete BEFORE DELETE ON discovery_events BEGIN SELECT RAISE(ABORT,"candidate audit is immutable");END;')
    def discovery_candidates(self,q):
        p,s=paging(q);state=field(q,'state',20)
        if state not in ('','draft','accepted','rejected'):raise AppError('候选状态无效')
        with self.db() as db:items=[json.loads(r[0]) for r in db.execute('SELECT data FROM discovery_candidates ORDER BY rowid DESC')]
        if state:items=[x for x in items if x['state']==state]
        return page_result([public_candidate(x) for x in items[(p-1)*s:p*s]],len(items),p,s)
    def discovery_event(self,db,candidate,action,before):
        event={'candidate_id':candidate['candidate_id'],'action':action,'before':before,'after':candidate['state'],'created_at':now(),'customer_id':candidate.get('customer_id'),'evidence_level':candidate.get('evidence_level','website_self_statement'),'independently_proven':False}
        db.execute('INSERT INTO discovery_events VALUES(?,?,?)',(secrets.token_hex(16),candidate['candidate_id'],dump(event)))

class DiscoveryService:
    def __init__(self,store,websites,mode='local',search_provider=None):
        self.store=store;self.websites=websites;self.mode=mode;self.search_provider=search_provider or SearchProvider();self.jobs={};self.lock=threading.RLock();self.search_batches=[];self.researching=set()
        self.plans=SearchPlans(self)
        from .buyer_settings import BuyerSettings
        self.buyer_settings=BuyerSettings(store)
    def meta(self):
        f=self.store.factory()
        defaults,suppressed=discovery_defaults(f)
        result={'protocol':'discovery-v1','search':{'configured':bool(self.search_provider.configured),'status':'not_connected' if not self.search_provider.configured else 'configured','message':'尚未启用在线搜索' if not self.search_provider.configured else '搜索服务已配置'},'types':TYPES,'limits':{'pages':3,'preview_ttl_seconds':600,'pending_previews':5},'defaults':{'products':f.get('focus') or f.get('products',''),'countries':f.get('countries','')},'notice':'已知企业URL分析不是全网搜索；没有模型或搜索服务自动调用。'}
        if self.search_provider.configured:
            result['limits'].update(search_results=8,search_batches=5,search_ttl_seconds=600,search_cooldown_seconds=5)
            result['search'].update(status='configured_not_verified',protocol='search-v1',access_mode='bearer',message='独立搜索凭证已配置，认证与额度以实际请求为准')
        if hasattr(self.search_provider,'settings'):
            result['search'].update(self.search_provider.settings(),protocol='search-v1')
        result['defaults']=defaults
        result['defaults_source']='manual_required' if suppressed else 'factory_config'
        result['defaults_notice']='验收示例不会自动填入搜索，请填写实际产品或应用条件。' if suppressed else ''
        result['search'].update(plan_protocol='search-v2',plan_limits={'max_queries':8,'default_queries':6,'max_results_per_query':8,'max_provider_requests_per_click':1,'retained_plans':2,'plan_ttl_seconds':1800,'exclude_existing_customers':True,'manual_extension':True,'total_request_limit':None,'max_retained_plans':20,'zero_acknowledgement_after':2})
        return result
    def search(self,data):
        return search_workflow.search(self,data)
    def candidate(self,data):
        from .entity_candidates import summarize
        with self.lock:
            with self.store.db() as db:
                value=public_candidate(self.load(db,identifier(data)))
                summarize([value],annotate=True)
                if not value.get('entity_group'):return value
                publisher=website_domain(value.get('url',''))
                peers={c['candidate_id']:c for c in self.jobs.values() if c.get('expires',0)>time.monotonic() and c.get('state') in ('preview','draft') and website_domain(c.get('url',''))==publisher}
                for row in db.execute('SELECT data FROM discovery_candidates'):
                    c=json.loads(row[0])
                    if website_domain(c.get('url',''))==publisher:peers[c['candidate_id']]=c
                peers={cid:public_candidate(c) for cid,c in peers.items() if c.get('state') in ('preview','draft')}
                peers[value['candidate_id']]=value
                summarize(list(peers.values()),annotate=True)
                return peers[value['candidate_id']]
    def preview(self,data):
        return search_workflow.preview(self,data)
    def match(self,capture):
        from .discovery_quality import match_hint
        from .application_recall import CONCEPTS
        knowledge=[];seen_queries=set()
        # Bounded read-only local KB retrieval; normal expiry/review visibility rules remain authoritative.
        for concept in CONCEPTS:
            if concept['query'] in seen_queries:continue
            seen_queries.add(concept['query'])
            knowledge.extend(self.store.knowledge_search({'q':concept['query']}).get('items',[])[:3])
        return match_hint(self.store.factory(),capture,knowledge[:6])
    def _preview_url(self,data):
        url=normalize_page_url(field(data,'url',500,True))
        if data.get('consent') is not True:raise AppError('请明确同意读取该公开官网',code='website_consent_required')
        with self.lock:
            self.jobs={k:v for k,v in self.jobs.items() if v['expires']>time.monotonic()}
            if sum(v.get('source_kind')!='search_result' for v in self.jobs.values())>=5:raise AppError('待确认候选过多，请暂存或放弃后重试',429,'website_busy')
            cid=secrets.token_hex(16);self.jobs[cid]={'expires':time.monotonic()+600,'pending':True}
        if not self.websites.slots.acquire(False):
            with self.lock:self.jobs.pop(cid,None)
            raise AppError('已有采集正在进行，请稍后重试',429,'website_busy')
        try:
            capture=self.websites.collect(url)
            candidate={'candidate_id':cid,'state':'preview','saved':False,'created_at':now(),'suggested_name':capture['pages'][0]['title'],'capture':capture,'match_hint':self.match(capture),'expires':time.monotonic()+600,'expires_in':600,'source_kind':'known_company_url','evidence_level':'website_self_statement','independently_proven':False}
            from .discovery_quality import classified_candidate
            candidate=classified_candidate(candidate)
            with self.lock:self.jobs[cid]=candidate
            return public_candidate(candidate)
        except Exception:
            with self.lock:self.jobs.pop(cid,None)
            raise
        finally:self.websites.slots.release()
    def load(self,db,cid):
        row=db.execute('SELECT data FROM discovery_candidates WHERE id=?',(cid,)).fetchone()
        if row:return json.loads(row[0])
        candidate=self.jobs.get(cid)
        if not candidate or candidate['expires']<=time.monotonic() or candidate.get('pending'):raise AppError('候选预览已过期或未完成，请重新分析',410,'candidate_expired')
        return copy.deepcopy(candidate)
    def stash(self,data):
        cid=identifier(data)
        if data.get('consent') is not True:raise AppError('请明确确认暂存候选',code='candidate_consent_required')
        with self.lock:
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE');c=self.load(db,cid)
                if c['state']=='draft':return dict(public_candidate(c),replayed=True)
                if c['state']!='preview':raise AppError('候选已处理，不能重复暂存',409,'candidate_state_conflict')
                c=public_candidate(c);c.update(state='draft',saved=True,expires_in=None,updated_at=now());db.execute('INSERT INTO discovery_candidates VALUES(?,?)',(cid,dump(c)));self.store.discovery_event(db,c,'stash','preview')
            self.jobs.pop(cid,None)
        return dict(public_candidate(c),replayed=False)
    def reject(self,data):
        cid=identifier(data)
        with self.lock:
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE');c=self.load(db,cid)
                if c['state']=='accepted':raise AppError('已加入客户的候选不能放弃；不会删除客户',409,'candidate_state_conflict')
                if c['state']=='rejected':return {'candidate_id':cid,'state':'rejected','replayed':True}
                before=c['state'];c=public_candidate(c);c.update(state='rejected',saved=True,expires_in=None,updated_at=now());db.execute('INSERT OR REPLACE INTO discovery_candidates VALUES(?,?)',(cid,dump(c)));self.store.discovery_event(db,c,'reject',before)
            self.jobs.pop(cid,None)
        return {'candidate_id':cid,'state':'rejected','replayed':False}
    def confirm(self,data):
        cid=identifier(data)
        if data.get('consent') is not True:raise AppError('请核对来源后明确认可并加入客户开发',code='candidate_consent_required')
        existing=field(data,'existing_customer_id',32);name=field(data,'name',200,not bool(existing));kind=field(data,'type',30,not bool(existing));country=field(data,'country',100)
        if not existing and kind not in TYPES:raise AppError('请为候选选择客户类型')
        expected=data.get('expected_customer_version')
        identity_review=data.get('company_identity_review')
        if identity_review is not None and (not isinstance(identity_review,dict) or identity_review.get('confirmed') is not True or not isinstance(identity_review.get('basis'),str) or not 10<=len(identity_review['basis'].strip())<=1000):raise AppError('无法确认页面角色时，需要单独核对企业身份并填写依据',400,'candidate_identity_review_required')
        signed={'existing':existing,'name':name,'type':kind,'country':country,'expected':expected}
        if identity_review is not None:signed['company_identity_review']=identity_review
        signature=hashlib.sha256(dump(signed).encode()).hexdigest()
        with self.lock:
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE');c=self.load(db,cid)
                if c['state']=='accepted':
                    if c.get('request_signature')!=signature:raise AppError('已成功处理的候选不能改为另一个客户或改变确认内容',409,'candidate_request_conflict')
                    row=db.execute('SELECT data FROM customers WHERE id=?',(c['customer_id'],)).fetchone()
                    return dict(public_candidate(c),customer=json.loads(row[0]),replayed=True)
                if c['state']=='rejected':raise AppError('候选已放弃，不能加入客户',409,'candidate_state_conflict')
                if not c.get('capture') and ((c.get('source_classification') or {}).get('kind') in ('directory','media','third_party_document') or (c.get('source_classification') or {}).get('page_role')=='product_material'):raise AppError('目录、媒体或归属未核实文件不能直接作为公司客户；请先研究并核对企业来源',409,'candidate_not_company')
                if not c.get('capture'):raise AppError('先研究候选公开原文，再确认加入客户',409,'candidate_research_required')
                from .discovery_quality import classified_candidate
                c['match_hint']=self.match(c['capture'])  # Revalidate current local knowledge state without external calls.
                c=classified_candidate(c)  # Saved/legacy candidates never bypass current capture-role validation.
                if c['source_classification']['kind'] in ('directory','media','third_party_document') or c['source_classification'].get('page_role')=='product_material':raise AppError('目录、媒体或归属未核实文件不能直接作为公司客户；请先研究并核对企业来源',409,'candidate_not_company')
                if c['entity_review_required'] and identity_review is None:raise AppError('页面角色未知；请单独确认真实企业身份并填写核对依据',409,'candidate_identity_review_required')
                if identity_review is not None:c['company_identity_review']=identity_review
                capture=c['capture'];site=capture['pages'][0]['url'];domain=website_domain(site)
                if existing:
                    row=db.execute('SELECT data FROM customers WHERE id=?',(existing,)).fetchone()
                    if not row:raise AppError('关联客户不存在',404,'not_found')
                    customer=json.loads(row[0])
                    if type(expected) is not int or expected!=customer['version']:raise AppError('关联客户已变更，请重新核对',409,'candidate_customer_changed')
                else:
                    for (raw,) in db.execute('SELECT data FROM customers'):
                        old=json.loads(raw)
                        if norm(old['name'])==norm(name) or domain and website_domain(old.get('website',''))==domain:
                            error=AppError('客户库已有同名或相同官网的客户；请核对并选择关联，不会重复创建',409,'candidate_duplicate');error.details={'duplicate_customer':{k:old.get(k,'') for k in ('id','name','website','version')}};raise error
                    emails=[e for p in capture['pages'] for e in p.get('public_emails',[])]
                    customer=customer_data({'name':name,'type':kind,'country':country,'stage':STAGES[0],'website':site,'email':emails[0][:200] if emails else '', 'product':'、'.join(c['match_hint']['matched_terms'])[:500],'source':'官网自述，用户认可关联；非采购意向\n'+'\n'.join(p['url']+' / '+p['fetched_at'] for p in capture['pages']),'source_date':capture['fetched_at'][:10],'facts':'官网自述（非独立证明）：\n'+'\n'.join(e['quote'] for p in capture['pages'] for e in p['evidence'])[:2200],'unknowns':'真实采购计划、企业规模、决策人、联系方式有效性与具体需求未知。'})
                    customer.update(id=secrets.token_hex(8),demo=self.mode=='demo',version=1,updated_at=now());self.store.insert(db,customer)
                website_capture=dict(capture,id=cid,customer_id=customer['id'],confirmed_at=now(),evidence_level=capture_evidence_level(capture),independently_proven=False)
                record={'id':secrets.token_hex(8),'customer_id':customer['id'],'kind':'反馈','text':record_text(capture),'next_date':'','created_at':now(),'website_capture_id':cid,'evidence_level':capture_evidence_level(capture),'source_observations':capture_observations(capture)}
                if c.get('company_identity_review'):
                    website_capture['company_identity_review']=c['company_identity_review'];record['company_identity_review']=c['company_identity_review']
                    record['text']+='\n人工企业身份核对（非采购或独立能力证明）：'+c['company_identity_review']['basis']
                if c.get('search_provenance'):
                    record['search_provenance']=c['search_provenance'];website_capture['search_provenance']=c['search_provenance']
                website_capture['record_id']=record['id'];db.execute('INSERT INTO records VALUES(?,?,?)',(record['id'],customer['id'],dump(record)));db.execute('INSERT INTO website_captures VALUES(?,?,?)',(cid,customer['id'],dump(website_capture)))
                before=c['state'];c=public_candidate(c);c.update(state='accepted',saved=True,expires_in=None,customer_id=customer['id'],record_id=record['id'],request_signature=signature,updated_at=now());db.execute('INSERT OR REPLACE INTO discovery_candidates VALUES(?,?)',(cid,dump(c)));self.store.discovery_event(db,c,'link_existing' if existing else 'create_customer',before)
            self.jobs.pop(cid,None)
        return dict(public_candidate(c),customer=customer,replayed=False)
