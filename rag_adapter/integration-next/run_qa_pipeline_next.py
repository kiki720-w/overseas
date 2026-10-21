"""Query-only real local retrieval + canonical SQLite evidence assessment.

NEVER opens gold, generates answers, issues consent or touches a production DB.
"""
import argparse
import copy
import dataclasses as dc
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import psutil

HERE=Path(__file__).resolve().parent;ROOT=HERE.parent
sys.path[:0]=[str(HERE),str(ROOT/'answerability-next'),str(ROOT)]
from canonical import CanonicalSnapshot,write_local_owner_scope,read
from assessment import make_plan,assess
from preview_gate.gate import RequestPlan,Requirement
from preview_gate.state_api import apply_state_overrides
from rag.cleaned_v2 import adapt_semantic_v2,semantic_search,cover_requested_models
from rag.core import VectorCache

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def artifact_digest(directory):
    m=read(Path(directory)/'rag_model_manifest.json')
    data={k:m[k] for k in ('model_id','revision','license','sha256')}
    return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def overlay(records,fixture):
    if not fixture:return tuple(records)
    mode=fixture.get('mode')
    if mode=='authoritative_overrides':
        if set(fixture)-{'mode','overrides','after_rerank_overrides','notice'}:raise ValueError('unsupported fixture fields')
        # Validate both stages NOW, including when a query retrieves no results.
        apply_state_overrides(records,fixture.get('after_rerank_overrides',{}))
        return apply_state_overrides(records,fixture.get('overrides',{}))
    if mode=='product_default':
        if set(fixture)-{'mode','excluded_candidate_ids','notice'}:raise ValueError('unsupported fixture fields')
        ids={r.candidate_id for r in records}
        if any(cid in ids for cid in fixture['excluded_candidate_ids']):raise ValueError('excluded corpus candidate unexpectedly present')
        return tuple(records) # Excluded audit candidates never become product candidates.
    if mode=='isolated_version_overlay':
        if set(fixture)-{'mode','candidate_id','current_version','stale_copy_version','current_text_unchanged','notice'}:raise ValueError('unsupported fixture fields')
        cid=fixture['candidate_id'];current=fixture['current_version'];stale=fixture['stale_copy_version']
        if type(current) is not int or type(stale) is not int or current<=stale or stale<1:raise ValueError('invalid version fixture')
        changed=apply_state_overrides(records,{cid:{'version':current,'current_version':current}})
        # Synthetic version is explicitly NOT a real canonical mapping at that version.
        return tuple(dc.replace(r,canonical=False) if r.candidate_id==cid else r for r in changed)
    raise ValueError('unsupported fixture mode: '+str(mode))

def fresh(snapshot,fixture,after=False):
    records=overlay(snapshot(),fixture)
    if after and fixture and fixture.get('mode')=='authoritative_overrides':
        records=apply_state_overrides(records,fixture.get('after_rerank_overrides',{}))
    return records

def candidates_from_db(corpus,snapshot,records):
    schema=read(corpus/'schema.json')
    if schema['version']!='cleaned-v2-candidate':raise ValueError('v2 required')
    source=read(corpus/'semantic-chunks.json');by_id={r.candidate_id:r for r in records};rows=[]
    for raw in source:
        cid=raw['candidate_chunk_id'];r=by_id.get(cid)
        if not r:raise ValueError('corpus candidate lacks migration mapping')
        if not eligible(r):continue
        new=copy.deepcopy(raw);new['text']=r.text;new['_index_version']=r.version
        new['review']=dict(new['review'],enabled=r.enabled,review_status=r.review_status)
        p=snapshot.details[cid]['chunk'].get('metadata',{}).get('structured_parameters',{})
        if p:new['_model_cells']=p['cells'];new['_primary_model_verbatim']=p['model_verbatim']
        rows.append(new)
    return adapt_semantic_v2(rows)

def state(records):return {r.candidate_id:{'enabled':r.enabled,'review_status':r.review_status,
    'current_version':r.current_version} for r in records}

