"""Public search fixtures against the same ClosedStore binding as run_production."""
import contextlib,gc,io,json,tempfile,threading,unittest,urllib.request,urllib.error
from pathlib import Path
from unittest.mock import Mock,patch
from backend.production_runtime import ClosedStore,NoKeyStartupModels
from backend.discovery import DiscoveryService
from backend.search import TavilyKeylessSearch
from backend.website import WebsiteService
from backend.website_fetch import Collector
from backend.common import TYPES,STAGES
from test_website import fixture
from server import make_server

class ProductionSearchBinding(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=ClosedStore(Path(self.tmp.name)/'synthetic.db',False)
        self.store.save_customer({'name':'Synthetic Existing LLC','website':'https://existing.example/','type':TYPES[0],'stage':STAGES[0]},False)
        self.transport=Mock(return_value=(200,{'results':[{'url':'https://www.existing.example/','title':'Synthetic existing','content':'Public fixture'},{'url':'https://new.example/','title':'Synthetic New LLC','content':'Public fixtures'}]}))
        self.service=DiscoveryService(self.store,WebsiteService(self.store,Collector(fixture)),'local',TavilyKeylessSearch(self.transport,cooldown=0,key="synthetic-search-only"))
        self.http=None
    def tearDown(self):
        if self.http:self.http.shutdown();self.http.server_close();self.thread.join(2)
        gc.collect();self.tmp.cleanup()
    def request(self,path,data=None):
        req=urllib.request.Request(self.base+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json','X-CSRF-Token':self.token} if data is not None else {})
        try:
            with urllib.request.urlopen(req) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as r:return r.code,json.load(r)
    def launch(self):
        self.http=make_server(self.store,'local',False,0,model_service=NoKeyStartupModels(self.store,'local'),discovery_service=self.service)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start();self.base='http://127.0.0.1:'+str(self.http.server_port);self.token='';self.token=self.request('/api/meta')[1]['csrf']
    def data(self):return {'query':'PUBLIC gear fixtures','products':'gear fixtures','countries':'China','type':TYPES[0],'max_queries':1}
    def unchanged(self):
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM customers').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM discovery_candidates').fetchone()[0],0)
    def test_v2_context_manager_search_filters_existing_and_creates_only_preview(self):
        p=self.service.plans.create(self.data());self.transport.assert_not_called()
        r=self.service.plans.next({'plan_id':p['plan_id'],'request_id':'binding_click_1','consent':True})
        self.assertEqual(r['batch']['excluded_existing_count'],1);self.assertEqual(r['batch']['new_company_count'],0);self.assertEqual(r['batch']['unresolved_company_page_count'],1)
        self.assertEqual(r['batch']['items'][0]['state'],'preview');self.assertTrue(r['batch']['items'][0]['research_required']);self.unchanged()
        again=self.service.plans.next({'plan_id':p['plan_id'],'request_id':'binding_click_1','consent':True});self.assertTrue(again['replayed']);self.assertEqual(self.transport.call_count,1)
    def test_v1_context_manager_binding_and_connection_released(self):
        r=self.service.search(dict(self.data(),consent=True));self.assertEqual(r['excluded_existing_count'],1);self.assertEqual(len(r['items']),1);self.unchanged()
        with self.store.db() as db:db.execute('BEGIN IMMEDIATE');db.rollback()
    def test_http_formal_binding_plan_execution_and_candidate_get(self):
        self.launch();code,p=self.request('/api/discovery/search-plan',self.data());self.assertEqual(code,200);self.transport.assert_not_called()
        code,r=self.request('/api/discovery/search-next',{'plan_id':p['plan_id'],'request_id':'formal_click_1','consent':True});self.assertEqual(code,200,r)
        self.assertEqual(r['budget']['provider_requests_attempted'],1);cid=r['batch']['items'][0]['candidate_id']
        self.assertEqual(self.request('/api/discovery/candidate?candidate_id='+cid)[0],200);self.assertEqual(self.request('/api/discovery/search-plan?plan_id='+p['plan_id'])[1]['state'],'complete');self.unchanged()
    def test_internal_error_logs_safe_frames_and_incident_without_request_or_exception_values(self):
        self.launch();code,p=self.request('/api/discovery/search-plan',dict(self.data(),query='DO_NOT_LOG_PRIVATE_QUERY'));self.assertEqual(code,200)
        stream=io.StringIO()
        with patch('backend.search_identity.index',side_effect=RuntimeError('DO_NOT_LOG_EXCEPTION_SECRET')),contextlib.redirect_stderr(stream):
            code,r=self.request('/api/discovery/search-next',{'plan_id':p['plan_id'],'request_id':'failure_click_1','consent':True})
        self.assertEqual(code,500);self.assertEqual(r['code'],'internal_error');self.assertIn(r['incident'],stream.getvalue())
        self.assertNotIn('DO_NOT_LOG_PRIVATE_QUERY',stream.getvalue());self.assertNotIn('DO_NOT_LOG_EXCEPTION_SECRET',stream.getvalue());self.assertNotIn('database permissions',r['error'])
        logged=json.loads(stream.getvalue());self.assertEqual(logged['exception_type'],'RuntimeError');self.assertTrue(logged['frames']);self.assertEqual(logged['path'],'/api/discovery/search-next');self.unchanged()

if __name__=='__main__':unittest.main()
