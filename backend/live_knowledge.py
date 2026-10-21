"""Current relational KB -> fresh retrieval snapshot. No production DB mutations."""
import copy
from dataclasses import dataclass,field
import contextlib
import datetime as dt
import hashlib
import json
import re
import sqlite3
import threading

@dataclass(frozen=True)
class LiveRecord:
    candidate_id:str;document_id:str;version:int;chunk_id:str;current_version:int;revision:int
    subject:str;source_company:str;text:str;location:dict=field(default_factory=dict)
    fields:tuple=();canonical:bool=True;enabled:bool=True;review_status:str='unverified'
    expires_on:str='';conflict:bool=False;read_authorized:bool=False;send_authorized:bool=False

class LiveKnowledgeSnapshot:
    def __init__(self,store,can_read):
        if not callable(can_read):raise ValueError('trusted server read policy required')
        self.store=store;self.can_read=can_read;self.details={};self.registry={};self.owner={}
        self.vector_marks={};self.vector_document_versions={};self.last_index_error='';self.lock=threading.RLock();self.document_states=[]
    def __call__(self):
        with self.store.db() as db:
            db.execute('BEGIN')
            documents=db.execute('SELECT id,data FROM kb_documents').fetchall();docs=[]
            for did,raw in documents:
                d=json.loads(raw)
                if d.get('id')!=did or any(type(d.get(k)) is not int or d[k]<1 for k in ('version','revision')):
                    raise ValueError('SQL/JSON document identity/version mismatch')
                docs.append(d)
            chunks=db.execute("SELECT c.id,c.document_id,c.version,c.data FROM kb_chunks c JOIN kb_documents d ON c.document_id=d.id WHERE c.version=json_extract(d.data,'$.version') LIMIT 10001").fetchall()
            if len(chunks)>10000:raise ValueError('knowledge index chunk budget exceeded')
            versions={(r[0],r[1]):json.loads(r[2]) for r in db.execute("SELECT v.document_id,v.version,v.data FROM kb_versions v JOIN kb_documents d ON v.document_id=d.id WHERE v.version=json_extract(d.data,'$.version')")}
        by_doc={d['id']:d for d in docs};records=[];details={};registry={};states=[];read_ids=[]
        for d in docs:
            allowed=self.can_read(copy.deepcopy(d)) is True
            if allowed:read_ids.append(d['id'])
            current=[row for row in chunks if row[1]==d['id'] and row[2]==d['version']]
            version=versions.get((d['id'],d['version']),{})
            chunk_states=[]
            for cid,did,ver,raw in current:
                chunk=json.loads(raw)
                if chunk.get('chunk_id')!=cid:raise ValueError('SQL/JSON chunk identity mismatch')
                candidate_id=chunk.get('metadata',{}).get('candidate_chunk_id',cid)
                model=chunk.get('metadata',{}).get('structured_parameters',{}).get('model_verbatim')
                models=[model] if model else re.findall(r'(?<![A-Za-z0-9])[A-Za-z]+\d+[A-Za-z]*(?![A-Za-z0-9])',chunk['text'])
                models=[m for m in models if m and not re.match(r'^(ISO|DIN|IEC|EN)\d',m,re.I)]
                if allowed and d['enabled'] and d['review_status'] in ('confirmed','source_checked') and not self.store.knowledge_warnings(d,docs)['expired']:
                    for m in models:registry[m]=d.get('source_company','')+':'+m
                text_hash=hashlib.sha256(chunk['text'].encode()).hexdigest()
                chunk_states.append((candidate_id,ver,text_hash))
                warning_doc=self.store.knowledge_warnings(d,docs)
                r=LiveRecord(candidate_id,did,ver,cid,d['version'],d['revision'],
                    d.get('source_company','')+':'+(model or did),d.get('source_company',''),chunk['text'],chunk.get('location',{}),
                    enabled=d['enabled'],review_status=d['review_status'],expires_on=d.get('expires_on',''),
                    conflict=warning_doc['conflict'],read_authorized=allowed)
                records.append(r);details[candidate_id]={'document':d,'chunk':chunk,'version':version,'model':model}
            marked=sum(self.vector_marks.get(key) for key in chunk_states if key in self.vector_marks)
            vector='indexed' if chunk_states and marked==len(chunk_states) else 'partial' if marked else 'stale' if d['id'] in self.vector_document_versions else 'not_indexed'
            if not allowed:continue
            states.append({'document_id':d['id'],'current_version':d['version'],'revision':d['revision'],
                'processing':{'state':'parsed' if current else 'no_text_chunks','parser':version.get('parser','unknown'),
                    'chunk_count':len(current),'ocr_performed':version.get('processing',{}).get('ocr_performed') is True},
                'keyword_index':{'state':'ready' if current else 'no_text'},
                'vector_index':{'state':vector,'indexed_chunk_count':marked,'total_chunks':len(current),
                    'last_error':self.last_index_error,'strategy':'lazy_current_snapshot_content_hash'},
                'published':{'eligible':bool(d['enabled'] and d['review_status'] in ('confirmed','source_checked') and not self.store.knowledge_warnings(d,docs)['expired']),
                    'enabled':d['enabled'],'review_status':d['review_status'],'expires_on':d.get('expires_on','')}})
        if len({r.candidate_id for r in records})!=len(records):raise ValueError('duplicate current candidate identity')
        with self.lock:
            self.details=details;self.registry=registry;self.document_states=states
            self.owner={'read_document_ids':read_ids,'send_document_ids':[]}
        return tuple(records)
    def corpus(self):
        records=self();out=[]
        for r in records:
            d=self.details[r.candidate_id];meta=d['chunk'].get('metadata',{});parameters=meta.get('structured_parameters',{})
            out.append({'candidate_chunk_id':r.candidate_id,'origin_candidate_id':r.document_id,'record_type':'current_material',
                'text':r.text,'source_refs':[],'source_refs_role':'legacy_lineage','source_occurrences':[],
                'review':{'enabled':r.enabled,'review_status':r.review_status,'independently_proven':False},
                'target_document_id':None,'target_version':None,
                'eligible_for_product_retrieval':r.read_authorized,'body_mode':meta.get('body_mode','current_uploaded_text'),
                '_index_version':r.version})
        return out
    def mark_vectors(self,ids,snapshot):
        by_id={r.candidate_id:r for r in snapshot}
        with self.lock:
            for cid in ids:
                r=by_id.get(cid)
                if r:
                    self.vector_marks[(cid,r.version,hashlib.sha256(r.text.encode()).hexdigest())]=True
                    self.vector_document_versions[r.document_id]=r.version
            self.last_index_error=''
    def status(self):
        try:self()
        except ValueError:
            return {'documents':[],'snapshot_current':False,'index_error':'current_snapshot_invalid_or_capacity',
                'keyword_refresh':'failed','vector_rebuild':'not_started','automatic_OCR':False}
        return {'documents':copy.deepcopy(self.document_states),'snapshot_current':True,
            'keyword_refresh':'on_read_current_SQL','vector_rebuild':'lazy_per_query','automatic_OCR':False}

