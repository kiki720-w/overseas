# -*- coding: utf-8 -*-
from pathlib import Path
import sqlite3
import json
import secrets
import datetime as dt
from .common import *
from .analysis_history import AnalysisHistory
from .knowledge import KnowledgeStore
from .website import WebsiteStore
from .discovery import DiscoveryStore

def read_customer(raw):
    c=json.loads(raw);c.setdefault('version',0)
    for k in ['website','email','contact_name']:c.setdefault(k,'')
    return c
def indexed(c):return (norm(c['name']),c['type'],c['stage'],norm(c.get('country','')),int(c['priority']),norm(c['name']+' '+c.get('country','')+' '+c.get('product','')+' '+c.get('website','')+' '+c.get('email','')),c['version'])
class ClosingConnection(sqlite3.Connection):
    """Preserve DB-API transactions and raw access; close after each with block."""
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:self.close()

class Store(AnalysisHistory,KnowledgeStore,WebsiteStore,DiscoveryStore):
    def __init__(self,path,demo,factory_defaults=None):
        self.path=Path(path);self.is_new_workspace=not self.path.exists();self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.execute('PRAGMA journal_mode=WAL')
            self.init_history(db)
            self.init_knowledge(db)
            self.init_website(db)
            self.init_discovery(db)
            db.executescript('CREATE TABLE IF NOT EXISTS config(id INTEGER PRIMARY KEY,data TEXT);CREATE TABLE IF NOT EXISTS customers(id TEXT PRIMARY KEY,data TEXT);CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,customer_id TEXT,data TEXT);CREATE INDEX IF NOT EXISTS records_customer ON records(customer_id);CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY,data TEXT);')
            db.execute('CREATE TABLE IF NOT EXISTS import_receipts(id TEXT PRIMARY KEY,data TEXT)')
            columns={r[1] for r in db.execute('PRAGMA table_info(customers)')}
            for col,typ in [('name_key','TEXT'),('type_key','TEXT'),('stage_key','TEXT'),('country_key','TEXT'),('priority_key','INTEGER'),('search_text','TEXT'),('version_key','INTEGER')]:
                if col not in columns:db.execute(f'ALTER TABLE customers ADD COLUMN {col} {typ}')
            for cid,raw in db.execute('SELECT id,data FROM customers WHERE name_key IS NULL').fetchall():
                c=read_customer(raw);db.execute('UPDATE customers SET name_key=?,type_key=?,stage_key=?,country_key=?,priority_key=?,search_text=?,version_key=? WHERE id=?',indexed(c)+(cid,))
            db.executescript('CREATE INDEX IF NOT EXISTS customers_filters ON customers(type_key,stage_key,country_key,priority_key);CREATE INDEX IF NOT EXISTS customers_name ON customers(name_key,country_key);')
            if not db.execute('SELECT 1 FROM config').fetchone():
                db.execute('INSERT INTO config VALUES(1,?)',(dump(factory_defaults or {'name':'虚构示例精密制造工厂','products':'卡盘、齿轮夹具、CNC 夹具','capabilities':'待填写可核实的精度、材料、设备及产能','countries':'','focus':''}),))
                if demo:
                    for i,(typ,product) in enumerate(zip(TYPES,['CNC 夹具','卡盘','齿轮夹具'])):
                        c=customer_data({'name':f'演示 · {chr(65+i)} 工业公司','type':typ,'stage':'待开发','product':product,'country':'未知','source':'虚构演示，未进行外部核验','source_date':dt.date.today().isoformat(),'facts':'无已核实公司及采购事实','hypotheses':'可能有工装应用场景，需核实','unknowns':'联系人、采购计划、规格及预算均未知','signals':'无已确认需求信号'})
                        c.update(id=secrets.token_hex(8),demo=True,version=1,updated_at=now());self.insert(db,c)
    def db(self):
        db=sqlite3.connect(self.path,timeout=15,factory=ClosingConnection);db.execute('PRAGMA busy_timeout=15000');return db
    def insert(self,db,c):db.execute('INSERT INTO customers(id,data,name_key,type_key,stage_key,country_key,priority_key,search_text,version_key) VALUES(?,?,?,?,?,?,?,?,?)',(c['id'],dump(c))+indexed(c))
    def factory(self):
        with self.db() as db:return json.loads(db.execute('SELECT data FROM config WHERE id=1').fetchone()[0])
    def state(self):
        # Legacy compatibility for tests; V2 UI uses paginated endpoints.
        with self.db() as db:return {'factory':self.factory(),'customers':[read_customer(r[0]) for r in db.execute('SELECT data FROM customers ORDER BY rowid DESC')],'records':[json.loads(r[0]) for r in db.execute('SELECT data FROM records ORDER BY rowid DESC')]}
    def customer(self,cid):
        with self.db() as db:row=db.execute('SELECT data FROM customers WHERE id=?',(cid,)).fetchone()
        if not row:raise AppError('客户不存在',404,'not_found')
        return read_customer(row[0])
    def save_customer(self,data,demo):
        c=customer_data(data);cid=field(data,'id',32) or secrets.token_hex(8)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE');old=None
            if data.get('id'):
                row=db.execute('SELECT data FROM customers WHERE id=?',(cid,)).fetchone()
                if not row:raise AppError('客户不存在',404,'not_found')
                old=read_customer(row[0]);expected=data.get('version')
                if type(expected) is not int or expected!=old['version']:raise ConflictError()
            c.update(id=cid,demo=old['demo'] if old else demo,version=old['version']+1 if old else 1,updated_at=now())
            if old:db.execute('UPDATE customers SET data=?,name_key=?,type_key=?,stage_key=?,country_key=?,priority_key=?,search_text=?,version_key=? WHERE id=?',(dump(c),)+indexed(c)+(cid,))
            else:self.insert(db,c)
        return c
    def save_factory(self,data):
        f={k:field(data,k,4000,k=='name') for k in ['name','products','capabilities','countries','focus']}
        with self.db() as db:db.execute('UPDATE config SET data=? WHERE id=1',(dump(f),))
        return f
    def customers(self,q):
        p,s=paging(q);clauses=[];args=[]
        term=field(q,'q',200)
        if term:clauses.append("search_text LIKE ? ESCAPE '\\'");args.append('%'+norm(term).replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%')
        for key,col in [('type','type_key'),('stage','stage_key'),('country','country_key')]:
            value=field(q,key,100)
            if value:clauses.append(col+'=?');args.append(norm(value) if key=='country' else value)
        if q.get('priority','')!='':
            if q['priority'] not in ['0','1']:raise AppError('重点筛选应为0或1')
            clauses.append('priority_key=?');args.append(int(q['priority']))
        where=' WHERE '+' AND '.join(clauses) if clauses else ''
        with self.db() as db:
            total=db.execute('SELECT COUNT(*) FROM customers'+where,args).fetchone()[0]
            items=[read_customer(r[0]) for r in db.execute('SELECT data FROM customers'+where+' ORDER BY rowid DESC LIMIT ? OFFSET ?',args+[s,(p-1)*s])]
        return page_result(items,total,p,s)
    def records(self,cid,q):
        self.customer(cid);p,s=paging(q)
        with self.db() as db:
            total=db.execute('SELECT COUNT(*) FROM records WHERE customer_id=?',(cid,)).fetchone()[0]
            items=[json.loads(r[0]) for r in db.execute('SELECT data FROM records WHERE customer_id=? ORDER BY rowid DESC LIMIT ? OFFSET ?',(cid,s,(p-1)*s))]
        return page_result(items,total,p,s)
    def record(self,data):
        cid=field(data,'customer_id',32,True);self.customer(cid);kind=field(data,'kind',20,True)
        if kind not in KINDS:raise AppError('记录类型无效')
        date=field(data,'next_date',10)
        if date:
            try:dt.date.fromisoformat(date)
            except ValueError:raise AppError('跟进日期无效')
        r={'id':secrets.token_hex(8),'customer_id':cid,'kind':kind,'text':field(data,'text',12000,True),'next_date':date,'created_at':now()}
        with self.db() as db:db.execute('INSERT INTO records VALUES(?,?,?)',(r['id'],cid,dump(r)))
        return r
    def bulk(self,data):
        items=data.get('items');action=data.get('action');value=data.get('value')
        if not isinstance(items,list) or not 1<=len(items)<=100:raise AppError('一次请选择1至100个客户')
        if action=='priority' and type(value) is not bool or action=='stage' and value not in STAGES or action not in ['priority','stage']:raise AppError('批量操作参数无效')
        result={'updated':0,'conflicts':[],'missing':[]};seen=set()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            for item in items:
                if not isinstance(item,dict):raise AppError('客户版本列表无效')
                cid=field(item,'id',32,True)
                if cid in seen:continue
                seen.add(cid);row=db.execute('SELECT data FROM customers WHERE id=?',(cid,)).fetchone()
                if not row:result['missing'].append(cid);continue
                c=read_customer(row[0])
                if type(item.get('version')) is not int or item['version']!=c['version']:result['conflicts'].append(cid);continue
                c[action]=value;c['version']+=1;c['updated_at']=now()
                db.execute('UPDATE customers SET data=?,name_key=?,type_key=?,stage_key=?,country_key=?,priority_key=?,search_text=?,version_key=? WHERE id=?',(dump(c),)+indexed(c)+(cid,));result['updated']+=1
        return result
    def metrics(self):
        with self.db() as db:
            total=db.execute('SELECT COUNT(*) FROM customers').fetchone()[0];priority=db.execute('SELECT COUNT(*) FROM customers WHERE priority_key=1').fetchone()[0]
            stages=dict(db.execute('SELECT stage_key,COUNT(*) FROM customers GROUP BY stage_key'))
        return {'total':total,'priority':priority,'stages':stages}
    def call_log(self,task,status,profile,code='',cid=''):
        r={'id':secrets.token_hex(8),'created_at':now(),'task':task,'status':status,'provider':profile['provider'],'model':profile['model'],'error_code':code,'customer_id':cid}
        with self.db() as db:db.execute('INSERT INTO calls VALUES(?,?)',(r['id'],dump(r)))
    def calls(self,q):
        p,s=paging(q)
        with self.db() as db:
            total=db.execute('SELECT COUNT(*) FROM calls').fetchone()[0];items=[json.loads(r[0]) for r in db.execute('SELECT data FROM calls ORDER BY rowid DESC LIMIT ? OFFSET ?',(s,(p-1)*s))]
        return page_result(items,total,p,s)
