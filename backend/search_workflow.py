"""Search snippets remain candidates until website research and explicit acceptance."""
import copy
import re
import secrets
import time
from urllib.parse import urlsplit
from .common import AppError,field,now,dump,TYPES
from .webpage_url import inspect_page_url,normalize_page_url
from . import search_identity,entity_candidates
from .discovery_quality import classify_page,source_type_for
from .fetch_quality import assess

DIRECTORIES={'techpilot.com','companyhouse.de','rocketreach.co','leadiq.com','linkedin.com','zoominfo.com','crunchbase.com','dnb.com','alibaba.com','globalspec.com','thomasnet.com','kompass.com','made-in-china.com','facebook.com'}

def candidate_id(data):
    value=field(data,'candidate_id',32,True)
    if not re.fullmatch('[a-f0-9]{32}',value):raise AppError('候选标识无效')
    return value

def refresh_reference_label(candidate):
    """Current labels for legacy search previews; only changes a public copy."""
    if candidate.get('source_kind')!='search_result' or candidate.get('capture'):return candidate
    provenance=candidate.get('search_provenance') or {}
    url=provenance.get('source_url') or candidate.get('url','')
    from .source_evidence import attach
    attach(candidate)
    if not isinstance(url,str) or candidate['source_classification']['kind'] not in ('directory','media','third_party_document'):return candidate
    classification=candidate['source_classification']
    candidate.update(source_classification=classification,source_type=source_type_for(classification))
    candidate.setdefault('match_hint',{}).update(status='source_reference_only',assessment_state='not_company_candidate',matched_terms=[],notice=classification['label']+'；仅作参考来源，企业身份与采购需求待核实。')
    return candidate

