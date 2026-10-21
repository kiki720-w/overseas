import sys
"""Read-only isolation bindings; existing selection/project/catalog protocols."""
import contextlib
import hashlib
import importlib
import json
import pathlib
import sqlite3
import subprocess
import sys
import uuid
import psutil

HERE=pathlib.Path(__file__).resolve().parent;ROOT=HERE.parent
sys.path[:0]=[str(ROOT/'integration-next'),str(ROOT/'answerability-next'),str(ROOT)]
from canonical import CanonicalSnapshot,read
from assessment import make_plan,assess
from api import EnhancedPreviewAPI

def load_protocol(ready):
    runtime=pathlib.Path(ready['projection_runtime']).resolve(strict=True)
    sys.path.insert(0,str(runtime))
    knowledge=importlib.import_module('backend.knowledge')
    projection=importlib.import_module('backend.context_knowledge')
    analysis=importlib.import_module('backend.model_analysis')
    for module in (knowledge,projection,analysis):
        if runtime not in pathlib.Path(module.__file__).resolve().parents:raise ValueError('unexpected existing protocol module')
    class ReadOnlyStore(knowledge.KnowledgeStore):
        def __init__(self,path):self.path=pathlib.Path(path).resolve(strict=True)
        @contextlib.contextmanager
        def db(self):
            connection=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)
            try:yield connection
            finally:connection.close()
    return ReadOnlyStore(ready['database']),projection.project_knowledge,analysis.evidence_catalog

class RecordedDevRetriever:
    """Labeled replay of frozen ACTUAL local model retrieval; not fresh inference."""
    def __init__(self,predictions):
        self.predictions=pathlib.Path(predictions).resolve(strict=True)
        self.data=read(self.predictions)
        self.candidates=read(self.predictions.with_suffix('.e5-candidates.json'))
    def __call__(self,query,limit):
        c=next((c for c in self.candidates['cases'] if c['query']==query),None)
        if c is None:raise ValueError('recorded mode only supports exact frozen dev queries')
        prediction=next(p for p in self.data['cases'] if p['case_id']==c['case_id'])
        by_id={i['candidate_chunk_id']:i for i in c['candidates']}
        return {'items':[{'candidate_chunk_id':cid,'score':by_id[cid]['score']} for cid in prediction['ranked_candidate_ids'][:limit]],
            'method':'local_bm25_dense_rrf_rerank','provenance':'recorded actual local model dev run; no fresh model inference'}

class IsolatedLocalRetriever:
    """Actual sequential CPU models through frozen runner; semantics ignored here."""
    def __init__(self,final_ready,owner_scope,corpus,workdir):
        self.final_ready=pathlib.Path(final_ready).resolve(strict=True);self.ready=read(self.final_ready)
        self.owner_scope=pathlib.Path(owner_scope).resolve(strict=True);self.corpus=pathlib.Path(corpus).resolve(strict=True)
        self.workdir=pathlib.Path(workdir).resolve();self.workdir.mkdir(parents=True,exist_ok=True)
        if HERE not in self.workdir.parents:raise ValueError('isolated preview work directory required')
    def __call__(self,query,limit):
        job=self.workdir/uuid.uuid4().hex;job.mkdir()
        queries=job/'queries.json';output=job/'predictions.json'
        queries.write_text(json.dumps([{'case_id':'LOCAL-PREVIEW','query':query,'state_fixture':None}],ensure_ascii=False),encoding='utf-8')
        command=[sys.executable,
            str(ROOT/'integration-next/run_qa_pipeline_next.py'),'--queries',str(queries),'--output',str(output),
            '--db',self.ready['database'],'--final-ready',str(self.final_ready),'--lineage',self.ready['canonical_chunk_map'],
            '--owner-scope',str(self.owner_scope),'--corpus',str(self.corpus),'--config',str(ROOT/'integration-next/config.json'),
            '--phase','e5']
        config=read(ROOT/'integration-next/config.json')
        if psutil.virtual_memory().available<config['minimum_start_ram_mib']*1024**2:raise RuntimeError('insufficient RAM before local retrieval')
        subprocess.run(command,check=True,cwd=str(ROOT))
        if psutil.virtual_memory().available<config['minimum_start_ram_mib']*1024**2:raise RuntimeError('insufficient RAM before local rerank')
        # Dedicated ranking-only second phase: semantic parser never runs on this path.
        subprocess.run([command[0],str(HERE/'rerank_local.py'),'--input',str(output.with_suffix('.e5-candidates.json')),
            '--output',str(output),'--config',str(ROOT/'integration-next/config.json'),'--limit',str(limit)],check=True,cwd=str(HERE))
        return read(output)

def build_api(final_ready,owner_scope,retrieve,enabled=False):
    ready=read(final_ready)
    if not ready.get('final') or ready.get('production_import') or ready.get('receipt_rolled_back'):raise ValueError('final isolation package required')
    if hashlib.sha256(pathlib.Path(ready['database']).read_bytes()).hexdigest()!=ready['database_sha256']:raise ValueError('final database hash mismatch')
    snapshot=CanonicalSnapshot(ready['database'],ready['canonical_chunk_map'],owner_scope)
    store,project,catalog=load_protocol(ready)
    def advisory(query,rows,selections):
        plan,unknown=make_plan(query,snapshot.registry);result=assess(plan,rows,selections,unknown)
        return {'experimental_coverage':result['evidence_answerability'],'unresolved_scope':result['unresolved_scope'],
            'field_states':[{'field':o['field'],'state':o['state']} for o in result['outcomes']],
            'not_an_answer_validity_verdict':True}
    return EnhancedPreviewAPI(retrieve,snapshot,store.knowledge_selection,project,catalog,advisory,enabled=enabled)
