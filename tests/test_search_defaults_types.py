"""Regression for acceptance defaults and distinct integrator/manufacturer semantics."""
import gc
import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import Mock
from backend.common import AppError,TYPES,STAGES
from backend.store import Store
from backend.discovery import DiscoveryService,discovery_defaults
from backend.search import TavilyKeylessSearch
from backend.website import WebsiteService
from backend.website_fetch import Collector
from test_website import fixture
from server import make_server

class SearchDefaultsTypesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'synthetic.db',False)
        self.transport=Mock(return_value=(200,{'results':[]}));self.provider=TavilyKeylessSearch(self.transport,cooldown=0,key="synthetic-search-only")
        self.service=DiscoveryService(self.store,WebsiteService(self.store,Collector(fixture)),'local',self.provider)
    def tearDown(self):gc.collect();self.tmp.cleanup()
    def test_known_seed_suppressed_without_factory_mutation(self):
        original=self.store.save_factory({'name':'Synthetic test factory','products':'合成 CNC 齿轮夹具','focus':'','countries':''})
        meta=self.service.meta();self.assertEqual(meta['defaults']['products'],'');self.assertEqual(meta['defaults_source'],'manual_required');self.assertTrue(meta['defaults_notice'])
        self.assertEqual(self.store.factory(),original);self.transport.assert_not_called()
    def test_normalized_known_seed_and_focus_are_suppressed(self):
        for data in [{'products':'  合成 CNC 齿轮夹具  '},{'products':'real CNC fixtures','focus':'合成 ＣＮＣ 齿轮夹具'}]:
            defaults,suppressed=discovery_defaults(data);self.assertTrue(suppressed);self.assertEqual(defaults['products'],'')
    def test_real_configuration_preserved_no_broad_prefix_filter(self):
        for product in ['CNC 齿轮夹具','合成材料加工夹具','CNC machining fixtures']:
            defaults,suppressed=discovery_defaults({'products':product,'countries':'user-selected market'})
            self.assertFalse(suppressed);self.assertEqual(defaults,{'products':product,'countries':'user-selected market'})
        self.assertEqual(discovery_defaults({'products':'合成 CNC 齿轮夹具','focus':'actual selected focus'})[0]['products'],'actual selected focus')
    def test_empty_defaults_cannot_trigger_empty_search_and_explicit_conditions_only(self):
        self.store.save_factory({'name':'Synthetic test factory','products':'合成 CNC 齿轮夹具','capabilities':'PRIVATE TEST FACTORY CAPABILITY'})
        with self.assertRaises(AppError):self.service.search({'query':self.service.meta()['defaults']['products'],'consent':True})
        self.transport.assert_not_called()
        self.service.search({'query':'actual fixtures','products':'actual fixtures','type':'设备集成商','consent':True})
        payload=json.dumps(self.transport.call_args.args[0],ensure_ascii=False)
        self.assertNotIn('合成',payload);self.assertNotIn('PRIVATE TEST',payload);self.assertIn('industrial equipment system integrator',payload)
        self.assertEqual(self.transport.call_args.args[0]['max_results'],8)
    def test_all_customer_types_map_distinctly(self):
        self.assertEqual(TYPES,['终端加工厂','机床厂家','经销商','设备集成商'])
        expected={'终端加工厂':'machine shop manufacturer','机床厂家':'machine tool manufacturer','经销商':'industrial distributor','设备集成商':'industrial equipment system integrator'}
        for kind,term in expected.items():
            r=self.service.search({'query':'fixture','type':kind,'consent':True});self.assertIn('Customer type: '+term,r['executed_query'])
        self.assertEqual(len({c.args[0]['query'] for c in self.transport.call_args_list}),4)
    def test_integrator_confirm_and_customer_edit_remain_integrator(self):
        c=self.service.preview({'url':'https://fixture.example/','consent':True})
        r=self.service.confirm({'candidate_id':c['candidate_id'],'name':'Synthetic integrator','type':'设备集成商','consent':True})
        customer=r['customer'];self.assertEqual(customer['type'],'设备集成商')
        edited=self.store.save_customer(dict(customer,name='Synthetic integrator edited'),False)
        self.assertEqual(edited['type'],'设备集成商');self.assertEqual(self.store.customer(edited['id'])['type'],'设备集成商')
    def test_http_meta_returns_empty_default_and_canonical_enum_without_search(self):
        self.store.save_factory({'name':'Synthetic test factory','products':'合成 CNC 齿轮夹具'})
        server=make_server(self.store,'local',port=0,discovery_service=self.service);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with urllib.request.urlopen('http://127.0.0.1:'+str(server.server_port)+'/api/discovery/meta') as r:meta=json.load(r)
            self.assertEqual(meta['defaults']['products'],'');self.assertIn('机床厂家',meta['types']);self.assertIn('设备集成商',meta['types']);self.transport.assert_not_called()
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