def search(service,data,_batch_limit=5,_attempt_hook=None,_rolling_completed=False):
    query=field(data,'query',500,True);products=field(data,'products',500);countries=field(data,'countries',200);kind=field(data,'type',30)
    if kind and kind not in TYPES:raise AppError('客户类型无效')
    if data.get('consent') is not True:raise AppError('请明确确认公开搜索条件',code='search_consent_required')
    request={'query':query,'products':products,'countries':countries,'type':kind}
    if 'count' in data:
        count=data['count']
        if type(count) is not int or not 1<=count<=50:raise AppError('count 必须为1–50整数；没有发出请求',400,'invalid_input')
        settings=service.search_provider.settings() if hasattr(service.search_provider,'settings') else {}
        if settings.get('provider')!='deepseek':raise AppError('显式 count 仅适用于 DeepSeek；没有切换供应商或发出请求',400,'invalid_input')
        request['count']=count
    for key in ('need_content','ori_question'):
        if key in data:request[key]=data[key]
    if _attempt_hook:request['_attempt_hook']=_attempt_hook
    with service.lock:
        service.jobs={k:v for k,v in service.jobs.items() if v['expires']>time.monotonic()}
        service.search_batches=[x for x in service.search_batches if x['expires']>time.monotonic()]
        if _rolling_completed:
            # Retain bounded completed reservations; in-flight requests are never evicted.
            while len(service.search_batches)>=_batch_limit:
                completed=next((b for b in service.search_batches if b.get('completed')),None)
                if completed is None:break
                service.search_batches.remove(completed)
        if len(service.search_batches)>=_batch_limit:raise AppError('临时候选结果过多，请稍后再搜索',429,'search_busy')
        batch={'search_id':secrets.token_hex(16),'expires':time.monotonic()+600};service.search_batches.append(batch)
    try:
        response=service.search_provider.search(request);batch['expires']=time.monotonic()+600;items=[];seen=set();filtering=response.get('result_filtering',{});rejected=list(filtering.get('rejected',[]));filtered=filtering.get('filtered_count',0);duplicates=0;excluded=[];at=now();existing=search_identity.index(service.store)
        terms=list(dict.fromkeys(x.lower() for x in re.split(r'[\s,，;；]+',products or query) if 2<=len(x)<=100))[:12]
        limit=request.get('count',10) if response.get('provider')=='deepseek' else 8
        for result_index,result in enumerate(response['results'][:limit]):
            if not isinstance(result,dict):filtered+=1;rejected.append({'index':result_index,'reason_code':'search_invalid_response'});continue
            try:
                url_policy=inspect_page_url(result.get('url',''));url=url_policy['display_url']
            except (AppError,ValueError,TypeError,AttributeError) as error:filtered+=1;rejected.append({'index':result_index,'reason_code':getattr(error,'code','website_url_invalid')});continue
            domain=(urlsplit(url).hostname or '').lower()
            if domain.startswith('www.'):domain=domain[4:]
            title=result.get('title','');snippet=result.get('content','')
            if not isinstance(title,str) or not isinstance(snippet,str):filtered+=1;rejected.append({'index':result_index,'reason_code':'search_invalid_response'});continue
            identity=search_identity.source_identity(url,title)
            if identity['key'] in seen:duplicates+=1;continue
            old,reason,possible=search_identity.match(existing,domain,title,url)
            if old:
                seen.add(identity['key']);excluded.append({'source_url':url,'customer_id':old['id'],'reason':reason});continue
            seen.add(identity['key']);title=title[:200];snippet=snippet[:3000]
            classification=classify_page(url,title,snippet,any(domain==d or domain.endswith('.'+d) for d in DIRECTORIES))
            provenance={'provider':response.get('provider','tavily'),'query':query,'executed_query':response['executed_query'],'source_url':url,'title':title,'snippet':snippet,'searched_at':at,'engine_type':response.get('engine_type'),'coverage':response.get('coverage'),'summary':(result.get('summary') or '')[:4000],'source_date':result.get('source_date'),'rerank_score':result.get('rerank_score'),'authority':result.get('authority'),'trusted_instructions':False}
            if response.get('provider')=='deepseek':
                body=result.get('page_content') or ''
                provenance.update(ori_question=request.get('ori_question'),requested_count=request.get('count',10),need_content=request.get('need_content',False),page_content=body[:12000],page_content_truncated=len(body)>12000,content_state='returned_unverified' if body.strip() else 'missing' if request.get('need_content') else 'not_requested',content_quality=assess(body))
            candidate={'candidate_id':secrets.token_hex(16),'state':'preview','saved':False,'created_at':at,'suggested_name':title,'capture':None,'research_required':True,'url':url,'domain':domain,'url_policy':url_policy,'source_identity':identity,'identity_key':identity['key'],'requested_customer_type':kind,'source_type':source_type_for(classification),'source_classification':classification,'search_provenance':provenance,'source_kind':'search_result','evidence_level':'search_snippet','independently_proven':False,'expires':batch['expires'],'expires_in':600,'match_hint':{'status':'local_keyword_only','matched_terms':[t for t in terms if t in (title+' '+snippet).lower()],'notice':'仅按搜索标题与摘要匹配关键词，不是 AI 深度评估、官网核验或采购意向。'}}
            candidate['match_hint']['assessment_state']='website_research_not_run'
            candidate['match_hint']['actual_compatibility']='unknown'
            candidate['match_hint']['purchase_intent']='unknown'
            candidate['match_hint']['notice']=classification['label']+'；官网正文及语义分析尚未运行。'+candidate['match_hint']['notice']
            if classification['kind'] in ('directory','media','third_party_document'):
                candidate['match_hint'].update(status='source_reference_only',assessment_state='not_company_candidate',matched_terms=[],notice=classification['label']+'；仅保留为发现线索，不能作为公司客户或产品适配结论。')
            items.append(candidate)
            if possible:candidate['possible_existing']=possible
        entity_summary=entity_candidates.summarize(items,annotate=True)
        with service.lock:
            for candidate in items:service.jobs[candidate['candidate_id']]=candidate
        with service.lock:batch['completed']=True
        raw_count=filtering.get('received_count',len(response['results']))
        valid_count=filtering.get('valid_count',len(response['results']))-(filtered-filtering.get('filtered_count',0))
        omitted=max(0,len(response['results'])-limit)
        reasons=', '.join(dict.fromkeys(v['reason_code'] for v in rejected)) or '无'
        summary=f"来源网页 {raw_count} 条；有效 {valid_count} 条；过滤 {filtered} 条（原因码：{reasons}）；重复页面 {duplicates} 条；已入库排除 {len(excluded)} 条；保留 {len(items)} 项来源线索，企业数量尚未核实"+(f'；本批展示上限另省略 {omitted} 条有效网页' if omitted else '')
        return {'entity_summary':entity_summary,'url_rejection_diagnostics':filtering.get('url_rejection_diagnostics',[]),'result_summary':summary,'provider_http_status':response.get('provider_http_status'),'source_pages':[{'title':v.get('title',''),'url':v.get('url',''),'snippet':v.get('content','')[:3000],'summary':(v.get('summary') or '')[:4000],'source_date':v.get('source_date')} for v in response['results']] if response.get('provider')=='deepseek' else None,'engine_type':response.get('engine_type'),'usage':response.get('usage'),'protocol':'search-v1','search_id':batch['search_id'],'provider':response.get('provider','tavily'),'access_mode':'bearer','query':query,'executed_query':response['executed_query'],'searched_at':at,'expires_in':600,'items':[{k:v for k,v in c.items() if k!='expires'} for c in items],'raw_result_count':raw_count,'valid_result_count':valid_count,'display_limit_omitted_count':omitted,'filter_reasons':rejected,'count_unit':'source_pages_not_verified_companies','verified_company_count':0,'requested_customer_type':kind,'filtered_count':filtered,'deduplicated_count':duplicates,'excluded_existing_count':len(excluded),'excluded_existing':excluded,'notice':'搜索摘要未经官网核验；目录页面已标记，已入库客户已排除。先研究公开原文，再确认加入客户。' if items else '本次没有新的可用候选；可能已在客户库或没有搜索结果。未生成模拟客户。'}
    except Exception:
        with service.lock:service.search_batches.remove(batch)
        raise

