import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
import urllib.request
import urllib.error
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server

class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=server.Store(Path(self.tmp.name)/'demo.sqlite3',True)
        self.http=server.make_server(self.store,port=0)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True)
        self.thread.start()
        self.base='http://127.0.0.1:'+str(self.http.server_port)
        self.state=self.call('/api/state')
    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.thread.join();self.tmp.cleanup()
    def call(self,path,data=None,token=True):
        headers={'Content-Type':'application/json'} if data is not None else {}
        if data is not None and token:headers['X-CSRF-Token']=self.state['csrf']
        req=urllib.request.Request(self.base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
        with urllib.request.urlopen(req) as r:return json.load(r)
    def customer(self):
        return {'name':'测试客户','type':'终端加工厂','stage':'待开发','priority':True,'product':'CNC 夹具','country':''}
    def test_create_edit_and_reopen(self):
        c=self.call('/api/customer',self.customer())
        c.update(name='修改名称',stage='需求确认',facts='用户录入事实')
        self.call('/api/customer',c)
        reopened=server.Store(self.store.path,True).customer(c['id'])
        self.assertEqual(reopened['name'],'修改名称');self.assertTrue(reopened['demo'])
        self.assertEqual(self.call('/api/state')['customers'][0]['id'],c['id'])
    def test_shared_records_and_mock(self):
        c=self.call('/api/customer',self.customer())
        self.call('/api/record',{'customer_id':c['id'],'kind':'沟通','text':'请确认机床接口','next_date':'2026-10-10'})
        result=self.call('/api/analyze',{'customer_id':c['id'],'provider':'mock'})
        self.assertIn('1 条',result['next_step']);self.assertEqual(result['mode'],'mock')
        self.assertEqual(self.call('/api/state')['records'][0]['customer_id'],c['id'])
    def test_validation(self):
        c=self.customer()
        for change in [{'name':' '},{'name':'x'*201},{'type':'不存在'},{'source_date':'2026-99-99'},{'priority':'yes'}]:
            with self.assertRaises(urllib.error.HTTPError) as ctx:self.call('/api/customer',dict(c,**change))
            self.assertEqual(ctx.exception.code,400)
        customer=self.call('/api/customer',c)
        for text in ['', 'x'*12001]:
            with self.assertRaises(urllib.error.HTTPError):self.call('/api/record',{'customer_id':customer['id'],'kind':'跟进','text':text})
    def test_mode_isolation_and_no_network(self):
        local=server.Store(Path(self.tmp.name)/'local.sqlite3',False)
        self.assertEqual(local.state()['customers'],[])
        c=self.state['customers'][0]
        with patch('urllib.request.urlopen',side_effect=AssertionError('network forbidden')):
            result=server.mock_analysis(c,self.state['factory'],[])
            self.assertEqual(result['mode'],'mock')
            with self.assertRaises(ValueError):server.make_server(self.store,'demo',True,0)
            with self.assertRaises(ValueError):server.DeepSeekAdapter(False).analyze({})
        with self.assertRaises(urllib.error.HTTPError) as ctx:self.call('/api/analyze',{'customer_id':c['id'],'provider':'deepseek','consent':True})
        self.assertEqual(ctx.exception.code,400)
    def test_csrf_and_unknown_customer(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:self.call('/api/customer',self.customer(),False)
        self.assertEqual(ctx.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError) as ctx:self.call('/api/record',{'customer_id':'missing','kind':'沟通','text':'hello'})
        self.assertEqual(ctx.exception.code,404)
    def test_factory_configurable(self):
        f=dict(self.state['factory'],focus='卡盘',countries='用户自行配置')
        self.call('/api/factory',f)
        self.assertEqual(self.call('/api/state')['factory']['countries'],'用户自行配置')
    def test_local_requires_explicit_model_consent(self):
        local=server.Store(Path(self.tmp.name)/'local.sqlite3',False)
        c=local.save_customer(self.customer(),False)
        local_http=server.make_server(local,'local',False,0)
        worker=threading.Thread(target=local_http.serve_forever,daemon=True);worker.start()
        url='http://127.0.0.1:'+str(local_http.server_port)
        try:
            with urllib.request.urlopen(url+'/api/state') as response:state=json.load(response)
            for consent in [False,True]:
                req=urllib.request.Request(url+'/api/analyze',data=json.dumps({'customer_id':c['id'],'provider':'deepseek','consent':consent}).encode(),headers={'X-CSRF-Token':state['csrf']})
                with self.assertRaises(urllib.error.HTTPError) as ctx:urllib.request.urlopen(req)
                self.assertEqual(ctx.exception.code,400)
        finally:local_http.shutdown();local_http.server_close();worker.join()
    def test_security_guards(self):
        payload=json.dumps(self.customer()).encode()
        for extra in [{'Origin':'https://foreign.invalid'},{'Host':'foreign.invalid'}]:
            req=urllib.request.Request(self.base+'/api/customer',data=payload,headers={'X-CSRF-Token':self.state['csrf'],**extra})
            with self.assertRaises(urllib.error.HTTPError) as ctx:urllib.request.urlopen(req)
            self.assertEqual(ctx.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError) as ctx:self.call('/../../server.py')
        self.assertEqual(ctx.exception.code,404)
        with self.assertRaises(urllib.error.HTTPError) as ctx:self.call('/api/customer',{'name':'x'*100001})
        self.assertEqual(ctx.exception.code,400)
    def test_deal_query_routing(self):
        # Static endpoint must ignore query while retaining customer id in the browser URL.
        for path in ['/deals?customer=test-id','/deals.html?customer_id=test-id','/app.js?v=1']:
            with urllib.request.urlopen(self.base+path) as response:
                self.assertEqual(response.status,200)
                self.assertGreater(len(response.read()),0)
    def test_atomic_customer_version(self):
        original=self.call('/api/customer',self.customer())
        first=self.call('/api/customer',dict(original,name='开发页新名称'))
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.call('/api/customer',dict(original,stage='报价沟通'))
        self.assertEqual(ctx.exception.code,409)
        self.assertEqual(self.store.customer(original['id'])['name'],'开发页新名称')
        merged=self.call('/api/customer',dict(first,stage='报价沟通'))
        self.assertEqual(merged['name'],'开发页新名称')
        self.assertEqual(merged['version'],3)
        without_version=dict(merged);without_version.pop('version')
        with self.assertRaises(urllib.error.HTTPError) as ctx:self.call('/api/customer',without_version)
        self.assertEqual(ctx.exception.code,409)
    def test_simultaneous_customer_updates(self):
        original=self.call('/api/customer',self.customer())
        barrier=threading.Barrier(2)
        def update(name):
            barrier.wait()
            try:
                saved=self.call('/api/customer',dict(original,name=name))
                return (200,saved['name'])
            except urllib.error.HTTPError as e:return (e.code,name)
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes=list(pool.map(update,['页面A','页面B']))
        self.assertEqual(sorted(code for code,_ in outcomes),[200,409])
        winner=next(name for code,name in outcomes if code==200)
        self.assertEqual(self.store.customer(original['id'])['name'],winner)


if __name__=='__main__':unittest.main()
