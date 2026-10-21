"""Synthetic secrets only. No provider, existing vault, or business database access."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
import server
from backend.common import AppError
from backend.credential_vault import CredentialVault, WindowsDPAPI, model_scope
from backend.models import ModelService
from backend.production_runtime import NoKeyStartupModels

KEY = 'synthetic-fixture-credential-only'


class SyntheticProtector:
    """Reversible fixture, deliberately NOT production encryption."""
    available = True
    def __init__(self):
        self.loads = 0
    def transform(self, data, entropy, decrypt=False):
        tag = hashlib.sha256(entropy).digest()
        if decrypt:
            self.loads += 1
            if not data.startswith(tag):
                raise OSError('synthetic secret not to echo: '+KEY)
            return data[len(tag):][::-1]
        return tag + data[::-1]


def resolver(*args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.crypto = SyntheticProtector()
        self.vault = CredentialVault(self.root/'credentials', 'fixture-db', self.crypto)
        self.store = server.Store(self.root/'synthetic.sqlite3', False)
        self.calls = []
        self.service = ModelService(self.store, 'local', lambda *args: self.calls.append(args), resolver, self.vault)
    def tearDown(self):
        self.tmp.cleanup()
    def body(self, **kwargs):
        return dict(provider='deepseek', base_url='https://api.deepseek.com', model='synthetic-model', **kwargs)
    def save(self, **kwargs):
        return self.service.save(self.body(**kwargs))
    def error(self, code, action):
        with self.assertRaises(AppError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn(KEY, str(caught.exception))
    def test_default_memory_only_does_not_create_vault(self):
        result = self.save(api_key=KEY)
        self.assertTrue(result['configured'])
        self.assertFalse(self.vault.directory.exists())
        self.assertFalse(result['credential_saved_encrypted'])
        self.assertEqual(self.calls, [])
    def test_explicit_save_never_exposes_secret_in_settings_or_sqlite(self):
        result = self.save(api_key=KEY, persist=True)
        self.assertTrue(result['credential_saved_encrypted'])
        self.assertFalse(result['automatic_key_restore'])
        self.assertNotIn(KEY, json.dumps(result))
        self.assertNotIn(KEY.encode(), self.vault.path(model_scope(self.service.profile)).read_bytes())
        with self.store.db() as db:
            self.assertNotIn(KEY, db.execute('SELECT data FROM model_config').fetchone()[0])
        self.assertEqual(self.crypto.loads, 0)
        self.assertEqual(self.calls, [])
    def test_restart_and_get_do_not_decrypt(self):
        self.save(api_key=KEY, persist=True)
        reopened = ModelService(self.store, 'local', resolver=resolver, credential_vault=self.vault)
        for _ in range(3):
            result = reopened.settings()
            self.assertFalse(result['configured'])
            self.assertTrue(result['credential_saved_encrypted'])
        self.assertEqual(self.crypto.loads, 0)
    def test_explicit_restore_into_memory_has_no_provider_request(self):
        self.save(api_key=KEY, persist=True)
        self.service = ModelService(self.store, 'local', lambda *args:self.calls.append(args), resolver, self.vault)
        result = self.save(restore_key=True, consent=True, revision=1)
        self.assertTrue(result['configured'])
        self.assertNotIn(KEY, json.dumps(result))
        self.assertEqual(self.crypto.loads, 1)
        self.assertEqual(self.calls, [])
    def test_restore_requires_separate_explicit_confirmation(self):
        self.save(api_key=KEY, persist=True)
        for extra in [{}, {'api_key':KEY}, {'persist':True}, {'clear_key':True}]:
            self.error('consent_required', lambda extra=extra:self.save(restore_key=True, **extra))
        self.assertEqual(self.crypto.loads, 0)
    def test_cas_failure_never_decrypts(self):
        self.save(api_key=KEY, persist=True)
        self.error('profile_changed', lambda:self.save(restore_key=True, consent=True, revision=0))
        self.assertEqual(self.crypto.loads, 0)
    def test_different_endpoint_cannot_reuse_saved_key(self):
        self.save(api_key=KEY, persist=True)
        result = self.service.save({'provider':'custom','base_url':'https://other.example/v1','model':'synthetic'})
        self.assertFalse(result['configured'])
        self.assertFalse(result['credential_saved_encrypted'])
        self.assertEqual(self.crypto.loads, 0)
    def test_restore_cannot_switch_endpoint_in_same_request(self):
        self.save(api_key=KEY, persist=True)
        self.error('profile_changed', lambda:self.service.save({'provider':'custom','base_url':'https://other.example/v1','model':'synthetic','restore_key':True,'consent':True}))
        self.assertEqual(self.crypto.loads, 0)
    def test_model_change_same_credential_scope_keeps_encrypted_key(self):
        self.save(api_key=KEY, persist=True)
        data=self.body();data['model']='next-synthetic'
        self.assertTrue(self.service.save(data)['credential_saved_encrypted'])
    def test_clear_removes_memory_and_only_current_scope_encrypted_copy(self):
        self.save(api_key=KEY, persist=True)
        self.vault.save('unrelated-synthetic-scope', KEY)
        result=self.save(clear_key=True)
        self.assertFalse(result['configured'])
        self.assertFalse(result['credential_saved_encrypted'])
        self.assertTrue(self.vault.contains('unrelated-synthetic-scope'))
        self.error('credential_restore_failed', lambda:self.save(restore_key=True,consent=True))
    def test_wrong_scope_or_namespace_ciphertext_fails_closed(self):
        self.vault.save('A',KEY)
        shutil.copyfile(self.vault.path('A'),self.vault.path('B'))
        self.error('credential_restore_failed',lambda:self.vault.load('B'))
        other=CredentialVault(self.root/'other','different-db',self.crypto)
        other.directory.mkdir();shutil.copyfile(self.vault.path('A'),other.path('A'))
        self.error('credential_restore_failed',lambda:other.load('A'))
    def test_corrupt_oversize_and_invalid_plaintext_do_not_echo(self):
        self.vault.directory.mkdir()
        for raw in [b'bad',b'x'*65537,self.vault.MAGIC+b'bad']:
            self.vault.path('A').write_bytes(raw)
            self.error('credential_restore_failed',lambda:self.vault.load('A'))
        for invalid in ['', 'has space', 'line\nbreak', 'x'*4097, '\u4e2d']:
            self.error('invalid_input',lambda invalid=invalid:self.vault.save('A',invalid))
    def test_failed_encrypt_or_atomic_replace_retains_existing_ciphertext(self):
        self.vault.save('A',KEY);before=self.vault.path('A').read_bytes()
        from unittest.mock import patch
        with patch('backend.credential_vault.os.replace',side_effect=OSError(KEY)):
            self.error('credential_storage_failed',lambda:self.vault.save('A','different-fixture-secret'))
        self.assertEqual(self.vault.path('A').read_bytes(),before)
        self.assertEqual(list(self.vault.directory.glob('.pending-*')),[])
    def test_platform_unavailable_does_not_write_plaintext(self):
        self.crypto.available=False
        self.assertFalse(self.service.settings()['credential_persistence_available'])
        self.error('key_persistence_unavailable',lambda:self.save(api_key=KEY,persist=True))
        self.assertFalse(self.vault.directory.exists())
    def test_no_key_persistence_without_explicit_available_vault(self):
        plain=ModelService(self.store,'local',resolver=resolver)
        self.error('key_persistence_unavailable',lambda:plain.save(self.body(api_key=KEY,persist=True)))
    def test_production_startup_does_not_load_existing_ciphertext(self):
        from unittest.mock import patch
        with patch('backend.credential_vault.CredentialVault', return_value=self.vault):
            production=NoKeyStartupModels(self.store,'local')
        self.assertFalse(production.settings()['configured'])
        self.assertEqual(self.crypto.loads,0)
    @unittest.skipUnless(os.name=='nt','Windows DPAPI only')
    def test_actual_windows_dpapi_roundtrip_with_synthetic_secret_only(self):
        native=CredentialVault(self.root/'native-fixture','synthetic-only')
        native.save('A',KEY)
        self.assertNotIn(KEY.encode(),native.path('A').read_bytes())
        self.assertEqual(native.load('A'),KEY)
        shutil.copyfile(native.path('A'),native.path('B'))
        self.error('credential_restore_failed',lambda:native.load('B'))
        native.delete('A');self.assertFalse(native.contains('A'))
    def test_http_save_restart_restore_clear_never_returns_key(self):
        def exercise(service, steps):
            http=server.make_server(self.store,'local',False,0,model_service=service)
            thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
            try:
                base='http://127.0.0.1:'+str(http.server_port)
                with urlopen(base+'/api/model/settings') as response:
                    first=json.load(response)
                self.assertNotIn(KEY,json.dumps(first))
                with urlopen(base+'/api/state') as response:
                    token=json.load(response)['csrf']
                for body in steps:
                    request=Request(base+'/api/model/settings',data=json.dumps(body).encode(),headers={'Content-Type':'application/json','X-CSRF-Token':token},method='POST')
                    with urlopen(request) as response:
                        result=json.load(response)
                    self.assertNotIn(KEY,json.dumps(result))
                return result
            finally:
                http.shutdown();http.server_close();thread.join()
        result=exercise(self.service,[self.body(api_key=KEY,persist=True)])
        self.assertTrue(result['credential_saved_encrypted'])
        reopened=ModelService(self.store,'local',lambda *args:self.calls.append(args),resolver,self.vault)
        self.assertFalse(reopened.settings()['configured'])
        result=exercise(reopened,[self.body(restore_key=True,consent=True,revision=1),self.body(clear_key=True,revision=2)])
        self.assertFalse(result['configured']);self.assertFalse(result['credential_saved_encrypted'])
        self.assertEqual(self.calls,[])


if __name__=='__main__':
    unittest.main()
