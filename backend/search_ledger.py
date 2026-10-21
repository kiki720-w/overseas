"""SQLite query reservations and local history. Never reads credential tables."""
import copy
import ctypes
import hashlib
import json
import os
import re
import time
import weakref
from .common import AppError,now,norm
from .webpage_url import inspect_page_url

OWNERS=weakref.WeakValueDictionary()

def fingerprint(provider,query,count,request=None):
    value=[provider,norm(query),count]
    options={k:request[k] for k in ('ori_question','need_content') if request and request.get(k)}
    if options:value.append(options)
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def dimensions(text,conditions,intent):
    language='zh' if any('\u4e00'<=c<='\u9fff' for c in text) else 'de' if any(x in text.casefold() for x in ('zahn','wälz','deutsch','lohnverzahnung')) else 'en' if any(x in text.casefold() for x in ('company','services','manufactur','machining')) else 'und'
    if language=='zh' and re.search('[a-zA-Z]',text):language='mixed'
    return {'countries':conditions['countries'],'regions':[r for r in conditions['regions'] if r in text],'language':language,'language_basis':'query_script_and_terms_not_translation','industries':[v for v in conditions['industries'] if v in text],'processing_scenarios':[t for t in intent['process_terms'] if norm(t) in norm(text)]}

def owner_alive(pid,owner):
    if pid==os.getpid():return owner in OWNERS
    if os.name=='nt':
        from ctypes import wintypes as w
        k=ctypes.WinDLL('kernel32',use_last_error=True);k.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];k.OpenProcess.restype=w.HANDLE;k.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD];k.CloseHandle.argtypes=[w.HANDLE]
        handle=k.OpenProcess(0x100000,False,pid)
        if not handle:return ctypes.get_last_error()!=87
        try:return k.WaitForSingleObject(handle,0)==0x102
        finally:k.CloseHandle(handle)
    try:os.kill(pid,0)
    except ProcessLookupError:return False
    except PermissionError:return True
    return True

def encode(plan):
    value=copy.deepcopy({k:v for k,v in plan.items() if k not in ('query_builder','expires','retry_at','ledger_rows') and not k.startswith('_')})
    value['candidate_keys']=list(value['candidate_keys']);value['ledger_version']=1
    if plan.get('retry_at'):value['retry_until']=time.time()+max(0,plan['retry_at']-time.monotonic())
    return json.dumps(value,ensure_ascii=False,separators=(',',':'))

def decode(data):
    plan=json.loads(data)
    if plan.get('ledger_version')!=1:raise AppError('查询账本版本不支持，未执行请求',409,'search_ledger_version')
    plan['candidate_keys']=set(plan['candidate_keys']);plan['expires']=time.monotonic()+1800
    if plan.get('retry_until'):plan['retry_at']=time.monotonic()+max(0,plan['retry_until']-time.time())
    return plan

