"""No network: synthetic keys, injected responses, isolated workspaces."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from backend.aliyun_search import AliyunSearch
from backend.search import TavilySearch,request_once
from backend.search_connections import SearchConnections
from backend.production_runtime import ClosedStore
from backend.credential_vault import CredentialVault
from backend.discovery import DiscoveryService
from backend.common import AppError
from test_credential_vault import SyntheticProtector

KEY='synthetic-aliyun-only'
class AliyunSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=ClosedStore(self.root/'test.sqlite3',False)
        self.crypto=SyntheticProtector();self.vault=CredentialVault(self.root/'vault','synthetic',self.crypto)
        self.transport=Mock(return_value=(200,{'pageItems':[{'title':'Synthetic Fixture Company','link':'https://fixture-example.com/','snippet':'We manufacture gear machining fixtures.'}]}))
        self.ali=AliyunSearch(self.transport,cooldown=0,key=KEY,vault=self.vault,engine_type='GlobalAdvanced')
        self.tavily=TavilySearch(Mock(return_value=(200,{'results':[]})),cooldown=0,vault=self.vault)
        self.manager=SearchConnections(self.store,providers={'aliyun':self.ali,'tavily':self.tavily})
    def tearDown(self):self.tmp.cleanup()
    def error(self,code,fn):
        with self.assertRaises(AppError) as caught:fn()
        self.assertEqual(caught.exception.code,code);self.assertNotIn(KEY,str(caught.exception))
    def select(self,provider,engine=None):
        value={'provider':provider,'revision':self.manager.revision}
        if engine is not None:value['engine_type']=engine
        return self.manager.save(value)
    def save(self,**data):return self.manager.save(dict(data,revision=self.manager.revision))
    def test_fixed_bearer_endpoint_and_no_proxy_redirect_or_body_secret(self):
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False);response.code=200;response.read.return_value=b'{"pageItems":[]}'
        opener=Mock();opener.open.return_value=response
        with patch('backend.search.urllib.request.build_opener',return_value=opener) as build:request_once({'query':'synthetic'},KEY,self.ali.ENDPOINT)
        req=opener.open.call_args.args[0];self.assertEqual(req.full_url,self.ali.ENDPOINT);self.assertEqual(req.get_header('Authorization'),'Bearer '+KEY);self.assertNotIn(KEY.encode(),req.data);self.assertEqual(build.call_args.args[0].proxies,{})
    def test_normal_empty_and_global_unsupported_options_omitted(self):
        result=self.ali.search({'query':'synthetic gear'});self.assertEqual(result['results'][0]['url'],'https://fixture-example.com/');self.assertEqual(result['provider'],'aliyun')
        payload=self.transport.call_args.args[0];self.assertEqual(payload['engineType'],'GlobalAdvanced');self.assertNotIn('advancedParams',payload);self.assertNotIn('summary',payload['contents']);self.assertFalse(payload['contents']['mainText'])
        self.transport.return_value=(200,{'pageItems':[]});self.assertEqual(self.ali.search({'query':'none'})['results'],[])
    def test_china_tiers_and_no_paid_augmentation(self):
        for tier in ['CNLiteBasic','CNAuto']:
            self.ali.engine_type=tier;self.ali.search({'query':'synthetic'});payload=self.transport.call_args.args[0]
            self.assertEqual(payload['advancedParams'],{'numResults':'8'});self.assertFalse(payload['contents']['summary']);self.assertFalse(payload['contents']['rerankScore'])
    def test_missing_key_tier_long_query_never_reserves_or_calls(self):
        hook=Mock();self.ali._key='';self.error('search_key_required',lambda:self.ali.search({'query':'synthetic','_attempt_hook':hook}))
        self.ali._key=KEY;self.ali.engine_type='';self.error('search_tier_required',lambda:self.ali.search({'query':'synthetic','_attempt_hook':hook}))
        self.ali.engine_type='CNAuto';self.error('search_query_too_long',lambda:self.ali.search({'query':'x'*501,'_attempt_hook':hook}));hook.assert_not_called();self.transport.assert_not_called()
    def test_error_limit_malformed_secret_echo_and_timeout_no_retry(self):
        for status,code in [(401,'search_auth_failed'),(403,'search_forbidden'),(429,'search_rate_limited'),(500,'search_provider_error')]:
            self.transport.return_value=(status,{'message':'do not display vendor body'});self.error(code,lambda:self.ali.search({'query':'synthetic'}))
        for body in [{},{'pageItems':{}},{'pageItems':[{'title':1}]},{'pageItems':[],'secret':KEY}]:
            self.transport.return_value=(200,body);self.error('search_invalid_response',lambda:self.ali.search({'query':'synthetic'}))
        self.transport.side_effect=TimeoutError();before=self.transport.call_count;self.error('search_timeout',lambda:self.ali.search({'query':'synthetic'}));self.assertEqual(self.transport.call_count,before+1)
    def test_new_defaults_deepseek_existing_defaults_tavily_and_reopen(self):
        self.assertEqual(self.manager.settings()['provider'],'tavily');self.assertFalse(self.manager.path.exists())
        other=ClosedStore(self.root/'new.sqlite3',False);fresh=SearchConnections(other,new_workspace=True);self.assertEqual(fresh.settings()['provider'],'deepseek');self.assertFalse(fresh.configured)
        reopened=SearchConnections(other);self.assertEqual(reopened.settings()['provider'],'deepseek');self.assertEqual(reopened.settings().get('engine_type',''),'')
        self.assertEqual(json.loads(fresh.path.read_text()),{'provider':'deepseek','engine_type':''})
    def test_provider_and_model_credentials_isolated_no_automatic_restore(self):
        self.select('tavily');self.save(api_key='synthetic-tavily-only',persist=True);self.select('aliyun');self.save(api_key=KEY,persist=True)
        self.assertTrue(self.vault.contains(self.ali.SCOPE));self.assertTrue(self.vault.contains(self.tavily.SCOPE));self.assertNotEqual(self.ali.SCOPE,self.tavily.SCOPE)
        self.save(clear_key=True);self.assertTrue(self.vault.contains(self.tavily.SCOPE));self.assertFalse(self.vault.contains(self.ali.SCOPE))
        reopened=AliyunSearch(vault=self.vault);self.assertFalse(reopened.configured);self.assertEqual(self.crypto.loads,0)
        self.assertNotIn(KEY,json.dumps(self.manager.settings()));self.assertNotIn(KEY,self.manager.path.read_text())
    def test_no_provider_fallback_and_attempt_estimate(self):
        self.select('aliyun');self.transport.return_value=(429,{})
        self.error('search_rate_limited',lambda:self.manager.search({'query':'synthetic'}));self.tavily.transport.assert_not_called()
        self.assertEqual(self.manager.settings()['usage']['requests_attempted'],1);self.assertEqual(self.manager.settings()['usage']['estimated_cost_cny'],.056)
    def test_switch_with_key_rejected_and_stale_revision(self):
        self.error('search_scope_confirmation_required',lambda:self.save(provider='aliyun',api_key=KEY));self.assertEqual(self.manager.selected,'tavily')
        self.select('aliyun');self.error('search_config_changed',lambda:self.manager.save({'revision':0,'clear_key':True}));self.error('invalid_input',lambda:self.select('aliyun','Generic'))
    def test_plan_attempts_provenance_and_configuration_change(self):
        self.select('aliyun');service=DiscoveryService(self.store,Mock(),search_provider=self.manager)
        plan=service.plans.create({'query':'gear fixtures','products':'gear fixtures','max_queries':1})
        result=service.plans.next({'plan_id':plan['plan_id'],'request_id':'synthetic_click_1','consent':True})
        candidate=result['batch']['items'][0];self.assertEqual(candidate['search_provenance']['provider'],'aliyun');self.assertEqual(candidate['search_provenance']['engine_type'],'GlobalAdvanced');self.assertTrue(candidate['research_required']);self.assertEqual(result['budget']['estimated_cost_cny'],.056)
        self.error('candidate_research_required',lambda:service.confirm({'candidate_id':candidate['candidate_id'],'name':'Synthetic Fixture Company','type':'终端加工厂','consent':True}))
        replay=service.plans.next({'plan_id':plan['plan_id'],'request_id':'synthetic_click_1','consent':True});self.assertTrue(replay['replayed']);self.assertEqual(self.transport.call_count,1)
        other=service.plans.create({'query':'other','max_queries':1});self.select('tavily');self.error('search_plan_config_changed',lambda:service.plans.next({'plan_id':other['plan_id'],'request_id':'synthetic_click_2','consent':True}))

if __name__=='__main__':unittest.main()
