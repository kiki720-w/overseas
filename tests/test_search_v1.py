import gc
import json
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch,Mock
from backend.common import AppError,TYPES
from backend.store import Store
from backend.discovery import DiscoveryService
from backend.search import TavilyKeylessSearch,request_once,ENDPOINT,MAX_BYTES
from backend.website import WebsiteService
from backend.website_fetch import Collector
from test_website import fixture
from server import make_server

def result(url='https://fixture.example/',title='Synthetic CNC company'):
    return {'url':url,'title':title,'content':'Public CNC fixture products; no purchase claim.'}

class SearchV1Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'test.db',False)
        self.transport=Mock(return_value=(200,{'results':[result()]}))
        self.provider=TavilyKeylessSearch(self.transport,cooldown=0,key="synthetic-search-only")
        self.service=DiscoveryService(self.store,WebsiteService(self.store,Collector(fixture)),'local',self.provider)
    def tearDown(self):gc.collect();self.tmp.cleanup()
    def search(self,**fields):return self.service.search(dict({'query':'CNC fixtures','consent':True},**fields))
    def count(self,table):
        with self.store.db() as db:return db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
    def test_single_bounded_search_no_crm_or_model_write(self):
        r=self.search(products='CNC',countries='unconfirmed public market',type=TYPES[0]);self.assertEqual(len(r['items']),1)
        self.assertEqual(r['items'][0]['evidence_level'],'search_snippet');self.assertIsNone(r['items'][0]['capture']);self.assertTrue(r['items'][0]['research_required'])
        self.assertEqual(self.transport.call_count,1);p=self.transport.call_args.args[0];self.assertEqual(p['max_results'],8);self.assertFalse(p['auto_parameters'])
        for table in ['customers','records','calls','discovery_candidates']:self.assertEqual(self.count(table),0)
    def test_consent_empty_long_and_invalid_type_do_not_request(self):
        for data in [{'query':'x'},{'query':'','consent':True},{'query':'x'*501,'consent':True},{'query':'x','products':'x'*501,'consent':True},{'query':'x','type':'unknown','consent':True}]:
            with self.assertRaises(AppError):self.service.search(data)
        self.assertEqual(self.transport.call_count,0)
    def test_distinct_pages_directory_marker_and_unsafe_filter(self):
        self.transport.return_value=(200,{'results':[result(),result('https://www.fixture.example/about'),result('https://rocketreach.co/profile'),result('http://127.0.0.1/'),result('https://fixture.example/?tracking=x')]})
        r=self.search();self.assertEqual(len(r['items']),3);self.assertEqual(r['deduplicated_count'],0);self.assertEqual(r['filtered_count'],2);self.assertEqual(r['items'][2]['source_type'],'directory_or_platform')
    def test_empty_results_are_honest(self):
        self.transport.return_value=(200,{'results':[]});self.assertEqual(self.search()['items'],[]);self.assertEqual(self.count('customers'),0)
    def test_search_candidate_stash_research_confirm_and_source_retention(self):
        c=self.search()['items'][0];cid=c['candidate_id'];self.service.stash({'candidate_id':cid,'consent':True})
        with self.assertRaises(AppError) as e:self.service.confirm({'candidate_id':cid,'name':'Synthetic CNC','type':TYPES[0],'consent':True})
        self.assertEqual(e.exception.code,'candidate_research_required');self.assertEqual(self.count('customers'),0)
        enriched=self.service.preview({'candidate_id':cid,'url':c['url'],'consent':True});self.assertEqual(enriched['state'],'draft');self.assertFalse(enriched['research_required']);self.assertEqual(enriched['search_provenance'],c['search_provenance'])
        r=self.service.confirm({'candidate_id':cid,'name':'Synthetic CNC','type':TYPES[0],'consent':True});self.assertEqual(r['candidate_id'],cid);self.assertEqual(self.count('customers'),1)
        with self.store.db() as db:record=json.loads(db.execute('SELECT data FROM records').fetchone()[0])
        self.assertEqual(record['search_provenance'],c['search_provenance'])
    def test_url_mismatch_rejected_without_fetch(self):
        c=self.search()['items'][0]
        with self.assertRaises(AppError) as e:self.service.preview({'candidate_id':c['candidate_id'],'url':'https://different.example/','consent':True})
        self.assertEqual(e.exception.status,409);self.assertIsNone(self.service.candidate(c)['capture'])
    def test_failed_research_keeps_search_candidate(self):
        self.transport.return_value=(200,{'results':[result('https://fixture.example/private')]});c=self.search()['items'][0]
        with self.assertRaises(AppError):self.service.preview({'candidate_id':c['candidate_id'],'url':c['url'],'consent':True})
        self.assertEqual(self.service.candidate(c)['search_provenance'],c['search_provenance']);self.assertEqual(self.count('customers'),0)
    def test_search_ttl_and_batch_cap(self):
        c=self.search()['items'][0];self.service.jobs[c['candidate_id']]['expires']=0
        with self.assertRaises(AppError) as e:self.service.candidate(c)
        self.assertEqual(e.exception.status,410)
        for _ in range(4):self.search()
        with self.assertRaises(AppError) as e:self.search()
        self.assertEqual(e.exception.code,'search_busy');self.assertEqual(self.transport.call_count,5)
    def test_rate_limit_envelope_no_auth_or_retry(self):
        self.transport.return_value=(429,{'error':{'code':'cap_hit','message':'cap','window':'daily','retry_after_seconds':3600,'next_actions':[{'action':'login'}]}})
        with self.assertRaises(AppError) as e:self.search()
        self.assertEqual(e.exception.code,'search_rate_limited');self.assertEqual(e.exception.details['retry_after_seconds'],3600);self.assertNotIn('next_actions',e.exception.details);self.assertEqual(self.transport.call_count,1)
    def test_network_timeout_auth_invalid_and_provider_errors(self):
        for value,code in [(socket.timeout(),'search_timeout'),(urllib.error.URLError('network'),'search_network_error'),((401,{}),'search_auth_failed'),((500,{}),'search_provider_error'),((200,{}),'search_invalid_response'),((200,[]),'search_invalid_response')]:
            self.transport.side_effect=value if isinstance(value,Exception) else None
            if not isinstance(value,Exception):self.transport.return_value=value
            with self.assertRaises(AppError) as e:self.search()
            self.assertEqual(e.exception.code,code)
    def test_local_cooldown_no_second_request(self):
        self.provider.cooldown=5;self.search()
        with self.assertRaises(AppError) as e:self.search()
        self.assertEqual(e.exception.code,'search_busy');self.assertEqual(self.transport.call_count,1)
    def test_transport_fixed_bearer_endpoint_redirect_denied_and_body_cap(self):
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False);response.code=200;response.read.return_value=b'{"results":[]}'
        opener=Mock();opener.open.return_value=response
        with patch('backend.search.urllib.request.build_opener',return_value=opener) as builder:
            status,body=request_once({'query':'public'},'synthetic-bearer');req=opener.open.call_args.args[0]
            self.assertEqual(req.full_url,ENDPOINT);self.assertEqual(req.get_method(),'POST');self.assertIsNone(req.get_header('X-tavily-access-mode'));self.assertEqual(req.get_header('Authorization'),'Bearer synthetic-bearer');self.assertEqual(response.read.call_args.args[0],MAX_BYTES+1)
            self.assertEqual(builder.call_args.args[0].proxies,{})
    def test_demo_server_stays_offline(self):
        server=make_server(self.store,'demo',port=0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with urllib.request.urlopen('http://127.0.0.1:'+str(server.server_port)+'/api/discovery/meta') as r:meta=json.load(r)
            self.assertFalse(meta['search']['configured'])
        finally:server.shutdown();server.server_close();thread.join()
    def test_http_search_and_candidate_contract(self):
        server=make_server(self.store,'local',port=0,discovery_service=self.service);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();origin='http://127.0.0.1:'+str(server.server_port)
        try:
            with urllib.request.urlopen(origin+'/api/meta') as r:csrf=json.load(r)['csrf']
            req=urllib.request.Request(origin+'/api/discovery/search',data=json.dumps({'query':'CNC','consent':True}).encode(),headers={'Content-Type':'application/json','X-CSRF-Token':csrf,'Origin':origin})
            with urllib.request.urlopen(req) as r:c=json.load(r)['items'][0]
            with urllib.request.urlopen(origin+'/api/discovery/candidate?candidate_id='+c['candidate_id']) as r:self.assertEqual(json.load(r)['candidate_id'],c['candidate_id'])
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