def preview(service,data):
    if not data.get('candidate_id'):return service._preview_url(data)
    cid=candidate_id(data);url=inspect_page_url(field(data,'url',500,True))['display_url'];fetch_url=normalize_page_url(url)
    if data.get('consent') is not True:raise AppError('请明确同意读取该公开官网',code='website_consent_required')
    with service.lock:
        with service.store.db() as db:origin=service.load(db,cid)
        if origin.get('source_kind')!='search_result' or origin['state'] not in ('preview','draft') or origin['url']!=url:raise AppError('搜索候选状态或网址已变化，请重新核对',409,'candidate_state_conflict')
        if cid in service.researching:raise AppError('候选研究正在进行',429,'website_busy')
        service.researching.add(cid)
    generated=None
    try:
        generated=service._preview_url({'url':fetch_url,'consent':True})
        updated=copy.deepcopy(generated);updated.update(candidate_id=cid,search_provenance=origin['search_provenance'],source_kind='search_result',url=origin['url'],domain=origin['domain'],search_source_classification=origin.get('source_classification'),research_required=False)
        with service.lock:
            with service.store.db() as db:
                db.execute('BEGIN IMMEDIATE');current=service.load(db,cid)
                if current['state'] not in ('preview','draft'):raise AppError('候选状态已变化，研究结果未覆盖',409,'candidate_state_conflict')
                if current['state']=='draft':
                    updated.update(state='draft',saved=True,expires_in=None,updated_at=now())
                    db.execute('UPDATE discovery_candidates SET data=? WHERE id=?',(dump(updated),cid));service.store.discovery_event(db,updated,'research','draft')
                else:service.jobs[cid]=dict(updated,expires=time.monotonic()+600)
            service.jobs.pop(generated['candidate_id'],None)
        return updated
    finally:
        with service.lock:
            service.researching.discard(cid)
            if generated:service.jobs.pop(generated['candidate_id'],None)
