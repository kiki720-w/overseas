import base64,json,subprocess,sys,unittest
from pathlib import Path
from unittest.mock import patch
from backend.worker_entry import command,dispatch

class WorkerEntryTests(unittest.TestCase):
    def test_source_keeps_isolated_python_worker(self):
        with patch.object(sys,'frozen',False,create=True):
            args=command('knowledge-extract')
        self.assertEqual(args[:2],[sys.executable,'-I'])
        self.assertEqual(Path(args[2]).name,'knowledge_extract.py')

    def test_frozen_uses_only_named_worker_protocol(self):
        with patch.object(sys,'frozen',True,create=True):
            self.assertEqual(command('knowledge-extract'),[sys.executable,'--internal-worker','knowledge-extract'])
            self.assertEqual(command('website-fetch'),[sys.executable,'--internal-worker','website-fetch'])
            with self.assertRaises(ValueError):command('../arbitrary.py')

    def test_invalid_dispatch_cannot_run_a_script(self):
        with patch('backend.worker_entry.runpy.run_path') as run:
            for arguments in ([],['../arbitrary.py'],['knowledge-extract','extra']):
                with self.assertRaises(SystemExit):dispatch(arguments)
            run.assert_not_called()

    def test_source_worker_parses_synthetic_stdin(self):
        request={'extension':'.txt','content_base64':base64.b64encode(b'Synthetic ZX100 warranty is twelve months.').decode()}
        with patch.object(sys,'frozen',False,create=True):args=command('knowledge-extract')
        result=subprocess.run(args,input=json.dumps(request).encode(),capture_output=True,timeout=15,check=True)
        value=json.loads(result.stdout)
        self.assertTrue(value['ok'])
        self.assertIn('twelve months',value['value']['extracted_text'])

if __name__=='__main__':unittest.main()