def write_current_job(store,job,can_read):
    """Only current authorized KB metadata/text; never copy original BLOB or config."""
    path=job/'canonical.sqlite3'
    with store.db() as source,sqlite3.connect(path) as target:
        source.execute('BEGIN')
        for table in ('kb_documents','kb_versions','kb_chunks'):
            target.execute(source.execute('SELECT sql FROM sqlite_master WHERE type=? AND name=?',('table',table)).fetchone()[0])
        for did,raw in source.execute('SELECT id,data FROM kb_documents'):
            doc=json.loads(raw)
            if can_read(copy.deepcopy(doc)) is not True:continue
            target.execute('INSERT INTO kb_documents VALUES(?,?)',(did,raw))
            ver=doc['version'];v=source.execute('SELECT data FROM kb_versions WHERE document_id=? AND version=?',(did,ver)).fetchone()
            if v:target.execute('INSERT INTO kb_versions VALUES(?,?,?,?)',(did,ver,v[0],b''))
            for cid,body in source.execute('SELECT id,data FROM kb_chunks WHERE document_id=? AND version=?',(did,ver)):
                chunk=json.loads(body);chunk.setdefault('metadata',{}).setdefault('candidate_chunk_id',cid)
                target.execute('INSERT INTO kb_chunks VALUES(?,?,?,?)',(cid,did,ver,json.dumps(chunk,ensure_ascii=False)))
        target.commit()
    class JobStore:
        @contextlib.contextmanager
        def db(self):
            connection=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
            try:yield connection
            finally:connection.close()
        def knowledge_warnings(self,d,docs):return store.knowledge_warnings(d,docs)
    snapshot=LiveKnowledgeSnapshot(JobStore(),can_read);rows=snapshot();corpus=job/'current-corpus';corpus.mkdir()
    (corpus/'schema.json').write_text(json.dumps({'version':'cleaned-v2-candidate'}),encoding='utf-8')
    (corpus/'semantic-chunks.json').write_text(json.dumps(snapshot.corpus(),ensure_ascii=False),encoding='utf-8')
    (corpus/'model-specifications.json').write_text('[]',encoding='utf-8')
    mapping=[{'candidate_chunk_id':r.candidate_id,'document_id':r.document_id,'version':r.version,
        'chunk_id':r.chunk_id,'text_sha256':hashlib.sha256(r.text.encode()).hexdigest()} for r in rows]
    (job/'current-map.json').write_text(json.dumps(mapping),encoding='utf-8')
    return path,corpus,job/'current-map.json',snapshot,rows
