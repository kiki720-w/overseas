"""Ranking-only actual CPU rerank phase; no semantic answer gate or generation."""
import argparse
import json
import os
import pathlib
import sys
import time
import psutil
HERE=pathlib.Path(__file__).resolve().parent;ROOT=HERE.parent
sys.path.insert(0,str(ROOT))
from rag.adapters import LocalMiniLMReranker
from rag.cleaned_v2 import cover_requested_models

def main():
    p=argparse.ArgumentParser();p.add_argument('--input',required=True,type=pathlib.Path)
    p.add_argument('--output',required=True,type=pathlib.Path);p.add_argument('--config',required=True,type=pathlib.Path)
    p.add_argument('--limit',type=int,default=6);args=p.parse_args()
    config=json.loads(args.config.read_text(encoding='utf-8'))
    for key in ('HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE','HF_HUB_DISABLE_TELEMETRY','HF_HUB_DISABLE_IMPLICIT_TOKEN'):os.environ[key]='1'
    os.environ['HF_HOME']=os.environ.get('RAG_LOCAL_CACHE',str(args.output.parent/'runtime-cache'));os.environ['TOKENIZERS_PARALLELISM']='false'
    if psutil.virtual_memory().available<config['minimum_start_ram_mib']*1024**2:raise RuntimeError('RAM insufficient before local CPU model load')
    import torch
    torch.set_num_threads(config['cpu_threads']);torch.set_num_interop_threads(1)
    started=time.perf_counter();provider=LocalMiniLMReranker(config['rerank_dir']);load=time.perf_counter()-started
    if psutil.virtual_memory().available<config['minimum_running_ram_mib']*1024**2:raise RuntimeError('RAM reserve insufficient')
    case=json.loads(args.input.read_text(encoding='utf-8'))['cases'][0]
    items=case['candidates'];started=time.perf_counter()
    scores=provider.score(case['query'],[i['text'] for i in items]) if items else []
    order=sorted(range(len(items)),key=lambda i:(-scores[i],-items[i]['score'],items[i]['candidate_chunk_id']))
    ranked=cover_requested_models([dict(items[i],raw_rerank_logit=scores[i]) for i in order],case['query'],args.limit)
    # Raw body is reread and validated by API authority; do not expose indexed text here.
    result={'items':[{'candidate_chunk_id':i['candidate_chunk_id'],'score':i['score'],
        'raw_rerank_logit':i['raw_rerank_logit']} for i in ranked],
        'method':'local_bm25_dense_rrf_rerank','provenance':'actual sequential local CPU models for this query',
        'semantic_gate_applied':False,'generation_allowed':False,'network_calls':0,
        'resources':{'rerank_model_load_seconds':load,'rerank_ms':(time.perf_counter()-started)*1000,
            'peak_working_set_bytes':getattr(psutil.Process().memory_info(),'peak_wset',psutil.Process().memory_info().rss)}}
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print('Actual ranking-only local rerank completed',len(ranked),flush=True)

if __name__=='__main__':main()
