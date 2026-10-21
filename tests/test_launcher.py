"""Isolated launch planning and fresh DB tests; no formal server startup."""
import argparse,json,socket,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from backend.launcher_config import settings,initialize_store,preflight
from backend.production_runtime import NoKeyStartupModels,make_enhanced

def arguments(**changes):
    values=dict(config=None,workspace=None,database=None,port=None,ocr_profile=None,
                website_python=None,website_extraction=None,structured_validator=None)
    values.update(changes);return argparse.Namespace(**values)

class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)/'程序 空目录'
        self.root.mkdir();self.default=self.root/'workspace'
    def tearDown(self):self.tmp.cleanup()
    def config(self,**changes):return settings(arguments(**changes),self.root,self.default)
    def test_planning_does_not_create_workspace_or_read_credentials(self):
        c=self.config();self.assertTrue(c['create_database']);self.assertFalse(self.default.exists())
        self.assertEqual(c['database'],self.default/'data'/'overseas.sqlite3')
    def test_fresh_db_has_no_demo_business_and_neutral_factory(self):
        c=self.config();s=initialize_store(c)
        with s.db() as db:
            for table in ('customers','records','calls','analyses','kb_documents','discovery_candidates','website_captures'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0,table)
        self.assertEqual(s.factory()['products'],'');self.assertEqual(s.factory()['name'],'我的工厂（待完善）')
        m=NoKeyStartupModels(s,'local');self.assertFalse(m.settings()['configured']);self.assertFalse(m.credential_vault.directory.exists())
    def test_restart_preserves_records_and_factory(self):
        c=self.config();s=initialize_store(c)
        s.save_factory(dict(name='合成测试工厂',products='合成产品',capabilities='',countries='',focus=''))
        customer=s.save_customer(dict(name='合成测试客户',type='终端加工厂',stage='需求确认'),False)
        later=self.config();self.assertFalse(later['create_database']);r=initialize_store(later)
        self.assertEqual(r.customer(customer['id'])['name'],'合成测试客户');self.assertEqual(r.factory()['name'],'合成测试工厂')
    def test_missing_explicit_db_never_creates(self):
        with self.assertRaises(ValueError):self.config(database='data/missing.sqlite3')
        self.assertFalse((self.root/'data').exists())
    def test_explicit_legacy_database_is_retained(self):
        from backend.production_runtime import ClosedStore
        file=self.root/'data'/'acceptance-synthetic.sqlite3';ClosedStore(file,False)
        c=self.config(database=str(file));self.assertEqual(c['workspace'],self.root);self.assertEqual(c['database'],file)
        self.assertFalse(c['create_database'])
    def test_demo_wrong_extension_and_escape_rejected(self):
        for name in ('demo.sqlite3','other.txt','../outside.sqlite3'):
            with self.assertRaises(ValueError):self.config(workspace=str(self.default),database=str(self.default/name))
    def test_profile_relative_paths_and_cli_precedence(self):
        file=self.root/'launch.json'
        profile=dict(protocol='overseas-launch-v1',workspace='用户资料',port=8871,website_extraction='native',
                     rag=dict(local_python='runtime/python.exe',embedding_dir='models/e5',rerank_dir='models/rerank',local_model_cache='用户资料/cache',local_timeout_seconds=60))
        file.write_text(json.dumps(profile),encoding='utf8');c=self.config(config=str(file),port=8872)
        self.assertEqual(c['port'],8872);self.assertEqual(c['workspace'],self.root/'用户资料')
        self.assertEqual(c['rag']['embedding_dir'],str(self.root/'models/e5'))
    def test_unknown_or_secret_settings_and_bad_port_rejected(self):
        file=self.root/'launch.json';file.write_text(json.dumps(dict(protocol='overseas-launch-v1',api_key='synthetic')),encoding='utf8')
        with self.assertRaises(ValueError):self.config(config=str(file))
        for port in (True,0,8765,65536):
            with self.assertRaises(ValueError):self.config(port=port)
    def test_models_missing_uses_keyword_fallback_without_spawn(self):
        c=self.config();s=initialize_store(c)
        with patch('subprocess.run',side_effect=AssertionError('No model process')):
            e=make_enhanced(s,c['workspace'],c['rag']);r=e.retrieve('夹具',6)
        self.assertEqual(r['method'],'local_keyword_bm25_fallback');self.assertEqual(e.last_fallback,'local_models_missing')
    def test_wrong_enhanced_workspace_rejected(self):
        c=self.config();s=initialize_store(c)
        with self.assertRaises(ValueError):make_enhanced(s,self.root/'other',c['rag'])
    def test_main_assembles_default_services_without_real_listener_or_provider(self):
        import run_production,server
        from unittest.mock import Mock
        fake=Mock(server_port=8871);folder=self.root/'assembled'
        with patch('backend.launcher_config.preflight'),patch.object(run_production,'make_server',return_value=fake) as build,patch.object(server,'STATIC_ROOT',server.STATIC_ROOT),patch('builtins.print'):
            run_production.main(['--workspace',str(folder),'--port','8871'])
        store=build.call_args.args[0];kwargs=build.call_args.kwargs
        self.assertEqual(store.path,folder/'data'/'overseas.sqlite3');self.assertFalse(kwargs['model_service'].key)
        self.assertIsNotNone(kwargs['enhanced_service']);self.assertFalse(kwargs['enhanced_service'].api.enabled)
        fake.serve_forever.assert_called_once();fake.server_close.assert_called_once()
        receipt=json.loads((folder/'evidence'/'PRODUCTION_SERVER.json').read_text(encoding='utf8'))
        self.assertTrue(receipt['first_database_created']);self.assertFalse(receipt['ocr']['native_runner_configured'])
    def test_packaged_default_browser_and_explicit_suppression(self):
        import run_production,server,sys
        from unittest.mock import Mock
        for suppress in (False,True):
            folder=self.root/('no-browser' if suppress else 'open-browser');fake=Mock(server_port=8871)
            with patch.object(sys,'frozen',True,create=True),patch('backend.launcher_config.preflight'),patch.object(run_production,'make_server',return_value=fake),patch.object(server,'STATIC_ROOT',server.STATIC_ROOT),patch('builtins.print'),patch('backend.desktop_lifecycle.DesktopInstance.open_browser',return_value=True) as browser:
                run_production.main(['--workspace',str(folder),'--port','8871']+(['--no-browser'] if suppress else []))
            if suppress:browser.assert_not_called()
            else:browser.assert_called_once_with()
    def resources(self):
        folder=self.root/'frontend';folder.mkdir()
        for name in ('index.html','research.html','deals.html','knowledge.html','settings.js'):(folder/name).write_text('fixture')
        return folder
    def test_preflight_missing_assets_or_dependency_does_not_initialize(self):
        c=self.config()
        with self.assertRaises(ValueError):preflight(c,self.root/'missing')
        with patch('importlib.util.find_spec',return_value=None):
            with self.assertRaises(ValueError):preflight(c,self.resources())
        self.assertFalse(self.default.exists())
    def test_occupied_random_port_fails_before_db_initialization(self):
        folder=self.resources()
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1',0));occupied.listen()
            c=self.config(port=occupied.getsockname()[1])
            with patch('importlib.util.find_spec',return_value=object()):
                with self.assertRaises(ValueError):preflight(c,folder)
        self.assertFalse(self.default.exists())

if __name__=='__main__':unittest.main()
