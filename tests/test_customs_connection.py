import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from backend.common import AppError
from backend.customs_connection import CustomsConnection
from backend.customs import CustomsService

class CustomsConnectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=SimpleNamespace(path=Path(self.tmp.name)/'synthetic.sqlite3');self.c=CustomsConnection(self.store)
        self.profile={'provider_name':'Synthetic Provider','endpoint':'https://api.example.invalid/trade','auth_type':'bearer','header_name':'X-API-Key','documentation_url':''}
    def tearDown(self):self.tmp.cleanup()
    def save(self,key='synthetic-test-only',profile=None):return self.c.save({'revision':self.c.revision,'profile':profile or self.profile,'api_key':key})
    def test_save_never_connects_and_secret_never_leaves_memory(self):
        with patch('urllib.request.urlopen',side_effect=AssertionError('network forbidden')):
            value=self.save();self.assertTrue(value['profile_saved']);self.assertFalse(value['query_available']);self.assertFalse(value['adapter_supported'])
            self.assertTrue(value['key_configured']);self.assertNotIn('synthetic-test-only',json.dumps(value));self.assertNotIn('synthetic-test-only',self.c.path.read_text())
            with self.assertRaises(AppError) as caught:CustomsService(connection=self.c).analyze({'query':'synthetic','consent':True})
            self.assertEqual(caught.exception.code,'customs_adapter_required')
    def test_restart_keeps_profile_without_restoring_key(self):
        self.save();restart=CustomsConnection(self.store);self.assertTrue(restart.settings()['profile_saved']);self.assertFalse(restart.settings()['key_configured']);self.assertFalse(restart.settings()['automatic_key_restore'])
    def test_unchanged_scope_keeps_session_key_but_changed_endpoint_clears_it(self):
        self.save();self.assertTrue(self.save('')['key_configured']);changed=dict(self.profile,endpoint='https://other.example.invalid/trade');self.assertFalse(self.save('',changed)['key_configured'])
    def test_auth_switch_clears_key(self):
        self.save();self.assertFalse(self.save('',dict(self.profile,auth_type='none'))['key_configured'])
    def test_revision_conflict_preserves_profile(self):
        self.save()
        with self.assertRaises(AppError) as caught:self.c.save({'revision':0,'profile':self.profile})
        self.assertEqual(caught.exception.status,409);self.assertTrue(self.c.settings()['key_configured'])
    def test_invalid_destinations_rejected_without_mutation(self):
        for url in ['http://example.invalid','https://localhost/x','https://127.0.0.1/x','https://10.0.0.1/x','https://user:secret@example.invalid/x','https://example.invalid/x?key=secret','https://example.invalid/#secret']:
            with self.subTest(url=url),self.assertRaises(AppError):self.save(profile=dict(self.profile,endpoint=url))
        self.assertFalse(self.c.path.exists())
    def test_forbidden_headers_and_unknown_auth_rejected(self):
        for header in ['Authorization','Cookie','Host','Proxy-Authorization','bad\nheader']:
            with self.subTest(header=header),self.assertRaises(AppError):self.save(profile=dict(self.profile,header_name=header))
        with self.assertRaises(AppError):self.save(profile=dict(self.profile,auth_type='unknown'))
    def test_failed_write_retains_prior_memory_and_profile(self):
        self.save();prior=self.c.settings()
        with patch('backend.customs_connection.os.replace',side_effect=OSError('synthetic failure')),self.assertRaises(AppError):self.save('synthetic-new-value',dict(self.profile,provider_name='Changed'))
        self.assertEqual(prior,self.c.settings());self.assertEqual(self.c.key,'synthetic-test-only')
    def test_clear_removes_only_current_profile_and_session_key(self):
        self.save();value=self.c.save({'revision':self.c.revision,'action':'clear'});self.assertFalse(value['key_configured']);self.assertFalse(value['profile_saved']);self.assertFalse(self.c.path.exists())
