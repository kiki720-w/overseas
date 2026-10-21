import gc
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock
from backend.common import AppError,TYPES,STAGES
from backend.store import Store
from backend.discovery import DiscoveryService
from backend.search import TavilyKeylessSearch
from backend.website import WebsiteService
from backend.website_fetch import Collector
from test_website import fixture
from server import make_server

def result(url='https://fixture.example/',title='Synthetic Fixture Company LLC'):
    return {'url':url,'title':title,'content':'Public CNC machining capabilities, not a purchase signal.','summary':'# '+title+'\nWe manufacture gears.\nWe provide gear hobbing services.'}

class SearchV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'synthetic.db',False)
        self.transport=Mock(return_value=(200,{'results':[result()]}));self.provider=TavilyKeylessSearch(self.transport,cooldown=0,key="synthetic-search-only")
        self.s=DiscoveryService(self.store,WebsiteService(self.store,Collector(fixture)),'local',self.provider)
    def tearDown(self):gc.collect();self.tmp.cleanup()
    def plan(self,**data):return self.s.plans.create(dict({'query':'CNC 齿轮夹具 China','products':'CNC 齿轮夹具','countries':'China','type':TYPES[0]},**data))
    def next(self,p,rid='click_0001',**data):return self.s.plans.next(dict({'plan_id':p['plan_id'],'request_id':rid,'consent':True},**data))
    def customer(self,name='Existing Machine LLC',website='https://existing.example/'):
        return self.store.save_customer({'name':name,'website':website,'type':TYPES[0],'stage':STAGES[0]},False)
    def count(self,table):
        db=self.store.db()
        try:return db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
        finally:db.close()
    def test_planning_no_provider_or_database_write_and_public_conditions_only(self):
        self.store.save_factory({'name':'Private synthetic factory','products':'DO NOT SEND FACTORY TEXT','capabilities':'DO NOT SEND CAPABILITY'})
        p=self.plan(regions=['Suzhou'],industries=['automotive machining'],product_synonyms=['gear workholding'])
        self.transport.assert_not_called();self.assertEqual(p['budget']['max_provider_requests'],6);self.assertEqual(p['budget']['max_provider_requests_per_click'],1)
        self.assertLessEqual(len(p['queries']),6);self.assertIn('Suzhou',p['queries'][1]['executed_query']);self.assertIn('China',p['queries'][1]['executed_query']);self.assertNotIn('DO NOT SEND',json.dumps(p))
        for table in ['customers','records','calls','discovery_candidates']:self.assertEqual(self.count(table),0)
    def test_validation_and_fixed_budget(self):
        for args in [{'query':''},{'max_queries':9},{'max_queries':True},{'max_queries':0},{'regions':['x']*5},{'industries':'x'},{'product_synonyms':['x'*101]},{'type':'unknown'}]:
            with self.assertRaises(AppError):self.plan(**args)
        self.transport.assert_not_called()
    def test_each_next_one_request_replay_zero_extra_and_no_crm(self):
        p=self.plan();r=self.next(p);again=self.next(p)
        self.assertEqual(self.transport.call_count,1);self.assertTrue(again['replayed']);self.assertEqual(r['batch'],again['batch']);self.assertEqual(r['budget']['provider_requests_attempted'],1)
        for table in ['customers','records','calls','discovery_candidates']:self.assertEqual(self.count(table),0)
    def test_next_requires_consent_and_request_identifier(self):
        p=self.plan()
        for data in [{'consent':False},{'request_id':'x'},{'request_id':'x'*65}]:
            with self.assertRaises(AppError):self.next(p,**data)
        self.transport.assert_not_called()
    def test_continue_retains_distinct_pages_without_unverified_entity_merge(self):
        p=self.plan();r1=self.next(p)
        self.transport.return_value=(200,{'results':[result('https://www.fixture.example/about'),result('https://new.example/','New Company LLC')]})
        r2=self.next(p,'click_0002');self.assertEqual(r2['batch']['cross_batch_duplicate_count'],0);self.assertEqual(r2['batch']['new_company_count'],1)
        self.assertEqual(r1['batch']['items'][0]['domain'],'fixture.example');self.assertEqual([c['domain'] for c in r2['batch']['items']],['fixture.example','new.example'])
        self.assertEqual(len(self.s.plans.get({'plan_id':p['plan_id']})['batches']),2);self.assertEqual(self.transport.call_count,2)
    def test_budget_exhaustion_stops_without_extra_request(self):
        p=self.plan(max_queries=2)
        self.next(p);r=self.next(p,'click_0002');self.assertEqual(r['state'],'complete');self.assertFalse(r['has_more']);self.assertEqual(r['budget']['remaining'],0)
        with self.assertRaises(AppError) as e:self.next(p,'click_0003')
        self.assertEqual(e.exception.code,'search_plan_closed');self.assertEqual(self.transport.call_count,2)
    def test_eight_query_budget_continues_beyond_legacy_five_batches(self):
        p=self.plan(max_queries=8,regions=['Suzhou','Ningbo','Wuxi'],industries=['automotive','gear machining'])
        self.assertEqual(len(p['queries']),8)
        for i in range(8):
            self.transport.return_value=(200,{'results':[result('https://new'+str(i)+'.example/')]})
            r=self.next(p,'click_'+str(i).zfill(4))
            self.assertEqual(r['batch']['new_company_count'],1);self.assertEqual(r['budget']['provider_requests_attempted'],i+1)
        self.assertEqual(r['state'],'complete');self.assertEqual(self.transport.call_count,8)
        with self.assertRaises(AppError):self.next(p,'click_9999')
        self.assertEqual(self.transport.call_count,8)
    def test_empty_results_advance_without_auto_fill(self):
        self.transport.return_value=(200,{'results':[]});p=self.plan();r=self.next(p)
        self.assertEqual(r['batch']['new_company_count'],0);self.assertEqual(r['next_query_index'],1);self.assertEqual(self.transport.call_count,1)
    def test_directory_not_counted_as_new_company(self):
        self.transport.return_value=(200,{'results':[result('https://rocketreach.co/demo')]});r=self.next(self.plan())
        self.assertEqual(r['batch']['new_company_count'],0);self.assertEqual(r['batch']['directory_count'],1);self.assertEqual(r['batch']['new_candidate_count'],1)
    def test_existing_crm_domain_and_reliable_full_name_filtered_at_search(self):
        a=self.customer();b=self.customer('Another Existing Ltd','https://another.example/')
        self.transport.return_value=(200,{'results':[result('https://www.existing.example/about'),result('https://new-alias.example/','Another Existing Ltd'),result('https://fresh.example/','Fresh Company LLC')]})
        r=self.s.search({'query':'CNC','consent':True});self.assertEqual(r['excluded_existing_count'],2);self.assertEqual(len(r['items']),1)
        self.assertEqual({x['reason'] for x in r['excluded_existing']},{'exact_domain','exact_full_title'});self.assertEqual(self.count('customers'),2)
    def test_uncertain_name_and_subdomain_remain_with_hint_no_alias_merging(self):
        self.customer();self.customer('CNC Fixtures','https://generic-existing.example/')
        self.transport.return_value=(200,{'results':[result('https://sales.existing.example/','Existing Machine LLC | Products'),result('https://unknown.example/','CNC Fixtures')]})
        r=self.s.search({'query':'CNC','consent':True});self.assertEqual(len(r['items']),2);self.assertEqual(r['excluded_existing_count'],0);self.assertTrue(r['items'][0]['possible_existing'])
    def test_reject_and_stash_not_suppressed_across_new_queries(self):
        r=self.s.search({'query':'CNC','consent':True});c=r['items'][0];self.s.reject({'candidate_id':c['candidate_id']})
        again=self.s.search({'query':'other CNC','consent':True})['items'][0];self.assertNotEqual(again['candidate_id'],c['candidate_id'])
        self.s.stash({'candidate_id':again['candidate_id'],'consent':True});third=self.s.search({'query':'third CNC','consent':True})
        self.assertEqual(len(third['items']),1);self.assertEqual(self.count('customers'),0)
    def test_customer_added_between_batches_is_excluded_fresh(self):
        p=self.plan();self.next(p);self.customer('New Existing LLC','https://new.example/')
        self.transport.return_value=(200,{'results':[result('https://new.example/','New Existing LLC')]})
        r=self.next(p,'click_0002');self.assertEqual(r['batch']['excluded_existing_count'],1);self.assertEqual(r['batch']['new_company_count'],0)
    def test_cap_stops_counts_attempt_replays_error_no_retry_or_auth(self):
        self.transport.return_value=(429,{'error':{'code':'cap','retry_after_seconds':60,'next_actions':[{'action':'login'}]}});p=self.plan()
        for _ in range(2):
            with self.assertRaises(AppError) as e:self.next(p)
            self.assertEqual(e.exception.code,'search_rate_limited');self.assertEqual(e.exception.details['budget']['provider_requests_attempted'],1);self.assertEqual(e.exception.details['plan_state'],'stopped')
        self.assertEqual(self.transport.call_count,1);self.assertFalse(self.s.plans.get({'plan_id':p['plan_id']})['has_more'])
    def test_network_failure_stops_once(self):
        self.transport.side_effect=urllib.error.URLError('test network');p=self.plan()
        with self.assertRaises(AppError) as e:self.next(p)
        self.assertEqual(e.exception.code,'search_network_error');self.assertEqual(e.exception.details['budget']['remaining'],5);self.assertEqual(self.transport.call_count,1)
    def test_local_cooldown_does_not_consume_budget_and_manual_retry_allowed(self):
        self.provider.cooldown=5;p=self.plan();self.next(p)
        with self.assertRaises(AppError) as e:self.next(p,'click_0002')
        self.assertEqual(e.exception.code,'search_busy');self.assertEqual(e.exception.details['plan_state'],'ready');self.assertEqual(e.exception.details['budget']['provider_requests_attempted'],1)
        self.provider.last=None;self.next(p,'click_0002');self.assertEqual(self.transport.call_count,2)
    def test_manual_stop_no_reject_no_budget_reset(self):
        p=self.plan();r=self.next(p);stopped=self.s.plans.stop({'plan_id':p['plan_id']});self.assertEqual(stopped['state'],'stopped');self.assertEqual(stopped['budget']['provider_requests_attempted'],1)
        with self.assertRaises(AppError):self.next(p,'click_0002')
        self.assertEqual(self.s.candidate(r['batch']['items'][0])['state'],'preview');self.assertEqual(self.count('discovery_events'),0)
    def test_concurrent_clicks_do_not_start_second_transport(self):
        entered=threading.Event();release=threading.Event()
        def transport(payload,key):entered.set();release.wait(2);return 200,{'results':[result()]}
        self.provider.transport=transport;p=self.plan()
        with ThreadPoolExecutor(2) as pool:
            first=pool.submit(self.next,p);self.assertTrue(entered.wait(1))
            with self.assertRaises(AppError) as e:self.next(p,'click_0002')
            self.assertEqual(e.exception.code,'search_plan_busy');release.set();self.assertEqual(first.result()['budget']['provider_requests_attempted'],1)
    def test_stop_during_inflight_request_prevents_any_followup(self):
        entered=threading.Event();release=threading.Event()
        def transport(payload,key):entered.set();release.wait(2);return 200,{'results':[result()]}
        self.provider.transport=transport;p=self.plan()
        with ThreadPoolExecutor(1) as pool:
            first=pool.submit(self.next,p);self.assertTrue(entered.wait(1));self.s.plans.stop({'plan_id':p['plan_id']});release.set()
            r=first.result();self.assertEqual(r['state'],'stopped');self.assertFalse(r['has_more'])
        with self.assertRaises(AppError):self.next(p,'click_0002')
        self.assertEqual(self.s.plans.get({'plan_id':p['plan_id']})['budget']['provider_requests_attempted'],1)
    def test_plan_capacity_and_persistent_reopen(self):
        a=self.plan();self.assertEqual(self.plan()['plan_id'],a['plan_id']);self.plan(countries='Germany')
        with self.assertRaises(AppError) as e:self.plan(countries='USA')
        self.assertEqual(e.exception.code,'search_plan_capacity');self.s.plans.plans[a['plan_id']]['expires']=0
        self.assertEqual(self.s.plans.get({'plan_id':a['plan_id']})['plan_id'],a['plan_id'])
        self.s.plans.stop({'plan_id':a['plan_id']});self.plan(countries='USA');self.transport.assert_not_called()
    def test_stash_research_confirm_then_future_search_excludes_customer(self):
        p=self.plan();c=self.next(p)['batch']['items'][0];self.s.stash({'candidate_id':c['candidate_id'],'consent':True});self.s.preview({'candidate_id':c['candidate_id'],'url':c['url'],'consent':True})
        self.s.confirm({'candidate_id':c['candidate_id'],'name':'Synthetic Fixture Company LLC','type':'设备集成商','consent':True})
        r=self.next(p,'click_0002');self.assertEqual(r['batch']['excluded_existing_count'],1);self.assertEqual(r['batch']['new_company_count'],0);self.assertEqual(self.count('customers'),1)
    def test_http_plan_routes_csrf_and_no_auto_search(self):
        server=make_server(self.store,'local',port=0,discovery_service=self.s);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();origin='http://127.0.0.1:'+str(server.server_port)
        try:
            with urllib.request.urlopen(origin+'/api/meta') as r:csrf=json.load(r)['csrf']
            def post(path,data,token=csrf):
                request=urllib.request.Request(origin+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json','X-CSRF-Token':token,'Origin':origin})
                with urllib.request.urlopen(request) as r:return json.load(r)
            with self.assertRaises(urllib.error.HTTPError) as e:post('/api/discovery/search-plan',{'query':'CNC'},token='bad')
            self.assertEqual(e.exception.code,403)
            p=post('/api/discovery/search-plan',{'query':'CNC','max_queries':1});self.transport.assert_not_called()
            with urllib.request.urlopen(origin+'/api/discovery/search-plan?plan_id='+p['plan_id']) as r:self.assertEqual(json.load(r)['state'],'ready')
            result=post('/api/discovery/search-next',{'plan_id':p['plan_id'],'request_id':'http_0001','consent':True});self.assertEqual(result['state'],'complete');self.assertEqual(self.transport.call_count,1)
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