def eligible(r):
    if not r.read_authorized or not r.enabled or r.version!=r.current_version or r.review_status not in ('confirmed','source_checked','unverified'):return False
    if r.expires_on:
        try:
            if dt.date.fromisoformat(r.expires_on)<dt.date.today():return False
        except ValueError:return False
    return True

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--queries',type=Path);parser.add_argument('--corpus',type=Path)
    parser.add_argument('--db',required=True,type=Path);parser.add_argument('--lineage',type=Path)
    parser.add_argument('--final-ready',required=True,type=Path)
    parser.add_argument('--owner-scope',type=Path);parser.add_argument('--create-local-owner-scope',type=Path)
    parser.add_argument('--output',type=Path);parser.add_argument('--config',type=Path,default=ROOT/'evaluation/qa-config.json')
    parser.add_argument('--phase',choices=('all','e5','rerank'),default='all')
    args=parser.parse_args()
    for name in ('queries','corpus','db','lineage','owner_scope','create_local_owner_scope','output','config','final_ready'):
        value=getattr(args,name)
        if value is not None:setattr(args,name,value.resolve())
    ready=read(args.final_ready)
    if not ready.get('final') or ready.get('production_import') or ready.get('receipt_rolled_back'):
        raise ValueError('final unmoved isolation package required')
    if args.db.resolve()!=Path(ready['database']).resolve() or digest(args.db)!=ready['database_sha256']:
        raise ValueError('final SQLite hash/path mismatch')
    if args.create_local_owner_scope:
        write_local_owner_scope(args.db,args.create_local_owner_scope);print('Explicit isolation read owner scope created; send empty');return
    if not all((args.queries,args.corpus,args.lineage,args.output)):parser.error('queries/corpus/lineage/output required')
    if not args.owner_scope:parser.error('explicit isolation local owner scope required; missing permission is not a semantic quality result')
    if args.lineage.resolve()!=Path(ready['canonical_chunk_map']).resolve():raise ValueError('final canonical map required')
    config=read(args.config)
    for key in ('HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE','HF_HUB_DISABLE_TELEMETRY','HF_HUB_DISABLE_IMPLICIT_TOKEN'):os.environ[key]='1'
    os.environ['TOKENIZERS_PARALLELISM']='false'
    args.output.parent.mkdir(parents=True,exist_ok=True)
    cache_home=Path(os.environ.get('RAG_LOCAL_CACHE', str(args.output.parent/'runtime-cache')));cache_home.mkdir(exist_ok=True)
    os.environ['HF_HOME']=str(cache_home);os.environ['HF_HUB_CACHE']=str(cache_home/'hub')
    os.environ['TRANSFORMERS_CACHE']=str(cache_home/'transformers')
    intermediate=args.output.with_suffix('.e5-candidates.json');resource=args.output.with_suffix('.resources.json')
    snapshot=CanonicalSnapshot(args.db,args.lineage,args.owner_scope)
    # Fail early on unsupported fixture before allocating any model.
    queries=read(args.queries)
    for q in queries:overlay(snapshot(),q.get('state_fixture'))
    if args.phase=='all':
        for phase in ('e5','rerank'):
            if psutil.virtual_memory().available<config['minimum_start_ram_mib']*1024**2:raise RuntimeError('RAM insufficient; retry when free')
            command=[sys.executable,str(Path(__file__).resolve()),'--queries',str(args.queries),'--corpus',str(args.corpus),
                '--db',str(args.db),'--lineage',str(args.lineage),'--final-ready',str(args.final_ready),
                '--output',str(args.output),'--config',str(args.config),'--phase',phase]
            if args.owner_scope:command+=['--owner-scope',str(args.owner_scope)]
            subprocess.run(command,check=True,cwd=str(ROOT))
        print('Real retrieval + evidence coverage complete:',args.output);return
    if psutil.virtual_memory().available<config['minimum_start_ram_mib']*1024**2:raise RuntimeError('RAM insufficient before local CPU model load')
    import torch
    torch.set_num_threads(config['cpu_threads']);torch.set_num_interop_threads(1)
    from rag.adapters import LocalE5Embedder,LocalMiniLMReranker
    started=time.perf_counter();provider=LocalE5Embedder(config['embedding_dir']) if args.phase=='e5' else LocalMiniLMReranker(config['rerank_dir'])
    load_seconds=time.perf_counter()-started
    if psutil.virtual_memory().available<config['minimum_running_ram_mib']*1024**2:raise RuntimeError('RAM reserve insufficient after load')
    cases=[];latencies=[]
    if args.phase=='e5':
        cache=VectorCache(cache_home/'vectors/rag_vectors.sqlite3')
        for q in queries:
            rows=fresh(snapshot,q.get('state_fixture'));candidates=candidates_from_db(args.corpus,snapshot,rows)
            started=time.perf_counter()
            result=semantic_search(candidates,q['query'],provider,cache,min_cosine=config['dense_min_cosine'],
                top_k=config['candidate_k'],state_snapshot=lambda:state(fresh(snapshot,q.get('state_fixture'))))
            lexical=semantic_search(candidates,q['query'],top_k=config['final_k'],state_snapshot=lambda:state(fresh(snapshot,q.get('state_fixture'))))
            elapsed=(time.perf_counter()-started)*1000;latencies.append(elapsed)
            cases.append({'case_id':q['case_id'],'query':q['query'],'state_fixture':q.get('state_fixture'),
                'request_plan':q.get('request_plan'),'selected_candidate_ids':q.get('selected_candidate_ids'),
                'candidates':result['items'],'bm25_candidate_ids':[i['candidate_chunk_id'] for i in lexical['items']],
                'e5_latency_ms':elapsed})
        intermediate.write_text(json.dumps({'cases':cases},ensure_ascii=False,indent=2),encoding='utf-8')
    else:
        for c in read(intermediate)['cases']:
            items=c['candidates'];started=time.perf_counter()
            scores=list(provider.score(c['query'],[i['text'] for i in items])) if items else []
            order=sorted(range(len(items)),key=lambda i:(-scores[i],-items[i]['score'],items[i]['candidate_chunk_id']))
            ranked=cover_requested_models([dict(items[i],raw_rerank_logit=scores[i]) for i in order],c['query'],config['final_k'])
            assessment_candidates=[i['candidate_chunk_id'] for i in ranked]
            rows=fresh(snapshot,c.get('state_fixture'),after=True);by_id={r.candidate_id:r for r in rows}
            ranked=[i for i in ranked if i['candidate_chunk_id'] in by_id and eligible(by_id[i['candidate_chunk_id']])]
            if c.get('request_plan'):
                raw=c['request_plan']
                if set(raw)-{'requirements','scope_revision'}:raise ValueError('unsupported request_plan fields')
                plan=RequestPlan(c['query'],tuple(Requirement(**r) for r in raw['requirements']),raw.get('scope_revision',1));unknown=[]
            else:plan,unknown=make_plan(c['query'],snapshot.registry)
            # QA candidate assessment is NOT user selection or send consent. Explicit
            # user scope is preserved if supplied; never silently replace that selection.
            selections=c['selected_candidate_ids'] if c['selected_candidate_ids'] is not None else assessment_candidates
            assessment=assess(plan,rows,selections,unknown)
            elapsed=(time.perf_counter()-started)*1000;latencies.append(elapsed)
            cases.append(dict(assessment,case_id=c['case_id'],ranked_candidate_ids=[i['candidate_chunk_id'] for i in ranked],
                human_boundary_verdict='pending',stale_or_disabled_returned=False,bm25_candidate_ids=c['bm25_candidate_ids'],
                hybrid_candidate_ids=[i['candidate_chunk_id'] for i in items[:config['final_k']]],
                e5_latency_ms=c['e5_latency_ms'],rerank_and_gate_latency_ms=elapsed,
                selection_scope='explicit_qa_selected' if c['selected_candidate_ids'] is not None else 'qa_candidate_assessment_only',
                user_selections=[],citation_status='isolated_canonical_exact_chunk_only'))
        files=[*sorted(HERE.glob('*.py')),*sorted((ROOT/'answerability-next/preview_gate').glob('*.py')),*sorted((ROOT/'rag').glob('*.py'))]
        hashes={p.relative_to(ROOT).as_posix():digest(p) for p in files}
        metadata={'pipeline':'local_bm25_dense_rrf_rerank','gate_protocol':'evidence-gate-next-v1-isolated-canonical',
            'corpus_sha256':digest(args.corpus/'semantic-chunks.json'),
            'implementation_sha256':hashlib.sha256(json.dumps(hashes,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
            'implementation_files':hashes,'config_sha256':digest(args.config),
            'embedding_artifact_sha256':artifact_digest(config['embedding_dir']),'rerank_artifact_sha256':artifact_digest(config['rerank_dir']),
            'sqlite_snapshot_sha256':digest(args.db),'lineage_sha256':digest(args.lineage),
            'owner_scope_sha256':digest(args.owner_scope) if args.owner_scope else None,
            'network_calls':0,'holdout_gold_read':False,'models_concurrent':False,'production_auth_claim':False,
            'cloud_send_consent':False,'canonical_scope':'isolated_migration_copy'}
        args.output.write_text(json.dumps({'metadata':metadata,'cases':cases},ensure_ascii=False,indent=2),encoding='utf-8')
    info=psutil.Process().memory_info();observations=read(resource) if resource.exists() else {}
    if digest(args.db)!=ready['database_sha256']:raise ValueError('final DB changed during evaluation')
    observations[args.phase]={'load_seconds':load_seconds,'query_latency_ms':latencies,
        'process_peak_working_set_bytes':getattr(info,'peak_wset',info.rss),'available_ram_after_bytes':psutil.virtual_memory().available,
        'torch_gpu_peak_allocated_bytes':torch.cuda.max_memory_allocated()}
    resource.write_text(json.dumps(observations,indent=2),encoding='utf-8')
    print('Actual model+canonical phase:',args.phase,'cases',len(cases),flush=True)

if __name__=='__main__':main()
