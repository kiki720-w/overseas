import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from backend.common import AppError
from backend.deepseek_tools import DeepSeekTools
from backend.discovery import DiscoveryService
from backend.production_runtime import ClosedStore
from backend.search_ledger import fingerprint
from test_deepseek_tools import row


class SearchOptionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=ClosedStore(Path(self.temp.name)/'synthetic.sqlite3',False)
        def response(tool,payload,key):
            return 200,{'results':[dict(row('https://count%d-factory%02d.example.com/about'%(payload['count'],i)),title='DEMO Gear Works %02d GmbH'%i,summary='We provide gear hobbing and gear grinding services.',content='# DEMO original body' if payload.get('need_content') and i%2 else '') for i in range(payload['count'])]}
        self.transport=Mock(side_effect=response);self.provider=DeepSeekTools(self.transport,key='synthetic-only',cooldown=0)
        self.service=DiscoveryService(self.store,None,'local',self.provider)
    def tearDown(self):self.temp.cleanup()
    def plan(self,**changes):
        return self.service.plans.create(dict(query='Find gear machining businesses',products='gear workholding',countries='Germany',max_queries=1,**changes))
    def run_one(self,plan):
        return self.service.plans.next({'plan_id':plan['plan_id'],'request_id':'synthetic-click','consent':True})
    def test_default_and_fifty_preserve_all_sources_without_eight_page_cap(self):
        for count in (20,50):
            plan=self.plan(count=count)
            result=self.run_one(plan)
            self.assertEqual(len(result['batch']['items']),count)
            self.assertEqual(result['batch']['display_limit_omitted_count'] if 'display_limit_omitted_count' in result['batch'] else 0,0)
            self.assertEqual(plan['budget']['max_results_per_request'],count)
            self.assertEqual(self.transport.call_args.args[1]['count'],count)
            self.assertEqual(self.transport.call_args.args[1]['ori_question'],'Find gear machining businesses')
            self.assertEqual(result['batch']['verified_company_count'],0)
            self.assertEqual(self.store.customers({})['total'],0)
    def test_explicit_original_and_content_state_survive_restart(self):
        p=self.plan(count=10,need_content=True,ori_question='Find German gear makers that perform grinding')
        result=self.run_one(p);items=result['batch']['items']
        self.assertEqual({c['search_provenance']['content_state'] for c in items},{'missing','returned_unverified'})
        self.assertTrue(all(c['research_required'] for c in items))
        self.assertEqual(self.transport.call_args.args[1]['ori_question'],'Find German gear makers that perform grinding')
        reopened=DiscoveryService(self.store,None,'local',DeepSeekTools(Mock(),cooldown=0))
        saved=reopened.plans.get({'plan_id':p['plan_id']})
        self.assertEqual(saved['conditions']['search_options']['count'],10)
        self.assertEqual(len(saved['batches'][0]['items']),10)
        self.assertFalse(reopened.search_provider.configured)
    def test_invalid_options_never_call_provider(self):
        for changes in ({'count':0},{'count':51},{'count':True},{'need_content':'yes'},{'ori_question':'x'*129}):
            with self.subTest(changes=changes),self.assertRaises(AppError):self.plan(**changes)
        self.transport.assert_not_called()
    def test_no_private_factory_or_documents_in_wire_payload(self):
        factory=self.store.factory();factory.update(capabilities='SYNTHETIC-PRIVATE-FACTORY',products='PRIVATE-NOT-PUBLIC')
        self.store.save_factory(factory)
        p=self.plan();self.run_one(p)
        text=json.dumps(self.transport.call_args.args[1])
        self.assertNotIn('PRIVATE',text)
        self.assertEqual(self.transport.call_args.args[1]['count'],20)
    def test_localized_buyer_queries_use_public_market_and_no_default_exclusion(self):
        p=self.service.plans.create({'query':'Find gear workshops','products':'gear workholding','countries':'China Germany','max_queries':8})
        queries=[q['executed_query'] for q in p['queries']]
        self.assertTrue(any('滚齿' in q or '磨齿' in q for q in queries))
        self.assertTrue(any('Wälzfräsen' in q or 'Zahnradschleifen' in q for q in queries))
        self.assertTrue(all('-site:' not in q and 'after:' not in q for q in queries))
    def test_wire_fingerprint_distinguishes_real_options_but_retains_legacy(self):
        old=fingerprint('deepseek','gear',5)
        self.assertEqual(old,fingerprint('deepseek','gear',5,{'need_content':False}))
        self.assertNotEqual(old,fingerprint('deepseek','gear',5,{'need_content':True}))
        self.assertNotEqual(fingerprint('deepseek','gear',20,{'ori_question':'Find factories'}),fingerprint('deepseek','gear',20,{'ori_question':'Find equipment sellers'}))


if __name__=='__main__':unittest.main()
