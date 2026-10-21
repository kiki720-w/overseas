import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from backend.common import AppError,STAGES
from backend.deepseek_tools import DeepSeekTools
from backend.discovery import DiscoveryService
from backend.production_runtime import ClosedStore
from backend.search_connections import SearchConnections
from backend.aliyun_search import AliyunSearch
from backend.search import TavilySearch,request_once
from backend.fetch_quality import assess
from backend.discovery_quality import classify_page
from test_deepseek_tools import row


class SearchQualityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=ClosedStore(Path(self.temp.name)/'mock.sqlite3',False)
        self.transport=Mock(return_value=(200,{'results':[row()]}))
        self.deep=DeepSeekTools(self.transport,key='synthetic-quality-only',cooldown=0)
        self.manager=SearchConnections(self.store,new_workspace=True,providers={'deepseek':self.deep,'aliyun':AliyunSearch(cooldown=0),'tavily':TavilySearch(cooldown=0)})
        self.service=DiscoveryService(self.store,None,'local',self.manager)
    def tearDown(self):self.temp.cleanup()
    def search(self,rows):
        self.transport.return_value=(200,{'results':copy.deepcopy(rows)})
        return self.service.search({'query':'precision gear machining company Germany lang:en','count':min(50,max(1,len(rows))),'consent':True})
    def test_mixed_unsafe_and_invalid_rows_keep_safe_results_without_fetch(self):
        unsafe=['http://127.0.0.1/','https://10.0.0.1/','https://[::1]/','https://localhost/','https://metadata.google.internal/','https://user:secret@example.com/','file:///etc/passwd','https://example.com:8443/','https://bad host/']
        rows=[row('https://safe.example/one')]+[row(url) for url in unsafe]+[{'title':'incomplete'},row('https://safe.example/two')]
        with patch('backend.search.request_once',side_effect=AssertionError('No secondary requests')):
            result=self.search(rows)
        self.assertEqual(len(result['items']),2);self.assertEqual(result['raw_result_count'],12);self.assertEqual(result['valid_result_count'],2);self.assertEqual(result['filtered_count'],10)
        self.assertEqual({v['reason_code'] for v in result['filter_reasons']},{'website_unsafe_url','website_url_invalid','search_invalid_response'})
        self.assertTrue(all(set(v)=={'index','reason_code'} for v in result['filter_reasons']));self.assertEqual(self.transport.call_count,1)
        self.assertNotIn('user:secret',json.dumps(result));self.assertEqual(result['verified_company_count'],0)
    def test_all_rejected_is_explicit_filtered_empty_still_estimated_billable(self):
        result=self.search([row('https://10.0.0.1/')]);self.assertEqual(result['items'],[]);self.assertEqual(result['filtered_count'],1)
        usage=self.deep.settings()['tool_usage']['search'];self.assertEqual(usage['provider_nonempty'],1);self.assertEqual(usage['business_filtered_empty'],1);self.assertEqual(usage['estimated_cost_cny'],.005)
        self.assertIn('website_unsafe_url',result['result_summary'])
    def test_exact_page_duplicate_only_unknown_entities_not_merged(self):
        a=row('https://www.techpilot.com:443/en/profiles/one');a['title']='One Manufacturing GmbH | Techpilot'
        b=row('https://www.techpilot.com/en/profiles/two');b['title']='Two Manufacturing GmbH | Techpilot'
        c=copy.deepcopy(a);c['url']='https://www.techpilot.com/en/profiles/one'
        d=copy.deepcopy(a);d['title']='Different Claim GmbH | Techpilot'
        result=self.search([a,b,c,d]);self.assertEqual(len(result['items']),3);self.assertEqual(result['deduplicated_count'],1)
    def test_cross_batch_aggregator_dedup_uses_page_and_entity_not_domain(self):
        plan=self.service.plans.create({'query':'gear','max_queries':2})
        a=row('https://techpilot.com/en/profiles/one');a['title']='One GmbH | Techpilot'
        b=row('https://techpilot.com/en/profiles/two');b['title']='Two GmbH | Techpilot'
        self.transport.return_value=(200,{'results':[a]});self.service.plans.next({'plan_id':plan['plan_id'],'request_id':'quality-one','consent':True})
        self.transport.return_value=(200,{'results':[a,b]});r=self.service.plans.next({'plan_id':plan['plan_id'],'request_id':'quality-two','consent':True})
        self.assertEqual(r['batch']['cross_batch_duplicate_count'],1);self.assertEqual(r['batch']['new_candidate_count'],1);self.assertEqual(r['batch']['directory_count'],1)
    def test_crm_exclusion_does_not_exclude_other_aggregate_entities(self):
        self.store.save_customer({'name':'One Manufacturing GmbH','type':'终端加工厂','stage':STAGES[0],'country':'Germany','product':'gear','website':'https://techpilot.com/en/profiles/one','email':'','contact_name':'','priority':False,'facts':''},True)
        a=row('https://techpilot.com/en/profiles/one');a['title']='One Manufacturing GmbH | Techpilot'
        b=row('https://techpilot.com/en/profiles/two');b['title']='Two Manufacturing GmbH | Techpilot'
        result=self.search([a,b]);self.assertEqual(result['excluded_existing_count'],1);self.assertEqual(len(result['items']),1)
        self.assertEqual(result['excluded_existing'][0]['reason'],'exact_entity_name')
    def test_long_body_remains_unverified_no_purchase_inference(self):
        quality=assess('We manufacture components to customer drawings using CNC machining and inspection in our production facility. '*8)
        self.assertEqual(quality['state'],'body_available_unverified');self.assertFalse(quality['background_investigation_completed']);self.assertEqual(quality['purchase_intent'],'unknown')
    def test_local_parse_failure_does_not_erase_provider_nonempty_charge(self):
        self.transport.return_value=(200,{'results':[row()]*6})
        with self.assertRaises(AppError):self.deep.search({'query':'gear','count':5})
        usage=self.deep.settings()['tool_usage']['search'];self.assertEqual(usage['provider_http_200'],1);self.assertEqual(usage['business_parse_failed'],1);self.assertEqual(usage['estimated_cost_min_cny'],.005);self.assertEqual(usage['estimated_cost_max_cny'],.005)
        self.assertEqual(usage['last_call']['business_outcome'],'business_parse_failed')
    def test_timeout_and_malformed_200_record_unknown_cost_interval_no_retry(self):
        self.transport.side_effect=TimeoutError()
        with self.assertRaises(AppError):self.deep.search({'query':'gear'})
        self.transport.side_effect=None;self.transport.return_value=(200,{})
        with self.assertRaises(AppError):self.deep.search({'query':'gear'})
        usage=self.deep.settings()['tool_usage']['search'];self.assertEqual(usage['estimated_cost_min_cny'],0);self.assertEqual(usage['estimated_cost_max_cny'],.01);self.assertEqual(usage['provider_outcome_unknown'],2);self.assertEqual(self.transport.call_count,2)
    def test_transport_invalid_json_preserves_received_http_status(self):
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False);response.code=200;response.read.return_value=b'not json'
        opener=Mock();opener.open.return_value=response
        with patch('backend.search.urllib.request.build_opener',return_value=opener),self.assertRaises(AppError) as caught:request_once({},'synthetic',DeepSeekTools.ENDPOINT)
        self.assertEqual(caught.exception.details['provider_http_status'],200)
    def test_query_target_and_business_labels_do_not_assert_buyers(self):
        result=self.search([dict(row(),snippet='We manufacture custom precision gears to drawings.')])
        self.assertEqual(result['executed_query'],'precision gear machining company Germany lang:en')
        self.assertEqual(result['items'][0]['source_classification']['business_role'],'terminal_manufacturing_statement')
        self.assertEqual(classify_page('https://example.com/','gear fixture','gear machining workholding')['business_role'],'unknown')
