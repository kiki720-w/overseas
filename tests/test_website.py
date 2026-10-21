import io
import json
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import Mock,patch
from backend.common import AppError,STAGES,TYPES
from backend.store import Store
from backend.website import WebsiteService
from backend.website_fetch import Collector,normalize_url,public_ips,fetch_public,MAX_BYTES
from backend.model_analysis import build_context,validate_analysis
from server import make_server

BASE='https://fixture.example/'
def response(html,status=200,**headers):return status,dict({'content-type':'text/html; charset=utf-8'},**headers),html.encode()
def fixture(url,deadline):
    if url.endswith('/robots.txt'):return 200,{'content-type':'text/plain'},b'User-agent: *\nDisallow: /private'
    if url.endswith('/'):return response('<title>DEMO Synthetic Company</title><p>Company offers CNC machining products.</p><a href="/about">About</a><a href="/products">Products</a><a href="/contact">Contact</a><script>SECRET SCRIPT</script>')
    return response('<p>Company makes CNC fixtures.</p><p>Public email: demo@fixture.example</p>')

class WebsiteNetworkTests(unittest.TestCase):
    def test_urls_reject_credentials_private_metadata_queries_ports_and_obfuscation(self):
        for url in ['','x'*501,'file:///etc/passwd','ftp://fixture.example','http://127.0.0.1/','http://169.254.169.254/','http://10.0.0.1/','http://[::1]/','http://[::ffff:127.0.0.1]/','http://metadata.google.internal/','http://a.local/','http://a.internal/','http://2130706433/','http://a:b@fixture.example/','https://fixture.example:8443/','https://fixture.example/?x=1','https://fixture.example/#x','https://fixture.example/../a','https://fixture.example\\@127.0.0.1/','https://fixture.example/\n']:
            with self.subTest(url=url),self.assertRaises(AppError):normalize_url(url)
        self.assertEqual(normalize_url('https://Fixture.example'),BASE)
    def test_dns_all_answers_must_be_public(self):
        rows=lambda ips:[(socket.AF_INET,socket.SOCK_STREAM,6,'',(i,443)) for i in ips]
        with self.assertRaises(AppError):public_ips(BASE,lambda *a,**k:rows(['8.8.8.8','127.0.0.1']))
        with self.assertRaises(AppError):public_ips(BASE,lambda *a,**k:rows(['169.254.169.254']))
        self.assertEqual(public_ips(BASE,lambda *a,**k:rows(['8.8.8.8'])),['8.8.8.8'])
    def test_pinned_transport_does_not_resolve_again_or_send_credentials(self):
        conn=Mock();r=Mock();r.status=200;r.getheaders.return_value=[('Content-Type','text/html')];r.read1.side_effect=[b'hello',b''];conn.getresponse.return_value=r;factory=Mock(return_value=conn);resolver=Mock(return_value=[(2,1,6,'',('8.8.8.8',443))])
        self.assertEqual(fetch_public(BASE,time.monotonic()+5,resolver,factory)[2],b'hello');factory.assert_called_once_with(BASE,'8.8.8.8');resolver.assert_called_once();headers=conn.request.call_args.kwargs['headers'];self.assertNotIn('Authorization',headers);self.assertNotIn('Cookie',headers);self.assertEqual(headers['Accept-Encoding'],'identity');conn.close.assert_called_once()
    def test_transport_size_encoding_timeout_and_close(self):
        for headers,side_effect in [([('Content-Length',str(MAX_BYTES+1))],None),([('Content-Encoding','gzip')],None),([], [b'x'*(MAX_BYTES+1)]),([],socket.timeout())]:
            conn=Mock();r=Mock();r.status=200;r.getheaders.return_value=headers;r.read1.side_effect=side_effect;conn.getresponse.return_value=r
            with self.assertRaises(AppError):fetch_public(BASE,time.monotonic()+5,lambda *a,**k:[(2,1,6,'',('8.8.8.8',443))],lambda *a:conn)
            conn.close.assert_called_once()
    def test_page_limit_extraction_exact_sources_no_scripts(self):
        calls=[]
        def f(url,deadline):calls.append(url);return fixture(url,deadline)
        v=Collector(f).collect(BASE);self.assertEqual(len(v['pages']),3);self.assertEqual(len(calls),4);self.assertNotIn('SECRET SCRIPT',json.dumps(v));self.assertTrue(v['pages'][0]['company_information']);self.assertTrue(v['pages'][0]['product_information']);self.assertIn('demo@fixture.example',v['pages'][1]['public_emails'])
        for p in v['pages']:
            self.assertTrue(p['fetched_at']);self.assertFalse(p['independently_proven']);self.assertTrue(all(e['source_url']==p['url'] for e in p['evidence']))
    def test_robot_disallow_never_requests_page(self):
        calls=[]
        def f(u,d):calls.append(u);return 200,{'content-type':'text/plain'},b'User-agent: *\nDisallow: /'
        with self.assertRaises(AppError):Collector(f).collect(BASE)
        self.assertEqual(calls,[BASE+'robots.txt'])
    def test_robot_unreadable_throttling_and_meta_restrictions(self):
        for robots in [(403,{},b''),(200,{'content-type':'text/html'},b'login'),(200,{'content-type':'text/plain'},b'User-agent: *\nCrawl-delay: 2')]:
            with self.assertRaises(AppError):Collector(lambda u,d:robots).collect(BASE)
        for body,h in [('<meta name="robots" content="noindex"><p>CNC products</p>',{}),('<p>CNC products</p>',{'x-robots-tag':'noarchive'})]:
            with self.assertRaises(AppError):Collector(lambda u,d:fixture(u,d) if u.endswith('robots.txt') else response(body,**h)).collect(BASE)
    def test_redirect_private_cross_domain_downgrade_and_long_chain(self):
        for target in ['http://127.0.0.1/','http://169.254.169.254/','https://other.example/','http://fixture.example/','/loop']:
            calls=[]
            def f(u,d):calls.append(u);return fixture(u,d) if u.endswith('robots.txt') else (302,{'location':target},b'')
            with self.assertRaises(AppError):Collector(f).collect(BASE)
            self.assertFalse(any('127.0.0.1' in x or '169.254' in x or 'other.example' in x for x in calls));self.assertLessEqual(len(calls),4)
    def test_redirect_destination_robots_checked_before_fetch(self):
        calls=[]
        def f(u,d):calls.append(u);return fixture(u,d) if u.endswith('robots.txt') else (302,{'location':'/private'},b'')
        with self.assertRaises(AppError):Collector(f).collect(BASE)
        self.assertNotIn(BASE+'private',calls)
    def test_same_origin_redirect_preserves_exact_final_url(self):
        def f(u,d):
            if u==BASE:return 301,{'location':'/company'},b''
            return fixture(u,d)
        v=Collector(f).collect(BASE);self.assertEqual(v['pages'][0]['url'],BASE+'company');self.assertEqual(v['pages'][0]['requested_url'],BASE)
    def test_binary_login_and_empty_js_site_are_honest_failures(self):
        for page in [(200,{'content-type':'application/pdf'},b'pdf'),response('Forbidden',403),response('<script>render()</script>')]:
            with self.assertRaises(AppError):Collector(lambda u,d:fixture(u,d) if u.endswith('robots.txt') else page).collect(BASE)
    def test_partial_secondary_failure_does_not_fabricate_results(self):
        v=Collector(lambda u,d:fixture(u,d) if u.endswith('/') or u.endswith('robots.txt') else response('blocked',403)).collect(BASE)
        self.assertEqual(len(v['pages']),1);self.assertEqual(len(v['warnings']),2)

class WebsiteWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'synthetic.db';self.store=Store(self.path,False);self.c=self.store.save_customer({'name':'DEMO synthetic only','type':'终端加工厂','stage':STAGES[0],'website':BASE},False);self.other=self.store.save_customer({'name':'OTHER synthetic','type':'经销商','stage':STAGES[0]},False);self.s=WebsiteService(self.store,Collector(fixture))
    def tearDown(self):
        import gc;gc.collect();self.tmp.cleanup()
    def preview(self):return self.s.preview({'customer_id':self.c['id'],'url':BASE,'consent':True})
    def confirm(self,p,**extra):return self.s.confirm(dict(job_id=p['job_id'],customer_id=self.c['id'],consent=True,**extra))
    def test_explicit_consent_and_preview_does_not_save(self):
        with self.assertRaises(AppError):self.s.preview({'customer_id':self.c['id'],'url':BASE})
        p=self.preview();self.assertFalse(p['saved']);self.assertEqual(self.store.records(self.c['id'],{})['total'],0);self.assertEqual(self.store.website_captures(self.c['id'])['items'],[])
        with self.assertRaises(AppError):self.s.confirm({'job_id':p['job_id'],'customer_id':self.c['id'],'consent':False})
    def test_confirmation_persists_once_links_correct_customer_and_context(self):
        _,_,_,_,before=build_context(self.store,self.c['id']);p=self.preview();r=self.confirm(p);self.assertTrue(self.confirm(p)['replayed']);reopened=Store(self.path,False);self.assertEqual(len(reopened.website_captures(self.c['id'])['items']),1);self.assertEqual(reopened.records(self.c['id'],{})['total'],1);self.assertEqual(reopened.records(self.other['id'],{})['total'],0);self.assertEqual(reopened.customer(self.c['id']),self.c)
        _,context,_,_,after=build_context(reopened,self.c['id']);self.assertNotEqual(before,after);self.assertEqual(context['records'][0]['website_capture_id'],r['id']);self.assertIn(BASE,context['records'][0]['text']);self.assertIn('非采购意向',context['records'][0]['text']);self.assertNotIn('OTHER synthetic',json.dumps(context))
        out=dict.fromkeys(['match','angle','next_step','draft','unknowns','stage_blocker','notice'],'synthetic');out.update(facts=[{'statement':'Has purchase intent','evidence':[{'ref_id':next(x['ref_id'] for x in context['evidence_catalog'] if x['source']=='record')}]}],hypotheses=[],questions=[]);normalized=validate_analysis(out,context);self.assertTrue(normalized['facts'][0]['statement'].startswith('客户官网自述（非采购意向）'));self.assertNotIn('Has purchase intent',normalized['facts'][0]['statement'])
    def test_cross_customer_confirmation_and_replay_are_rejected(self):
        p=self.preview()
        for confirmed in [False,True]:
            if confirmed:self.confirm(p)
            with self.assertRaises(AppError):self.s.confirm({'job_id':p['job_id'],'customer_id':self.other['id'],'consent':True})
    def test_discard_expiry_and_customer_revision_guard(self):
        p=self.preview();self.s.discard({'job_id':p['job_id'],'customer_id':self.c['id']})
        with self.assertRaises(AppError):self.confirm(p)
        p=self.preview();self.s.jobs[p['job_id']]['expires']=0
        with self.assertRaises(AppError):self.confirm(p)
        p=self.preview();self.store.save_customer(dict(self.c,name='Changed synthetic'),False)
        with self.assertRaises(AppError):self.confirm(p)
        self.assertEqual(self.store.records(self.c['id'],{})['total'],0)
    def test_capacity_slots_and_worker_timeout(self):
        for _ in range(5):self.preview()
        with self.assertRaises(AppError):self.preview()
        self.s.jobs.clear();self.s.slots.acquire();self.s.slots.acquire()
        with self.assertRaises(AppError):self.preview()
        self.assertEqual(len(self.s.jobs),0);self.s.slots.release();self.s.slots.release()
        s=WebsiteService(self.store);proc=Mock();proc.communicate.side_effect=[subprocess.TimeoutExpired('worker',22),(b'',b'')]
        with patch('backend.website.subprocess.Popen',return_value=proc),self.assertRaises(AppError):s.collect(BASE)
        proc.kill.assert_called_once()
    def test_http_endpoints_csrf_confirmation_and_static_page(self):
        server=make_server(self.store,'local',port=0,website_service=self.s);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();origin='http://127.0.0.1:'+str(server.server_port)
        def get(path):
            with urllib.request.urlopen(origin+path) as r:return r.read()
        def post(path,data,csrf):
            with urllib.request.urlopen(urllib.request.Request(origin+path,json.dumps(data).encode(),{'Content-Type':'application/json','X-CSRF-Token':csrf})) as r:return json.load(r)
        try:
            token=json.loads(get('/api/meta'))['csrf'];self.assertIn('官网'.encode(),get('/research'))
            with self.assertRaises(urllib.error.HTTPError):post('/api/website/preview',{},'wrong')
            p=post('/api/website/preview',{'customer_id':self.c['id'],'url':BASE,'consent':True},token);post('/api/website/confirm',{'customer_id':self.c['id'],'job_id':p['job_id'],'consent':True},token);self.assertEqual(len(json.loads(get('/api/website/captures?customer_id='+self.c['id']))['items']),1)
        finally:server.shutdown();server.server_close();thread.join()
