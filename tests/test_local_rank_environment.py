import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from backend.enhanced_preview import local_rank_environment

class LocalRankEnvironmentTests(unittest.TestCase):
    def test_host_secrets_identity_and_caches_are_not_inherited(self):
        host={key:os.environ[key] for key in ('SystemRoot','WINDIR','PATH') if key in os.environ}
        host.update(OPENAI_API_KEY='synthetic-do-not-copy',HF_TOKEN='synthetic-do-not-copy',USERNAME='host-user',TEMP='host-temp',TORCH_HOME='host-torch',PYTHONPATH='host-python',APPDATA='host-appdata')
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,host,clear=True):
            cache=Path(folder)/'专用 缓存'
            env=local_rank_environment(cache)
            for key in ('OPENAI_API_KEY','HF_TOKEN','PYTHONPATH','APPDATA'):
                self.assertNotIn(key,env)
            self.assertEqual(env['USERNAME'],'overseas_local_rank')
            for key in ('TEMP','TMP','USERPROFILE','TORCH_HOME','TORCHINDUCTOR_CACHE_DIR','HF_HOME','HF_HUB_CACHE','TRANSFORMERS_CACHE','RAG_LOCAL_CACHE'):
                self.assertTrue(Path(env[key]).is_relative_to(cache.resolve()),key)
                self.assertTrue(Path(env[key]).is_dir(),key)
            self.assertEqual(env['HF_HUB_DISABLE_IMPLICIT_TOKEN'],'1')
            self.assertEqual(env['HF_HUB_OFFLINE'],'1')

    def test_real_minimal_child_has_identity_and_dedicated_temp(self):
        with tempfile.TemporaryDirectory() as folder:
            cache=Path(folder)/'isolated-cache'
            env=local_rank_environment(cache)
            source="import getpass,json,os,tempfile; print(json.dumps([getpass.getuser(),tempfile.gettempdir(),os.environ['TORCHINDUCTOR_CACHE_DIR']]))"
            result=subprocess.run([sys.executable,'-I','-c',source],env=env,capture_output=True,text=True,check=True,timeout=15)
            identity,temp,inductor=json.loads(result.stdout)
            self.assertEqual(identity,'overseas_local_rank')
            self.assertEqual(Path(temp),cache/'tmp')
            self.assertEqual(Path(inductor),cache/'inductor')

if __name__=='__main__':unittest.main()
