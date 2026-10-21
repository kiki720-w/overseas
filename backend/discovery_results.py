"""Read existing previews and drafts. Never search, capture, or mutate storage."""
import json,time
from .common import AppError,norm
from .discovery import public_candidate
from . import search_identity,entity_candidates

def listing(service,query):
    try:
        page=int(query.get('page','1'));size=int(query.get('page_size','12'))
    except (TypeError,ValueError):raise AppError('分页参数无效')
    if page<1 or not 1<=size<=100:raise AppError('分页参数无效')
    state=query.get('state','');needle=query.get('q','');country=query.get('country','')
    if state not in ('','preview','draft'):raise AppError('候选状态无效')
    if not isinstance(needle,str) or len(needle)>200 or not isinstance(country,str) or len(country)>100:raise AppError('筛选条件无效')
    with service.lock:
        with service.store.db() as db:
            saved=[json.loads(r[0]) for r in db.execute('SELECT data FROM discovery_candidates ORDER BY rowid DESC')]
        now=time.monotonic()
        by_id={c['candidate_id']:c for c in service.jobs.values() if c.get('expires',0)>now and not c.get('pending')}
        # A persisted accepted/rejected state must override any stale in-memory preview.
        for c in saved:by_id[c['candidate_id']]=c
        existing=search_identity.index(service.store)
        available=[]
        for c in by_id.values():
            if c.get('state') not in ('preview','draft'):continue
            match,_,_=search_identity.match(existing,c.get('domain') or search_identity.host(c.get('url','')),c.get('suggested_name') or c.get('name',''),c.get('url',''))
            if match:continue
            available.append(public_candidate(c))
    available.sort(key=lambda c:(c.get('created_at',''),c['candidate_id']),reverse=True)
    all_count=len(available)
    if state:available=[c for c in available if c['state']==state]
    if country:available=[c for c in available if norm(c.get('country',''))==norm(country)]
    if needle:
        term=norm(needle)
        available=[c for c in available if term in norm(' '.join(str(c.get(k) or '') for k in ['suggested_name','name','country','url','domain'])+' '+(c.get('lead') or {}).get('display_name',''))]
    total=len(available);pages=max(1,(total+size-1)//size);page=min(page,pages)
    entity_summary=entity_candidates.summarize(available,annotate=True)
    return {'entity_summary':entity_summary,'protocol':'discovery-results-v1','items':available[(page-1)*size:page*size],'total':total,'total_all':all_count,'page':page,'pages':pages,'page_size':size,'notice':'已有候选；搜索摘要未经核验，未知位置不推断。预览沿用有效期，未自动搜索或入库。'}
