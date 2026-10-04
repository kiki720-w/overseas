import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_models import Stub, resolver, SYNTHETIC_KEY, completion, output
from backend.models import ModelService
from backend import model_catalog as catalog
from backend.common import AppError
from backend.model_transport import HTTPSJSONTransport
from urllib.parse import urlsplit
import server

class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=server.Store(Path(self.tmp.name)/'synthetic.db',False)
        self.stub=Stub();self.service=ModelService(self.store,'local',self.stub,resolver)
    def tearDown(self):self.tmp.cleanup()
    def save(self,index=0,**extra):
        data=dict(option_id=catalog.ITEMS[index]['option_id'],catalog_version=catalog.VERSION)
        data.update(extra);return self.service.save(data)
    def error(self,code,data):
        before=self.service.settings().copy()
        with self.assertRaises(AppError) as caught:self.service.save(data)
        self.assertEqual(caught.exception.code,code)
        self.assertEqual(before,self.service.settings())
    def test_catalog_read_only_copy_and_no_calls(self):
        c=catalog.catalog();self.assertEqual(len(c['items']),12)
        self.assertEqual(len({i['provider'] for i in c['items']}),6)
        self.assertEqual(len({i['option_id'] for i in c['items']}),12)
        for i in c['items']:
            self.assertTrue(i['source_url'].startswith('https://'))
            self.assertTrue(i['credentialScope']);self.assertTrue(i['requestAdapter'])
        c['items'][0]['model']='mutation';self.assertNotEqual(catalog.catalog()['items'][0]['model'],'mutation')
        self.assertEqual(self.stub.calls,[])
    def test_mapping_and_nonsecret_storage(self):
        for index,item in enumerate(catalog.ITEMS):
            value=self.save(index,api_key=SYNTHETIC_KEY)
            self.assertEqual(value['option_id'],item['option_id'])
            self.assertEqual(value['credentialScope'],item['credentialScope'])
            for k in ('provider','base_url','model'):self.assertEqual(value[k],item[k])
            self.assertNotIn(SYNTHETIC_KEY,json.dumps(value))
        self.assertEqual(self.stub.calls,[])
        with self.store.db() as db:self.assertNotIn(SYNTHETIC_KEY,db.execute('select data from model_config').fetchone()[0])
    def test_invalid_version_option_and_override_are_atomic(self):
        self.save(api_key=SYNTHETIC_KEY)
        for code,data in [('catalog_changed',dict(option_id=catalog.ITEMS[1]['option_id'],catalog_version='old')),
                          ('catalog_option_invalid',dict(option_id='unknown',catalog_version=catalog.VERSION))]:self.error(code,data)
        for k in ('provider','base_url','model','credentialScope','requestAdapter'):
            self.error('catalog_option_invalid',dict(option_id=catalog.ITEMS[1]['option_id'],catalog_version=catalog.VERSION,**{k:'override'}))
    def test_same_scope_keeps_key_other_scope_clears(self):
        self.save(api_key=SYNTHETIC_KEY);self.stub.result=completion('OK')
        self.service.test(dict(consent=True,revision=self.service.settings()['revision']))
        v=self.save(1);self.assertTrue(v['configured']);self.assertEqual(v['generation_status'],'not_tested')
        v=self.save(2);self.assertFalse(v['configured'])
        v=self.save(3,api_key=SYNTHETIC_KEY);self.assertTrue(v['configured'])
        v=self.service.save(dict(provider='kimi-global',base_url='https://api.moonshot.ai/v1',model='manual'))
        self.assertFalse(v['configured'])
    def test_existing_profiles_are_preserved_on_catalog_read_restart(self):
        old=self.service.save(dict(provider='custom',base_url='https://existing.example/v1',model='original',name='existing',api_key=SYNTHETIC_KEY))
        with self.store.db() as db:before=db.execute('select data from model_config').fetchone()[0]
        catalog.catalog();new=ModelService(self.store,'local',self.stub,resolver).settings()
        for k in ('provider','base_url','model','name','revision'):self.assertEqual(old[k],new[k])
        self.assertEqual(new['selection_mode'],'custom');self.assertIsNone(new['option_id']);self.assertFalse(new['configured'])
        with self.store.db() as db:self.assertEqual(before,db.execute('select data from model_config').fetchone()[0])
        self.assertEqual(self.stub.calls,[])
    def test_matching_legacy_profile_keeps_key_when_using_preset(self):
        self.service.save(dict(provider='deepseek',base_url='https://api.deepseek.com',model='deepseek-v4-pro',api_key=SYNTHETIC_KEY))
        self.assertTrue(self.save(3)['configured'])
    def test_all_adapters_have_exact_safe_parameter_subsets(self):
        expected={'qwen':{'max_completion_tokens','enable_thinking'},'deepseek':{'max_tokens','thinking'},
                  'kimi-k2.6':{'max_tokens','thinking'},'kimi-k3':{'max_tokens','reasoning_effort'},
                  'glm':{'max_tokens','thinking'},'doubao':{'max_tokens','thinking'},
                  'minimax-m3':{'max_completion_tokens','thinking','reasoning_split'},
                  'minimax-m2.7':{'max_tokens','reasoning_split'}}
        for index,item in enumerate(catalog.ITEMS):
            self.save(index,api_key=SYNTHETIC_KEY);self.stub.result=completion('OK')
            self.service.test(dict(consent=True,revision=self.service.settings()['revision']))
            request=self.stub.calls[-1];p=request['payload']
            self.assertEqual(request['base'],item['base_url']);self.assertEqual(request['path'],'chat/completions')
            self.assertEqual(set(p)-{'model','messages'},expected[item['requestAdapter']])
            self.assertEqual(p['model'],item['model'])
            if item['requestAdapter']=='glm':self.assertEqual(p['thinking'],{'type':'enabled'})
            if item['requestAdapter']=='kimi-k3':self.assertEqual(p['reasoning_effort'],'low')
            if item['requestAdapter'].startswith('minimax'):self.assertTrue(p['reasoning_split'])
            business=catalog.payload(item,[{'role':'user','content':'SYNTHETIC'}],'analyze')
            self.assertEqual(set(business),set(p))
            for k in ('temperature','top_p','n','presence_penalty','frequency_penalty','tools','enable_search'):self.assertNotIn(k,p)
    def test_catalog_cas_and_unsafe_advanced_are_still_guarded(self):
        self.save(api_key=SYNTHETIC_KEY)
        self.error('profile_changed',dict(option_id=catalog.ITEMS[1]['option_id'],catalog_version=catalog.VERSION,revision=0))
        self.error('unsafe_endpoint',dict(provider='custom',base_url='https://127.0.0.1/v1',model='manual'))
    def test_transport_appends_only_completions_to_each_full_base(self):
        with patch('backend.model_transport.PinnedHTTPSConnection') as connection:
            connection.return_value.getresponse.return_value.status=200
            connection.return_value.getresponse.return_value.read.return_value=b'{}'
            for item in catalog.ITEMS:
                HTTPSJSONTransport()(item['base_url'],'chat/completions',SYNTHETIC_KEY,{},['93.184.216.34'],'generation_test')
                args=connection.return_value.request.call_args.args
                self.assertEqual(args[0],'POST')
                self.assertEqual(args[1],urlsplit(item['base_url']).path.rstrip('/')+'/chat/completions')

if __name__=='__main__':unittest.main()
