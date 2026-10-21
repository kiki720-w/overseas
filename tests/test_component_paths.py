import json,tempfile,unittest
from pathlib import Path
from backend.component_paths import ocr_profile,inspect_ocr
from backend.production_runtime import ClosedStore
from run_production import configured_ocr

class ComponentPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.workspace=self.root/'合成 工作区';self.profile=self.root/'profile.json'
        self.value={'protocol':'local-ocr-production-v2','component_root':'./组件','runtime_root':'runtime','python':'python.exe','models_root':'models','staging':'tasks/ocr'}
    def tearDown(self):self.tmp.cleanup()
    def paths(self,**values):
        self.profile.write_text(json.dumps(dict(self.value,**values)),encoding='utf8');return ocr_profile(self.profile,self.workspace)
    def test_non_d_relative_roots_and_workspace_tasks(self):
        p=self.paths();self.assertEqual(p['python'],self.root/'组件'/'runtime'/'python.exe');self.assertEqual(p['staging'],self.workspace/'tasks'/'ocr')
    def test_root_escapes_and_bidirectional_overlap_rejected(self):
        for values in ({'runtime_root':'../escape'},{'models_root':'runtime/models'},{'runtime_root':'models/runtime'},{'python':'../escape.exe'},{'staging':'../escape'}):
            with self.assertRaises(ValueError):self.paths(**values)
    def test_tasks_cannot_contain_or_be_inside_component_roots(self):
        self.value['component_root']=str(self.workspace/'组件')
        for staging in ('组件','组件/runtime/tasks','组件/models/jobs'):
            with self.assertRaises(ValueError):self.paths(staging=staging)
    def test_missing_files_inspection_does_not_create_or_infer(self):
        p=self.paths();state=inspect_ocr(p);self.assertFalse(state['files_present']);self.assertFalse(state['model_inference_verified']);self.assertFalse((self.root/'组件').exists())
    def test_old_profile_requires_explicit_migration(self):
        with self.assertRaises(ValueError):self.paths(protocol='local-ocr-production-v1')
    def test_missing_optional_component_keeps_disabled_facade(self):
        self.paths();store=ClosedStore(self.workspace/'data'/'synthetic.sqlite3',False)
        service,receipt=configured_ocr(store,self.profile,self.workspace)
        self.assertIsNone(service);self.assertEqual(receipt['status'],'component_missing');self.assertFalse(receipt['native_runner_configured'])
