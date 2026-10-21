"""Synthetic independent search keys, temporary DPAPI vault and injected transport only."""
import json,socket,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from backend.search import TavilySearch,request_once
from backend.credential_vault import CredentialVault,model_scope
from backend.models import ModelService
from backend.production_runtime import ClosedStore
from backend.common import AppError
from test_credential_vault import SyntheticProtector,resolver
import server

KEY='synthetic-independent-tavily'
class SearchCredentialTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.crypto=SyntheticProtector();self.vault=CredentialVault(self.root/'credentials'/'search','fixture|search',self.crypto)
        self.transport=Mock(return_value=(200,{'results':[]}))
        self.service=TavilySearch(self.transport,cooldown=0,vault=self.vault)
    def tearDown(self):self.tmp.cleanup()
    def save(self,**values):return self.service.save(dict(revision=self.service.revision,**values))
    def error(self,code,action):
        with self.assertRaises(AppError) as result:action()
        self.assertEqual(result.exception.code,code);self.assertNotIn(KEY,str(result.exception))
    def test_missing_search_key_does_not_call_transport_or_attempt_hook(self):
        attempt=Mock();self.error('search_key_required',lambda:self.service.search({'query':'synthetic','_attempt_hook':attempt}))
        self.transport.assert_not_called();attempt.assert_not_called();self.assertFalse(self.vault.directory.exists())
    def test_bearer_header_fixed_endpoint_no_key_in_body_or_proxy(self):
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False);response.code=200;response.read.return_value=b'{"results":[]}'
        opener=Mock();opener.open.return_value=response
        with patch('backend.search.urllib.request.build_opener',return_value=opener) as build:
            request_once({'query':'synthetic'},KEY)
        req=opener.open.call_args.args[0];self.assertEqual(req.get_header('Authorization'),'Bearer '+KEY)
        self.assertNotIn(KEY.encode(),req.data);self.assertIsNone(req.get_header('Cookie'));self.assertIsNone(req.get_header('X-tavily-access-mode'))
        self.assertEqual(build.call_args.args[0].proxies,{})
    def test_memory_default_and_explicit_encrypted_save_restore_delete(self):
        self.save(api_key=KEY);self.assertFalse(self.vault.directory.exists())
        self.save(persist=True);self.assertTrue(self.service.settings()['credential_saved_encrypted'])
        self.assertNotIn(KEY.encode(),self.vault.path(self.service.SCOPE).read_bytes())
        reopened=TavilySearch(self.transport,vault=self.vault);self.assertFalse(reopened.configured);self.assertEqual(self.crypto.loads,0)
        self.assertTrue(reopened.settings()['credential_saved_encrypted']);self.assertEqual(self.crypto.loads,0)
        self.service=reopened;self.save(restore_key=True,consent=True);self.assertTrue(self.service.configured)
        self.save(clear_key=True);self.assertFalse(self.service.configured);self.assertFalse(self.vault.contains(self.service.SCOPE));self.transport.assert_not_called()
    def test_no_model_key_reuse_and_separate_namespaces(self):
        store=ClosedStore(self.root/'synthetic.sqlite3',False)
        model_vault=CredentialVault(self.root/'credentials','fixture',self.crypto)
        model=ModelService(store,'local',resolver=resolver,credential_vault=model_vault)
        model.save(dict(provider='deepseek',base_url='https://api.deepseek.com',model='synthetic',api_key='synthetic-model-only',persist=True))
        self.assertFalse(self.service.configured);self.error('search_key_required',lambda:self.service.search({'query':'synthetic'}))
        self.save(api_key=KEY,persist=True);self.save(clear_key=True)
        self.assertTrue(model_vault.contains(model_scope(model.profile)));self.assertTrue(model.settings()['configured'])
    def test_restore_revision_validation_and_no_secret_response(self):
        self.save(api_key=KEY,persist=True)
        for body in ({'restore_key':True},{'restore_key':True,'consent':True,'persist':True},{'restore_key':True,'consent':True,'api_key':KEY}):
            self.error('consent_required',lambda body=body:self.save(**body))
        self.error('search_config_changed',lambda:self.service.save({'revision':0,'clear_key':True}))
        self.assertNotIn(KEY,json.dumps(self.service.settings()));self.assertEqual(self.crypto.loads,0)
    def test_status_errors_distinguish_auth_forbidden_limit_and_quota(self):
        self.save(api_key=KEY)
        for status,code in ((401,'search_auth_failed'),(403,'search_forbidden'),(429,'search_rate_limited'),(402,'search_quota_exceeded'),(432,'search_quota_exceeded'),(433,'search_quota_exceeded')):
            self.transport.return_value=(status,{'error':{'code':'synthetic_error','message':'never echo vendor text'}})
            self.error(code,lambda:self.service.search({'query':'synthetic'}));self.assertEqual(self.service.settings()['status'],code)
        self.assertEqual(self.transport.call_count,6)
    def test_secret_echo_and_key_change_during_request_are_rejected(self):
        self.save(api_key=KEY);self.transport.return_value=(200,{'results':[],'debug':KEY})
        self.error('search_invalid_response',lambda:self.service.search({'query':'synthetic'}))
        def changed(payload,key):self.save(clear_key=True);return 200,{'results':[]}
        self.transport.side_effect=changed;self.error('search_config_changed',lambda:self.service.search({'query':'synthetic'}))
    def test_key_never_appears_in_search_payload(self):
        self.save(api_key=KEY);self.service.search({'query':'synthetic'})
        payload,key=self.transport.call_args.args;self.assertEqual(key,KEY);self.assertNotIn(KEY,json.dumps(payload))
    def test_unknown_config_fields_do_not_mutate(self):
        for body in ({'provider':'model'},{'endpoint':'https://other.invalid/'},{'persist':'yes'},{'api_key':'bad\nvalue'}):
            self.error('invalid_input',lambda body=body:self.save(**body))
        self.assertFalse(self.service.configured)
    def test_http_settings_guards_separate_model_and_no_search_on_save(self):
        store=ClosedStore(self.root/'http.sqlite3',False)
        http=server.make_server(store,'local',port=0,search_provider=self.service)
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start();base='http://127.0.0.1:'+str(http.server_port)
        def request(path,data=None,headers=None):
            req=Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers or {})
            try:
                with urlopen(req) as r:return r.status,json.load(r)
            except HTTPError as r:return r.code,json.load(r)
        try:
            csrf=request('/api/meta')[1]['csrf']
            self.assertEqual(request('/api/search/settings',{'revision':0,'api_key':KEY})[0],403)
            headers={'Content-Type':'application/json','X-CSRF-Token':csrf}
            code,s=request('/api/search/settings',{'revision':0,'api_key':KEY},headers);self.assertEqual(code,200);self.assertTrue(s['configured']);self.assertNotIn(KEY,json.dumps(s))
            self.assertFalse(request('/api/model/settings')[1]['configured'])
            self.assertEqual(request('/api/search/settings',headers={'Origin':'https://external.invalid'})[0],403)
            self.assertEqual(request('/api/search/settings',{'revision':1,'clear_key':True},{**headers,'Origin':'https://external.invalid'})[0],403)
            self.transport.assert_not_called()
        finally:http.shutdown();http.server_close();thread.join()

if __name__=='__main__':unittest.main()
