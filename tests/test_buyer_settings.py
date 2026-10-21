import tempfile,unittest
from pathlib import Path
from backend.store import Store
from backend.buyer_settings import BuyerSettings
from backend.common import AppError

class BuyerSettingsTests(unittest.TestCase):
    def test_save_cancel_contract_restart_revision_and_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'synthetic.sqlite3',False);settings=BuyerSettings(store)
            first=settings.read();self.assertFalse(first['saved']);self.assertEqual(first['provider_requests'],0)
            config={'target':'terminal_processor','process_terms':[' metal finishing services ']}
            saved=settings.save({'revision':first['revision'],'config':config})
            self.assertEqual(saved['config']['process_terms'],['metal finishing services'])
            self.assertEqual(BuyerSettings(store).read(),saved)
            with self.assertRaises(AppError):settings.save({'revision':first['revision'],'config':config})
            for value in (None,{'target':'bad'},{'process_terms':['x']},{'process_terms':['a']*5},{'profile':[]}):
                with self.assertRaises(AppError):settings.save({'revision':saved['revision'],'config':value})
            self.assertEqual(settings.read(),saved)