class SearchLedger:
    def __init__(self,store,owner):
        self.store=store;self.owner=owner;OWNERS[owner]=self
        with store.db() as db:
            db.executescript('CREATE TABLE IF NOT EXISTS search_plan_ledger(id TEXT PRIMARY KEY,scope TEXT UNIQUE NOT NULL,data TEXT NOT NULL,owner TEXT,pid INTEGER,rid TEXT,updated TEXT NOT NULL);CREATE TABLE IF NOT EXISTS search_query_ledger(fingerprint TEXT PRIMARY KEY,plan_id TEXT NOT NULL,query_index INTEGER NOT NULL,request_id TEXT NOT NULL,state TEXT NOT NULL,metadata TEXT NOT NULL,new_entities INTEGER NOT NULL DEFAULT 0);CREATE TABLE IF NOT EXISTS search_source_ledger(url TEXT NOT NULL,evidence TEXT NOT NULL,entity TEXT,data TEXT NOT NULL,plan_id TEXT NOT NULL,request_id TEXT NOT NULL,PRIMARY KEY(url,evidence));')
    def register(self,plan,scope):
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT id FROM search_plan_ledger WHERE scope=?',(scope,)).fetchone()
            if row:return row[0]
            db.execute('INSERT INTO search_plan_ledger(id,scope,data,updated) VALUES(?,?,?,?)',(plan['plan_id'],scope,encode(plan),now()))
        return plan['plan_id']
    def scope(self,key):
        with self.store.db() as db:row=db.execute('SELECT id FROM search_plan_ledger WHERE scope=?',(key,)).fetchone()
        return row[0] if row else None
    def rows(self,pid):
        with self.store.db() as db:
            return [dict(json.loads(m),query_index=i,state=s,new_entities=n) for i,s,m,n in db.execute('SELECT query_index,state,metadata,new_entities FROM search_query_ledger WHERE plan_id=? ORDER BY query_index',(pid,))]
    def latest(self):
        with self.store.db() as db:row=db.execute('SELECT id FROM search_plan_ledger ORDER BY updated DESC,rowid DESC LIMIT 1').fetchone()
        if not row:raise AppError('暂无已保存查询计划',404,'search_plan_not_found')
        return row[0]
    def fetch(self,pid):
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT data,owner,pid,rid FROM search_plan_ledger WHERE id=?',(pid,)).fetchone()
            if not row:raise AppError('查询计划不存在',404,'search_plan_not_found')
            plan=decode(row[0]);owner,process,rid=row[1:]
            if owner and not owner_alive(process,owner):
                sent=db.execute('SELECT query_index,state FROM search_query_ledger WHERE plan_id=? AND request_id=?',(pid,rid)).fetchone()
                if sent:
                    plan['next']=max(plan['next'],sent[0]+1)
                    if sent[1]!='failed':db.execute("UPDATE search_query_ledger SET state='outcome_unknown' WHERE plan_id=? AND request_id=?",(pid,rid))
                plan.update(pending=None,state='stopped',stop_reason='provider_failure',last_outcome=('request_failed' if sent[1]=='failed' else 'outcome_unknown') if sent else 'interrupted_before_send')
                plan['outcomes'][rid]={'ok':False,'message':'上次请求被中断，结果未确认；继续会选择未执行查询，不自动重发','status':409,'code':'search_interrupted','details':{'plan_id':pid,'plan_state':'stopped'}}
                db.execute('UPDATE search_plan_ledger SET data=?,owner=NULL,pid=NULL,rid=NULL,updated=? WHERE id=?',(encode(plan),now(),pid))
            elif owner and owner!=self.owner:plan['_busy']=True
        return plan
    def save(self,plan,release=False):
        with self.store.db() as db:
            if release:db.execute('UPDATE search_plan_ledger SET data=?,owner=NULL,pid=NULL,rid=NULL,updated=? WHERE id=? AND owner=?',(encode(plan),now(),plan['plan_id'],self.owner))
            else:db.execute('UPDATE search_plan_ledger SET data=?,updated=? WHERE id=? AND (owner IS NULL OR owner=?)',(encode(plan),now(),plan['plan_id'],self.owner))
    def acquire(self,pid,rid):
        self.fetch(pid)
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT owner FROM search_plan_ledger WHERE id=?',(pid,)).fetchone()
            if row[0]:raise AppError('此计划已有请求正在执行，未重复发送',409,'search_plan_busy')
            db.execute('UPDATE search_plan_ledger SET owner=?,pid=?,rid=? WHERE id=?',(self.owner,os.getpid(),rid,pid))
    def used(self):
        with self.store.db() as db:return {r[0] for r in db.execute('SELECT fingerprint FROM search_query_ledger')}
    def attempt(self,plan,index,rid):
        request=plan['requests'][index];query=plan['queries'][index];provider=plan['search_connection']['provider'];key=fingerprint(provider+':'+plan['search_connection'].get('engine_type',''),query['executed_query'],request.get('count',8),request)
        text=query['executed_query']
        metadata=dict(dimensions(text,plan['conditions'],plan['buyer_intent']),provider=provider,query=text,count=request.get('count',8),need_content=request.get('need_content',False),ori_question=request.get('ori_question'),buyer_intent=plan['buyer_intent'],angle=query['angle'],sent_at=now())
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM search_query_ledger WHERE fingerprint=?',(key,)).fetchone():raise AppError('此查询已有请求记录，未重复发送；请继续未执行组合',409,'search_query_already_attempted')
            if plan['attempts']>=plan['maximum']:raise AppError('本次已批准请求预算已达到',409,'search_budget_reached')
            db.execute('INSERT INTO search_query_ledger VALUES(?,?,?,?,?,?,0)',(key,plan['plan_id'],index,rid,'sent',json.dumps(metadata,ensure_ascii=False)))
            reserved=dict(plan,attempts=plan['attempts']+1)
            db.execute('UPDATE search_plan_ledger SET data=?,updated=? WHERE id=? AND owner=?',(encode(reserved),now(),plan['plan_id'],self.owner))
        plan['attempts']+=1
    def outcome(self,plan,rid,state,count=0):
        with self.store.db() as db:db.execute('UPDATE search_query_ledger SET state=?,new_entities=? WHERE plan_id=? AND request_id=?',(state,count,plan['plan_id'],rid))
    def evidence(self,plan,rid,items):
        retained=[];duplicates=0;new_urls=0;new_entities=set()
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            known_entities={r[0] for r in db.execute('SELECT DISTINCT entity FROM search_source_ledger WHERE entity IS NOT NULL')}
            for c in items:
                url=inspect_page_url(c['url'])['document_url'];p=c.get('search_provenance') or {}
                evidence=[p.get('title',''),p.get('summary',''),p.get('snippet','')]
                if p.get('page_content'):evidence.append(p['page_content'])
                digest=hashlib.sha256(json.dumps(evidence,ensure_ascii=False).encode()).hexdigest()
                entity=(c.get('entity_group') or {}).get('group_id')
                if db.execute('SELECT 1 FROM search_source_ledger WHERE url=? AND evidence=?',(url,digest)).fetchone():duplicates+=1;continue
                if not db.execute('SELECT 1 FROM search_source_ledger WHERE url=?',(url,)).fetchone():new_urls+=1
                stored={k:v for k,v in c.items() if k not in ('expires','pending')}
                db.execute('INSERT INTO search_source_ledger VALUES(?,?,?,?,?,?)',(url,digest,entity,json.dumps(stored,ensure_ascii=False),plan['plan_id'],rid));retained.append(c)
                if entity and entity not in known_entities:new_entities.add(entity)
        return retained,duplicates,new_urls,len(new_entities)

    def sources(self):
        with self.store.db() as db:
            rows=db.execute('SELECT s.data,q.state FROM search_source_ledger s LEFT JOIN search_query_ledger q ON s.plan_id=q.plan_id AND s.request_id=q.request_id').fetchall()
        result=[]
        for data,state in rows:
            c=json.loads(data);c['query_ledger_state']=state;result.append(c)
        return result
