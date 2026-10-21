import copy,json,os,sqlite3,tempfile,threading,unittest,urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock,patch
from backend.common import AppError
from backend.production_runtime import ClosedStore
from backend.discovery import DiscoveryService
from backend.deepseek_tools import DeepSeekTools
from test_deepseek_tools import row

def page(url='https://alpha.example/about',name='Alpha Gears GmbH'):
    v=row(url);v.update(title=name,snippet='We provide gear hobbing services.',summary='# '+name+'\nWe manufacture gears.\nWe provide gear hobbing services.');return v

class SearchLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'synthetic.sqlite3';self.store=ClosedStore(self.path,False)
        self.mock=Mock(return_value=(200,{'results':[page()]}));self.service=self.make(self.mock)
    def tearDown(self):self.temp.cleanup()
    def make(self,transport):
        provider=DeepSeekTools(transport,key='synthetic-no-real-credential',cooldown=0)
        return DiscoveryService(self.store,None,'local',provider)
    def plan(self,service=None,**changes):
        data={'query':'gear machining','countries':'Germany','max_queries':3,'buyer_intent':{'target':'as_entered'}};data.update(changes)
        return (service or self.service).plans.create(data)
    def next(self,p,rid='synthetic_click_01',service=None,**data):
        return (service or self.service).plans.next(dict(plan_id=p['plan_id'],request_id=rid,consent=True,**data))
    def test_repeat_create_and_replayed_click_survive_reconstruction(self):
        p=self.plan();first=self.next(p);other=self.make(Mock())
        self.assertEqual(self.plan(other)['plan_id'],p['plan_id']);again=self.next(p,service=other)
        self.assertTrue(again['replayed']);self.assertEqual(again['batch'],first['batch']);self.assertEqual(other.plans.get({})['budget']['provider_requests_attempted'],1)
    def test_reopen_restores_sources_and_continues_different_query(self):
        p=self.plan();self.next(p);other=self.make(self.mock);loaded=other.plans.get({})
        self.assertEqual(loaded['next_query_index'],1);self.assertEqual(len(other.jobs),1)
        self.next(p,'synthetic_click_02',other)
        payloads=[c.args[1] for c in self.mock.call_args_list];self.assertEqual(len(payloads),2);self.assertNotEqual(payloads[0]['query'],payloads[1]['query']);self.assertTrue(all(p['count']==20 and 'offset' not in p for p in payloads))
    def test_budget_and_country_changes_keep_history_separate(self):
        p=self.plan();self.next(p);self.service.plans.stop({'plan_id':p['plan_id']})
        reduced=self.plan(max_queries=1);self.assertNotEqual(reduced['plan_id'],p['plan_id']);self.assertEqual(reduced['budget']['max_provider_requests'],1)
        self.assertNotEqual(reduced['queries'][0]['executed_query'],p['queries'][0]['executed_query'])
        self.service.plans.stop({'plan_id':reduced['plan_id']});china=self.plan(countries='China')
        self.assertTrue(all('China' in q['executed_query'] and 'Germany' not in q['executed_query'] for q in china['queries']))
    def test_same_company_new_source_and_changed_evidence_are_preserved(self):
        p=self.plan();a=self.next(p)
        self.mock.return_value=(200,{'results':[page('https://alpha.example/services')]});b=self.next(p,'synthetic_click_02')
        self.assertEqual(a['batch']['new_company_count'],1);self.assertEqual(b['batch']['new_company_count'],0);self.assertEqual(b['batch']['new_source_page_count'],1)
        changed=page();changed['summary']+='\nWe offer CNC machining services.';self.mock.return_value=(200,{'results':[changed]});c=self.next(p,'synthetic_click_03')
        self.assertEqual(c['batch']['new_source_page_count'],0);self.assertEqual(c['batch']['new_evidence_count'],1)
    def test_identical_evidence_not_new_page_but_is_still_in_history(self):
        p=self.plan();self.next(p);r=self.next(p,'synthetic_click_02')
        self.assertEqual(r['batch']['new_evidence_count'],0);self.assertEqual(r['batch']['cross_batch_duplicate_count'],1);self.assertEqual(len(self.service.plans.get({})['batches']),2)
    def test_directory_and_pdf_never_new_entities(self):
        self.mock.return_value=(200,{'results':[page('https://directory.example/companies/gears'),page('https://alpha.example/brochure.pdf')]});r=self.next(self.plan())
        self.assertEqual(r['batch']['new_company_count'],0);self.assertEqual(r['batch']['entity_summary']['reference_source_page_count'],2)
    def test_two_instances_cannot_send_same_plan_concurrently(self):
        entered=threading.Event();release=threading.Event()
        def transport(*args):entered.set();release.wait(3);return 200,{'results':[page()]}
        self.mock.side_effect=transport;p=self.plan();other=self.make(Mock())
        with ThreadPoolExecutor(1) as pool:
            call=pool.submit(self.next,p);self.assertTrue(entered.wait(1))
            with self.assertRaises(AppError) as caught:self.next(p,'other_click_01',other)
            self.assertEqual(caught.exception.code,'search_plan_busy');release.set();call.result()
        self.assertEqual(self.mock.call_count,1);self.assertEqual(other.plans.get({})['budget']['provider_requests_attempted'],1)
    def test_failed_request_is_persisted_and_not_resent_on_continue(self):
        self.mock.side_effect=urllib.error.URLError('synthetic offline');p=self.plan()
        with self.assertRaises(AppError):self.next(p)
        failed=self.service.plans.get({});self.assertEqual(failed['ledger'][0]['state'],'failed');self.assertEqual(failed['last_outcome'],'request_failed');self.assertEqual(len(failed['batches']),0)
        first_query=self.mock.call_args.args[1]['query'];self.mock.side_effect=None
        self.next(p,'synthetic_click_02',resume=True);self.assertNotEqual(self.mock.call_args.args[1]['query'],first_query)
    def test_interrupted_sent_request_is_unknown_not_retried(self):
        p=self.plan();plans=self.service.plans;rid='interrupted_click';plans.ledger.acquire(p['plan_id'],rid);raw=plans.load(p['plan_id']);raw['pending']=rid;plans.ledger.attempt(raw,0,rid)
        with self.store.db() as db:db.execute('UPDATE search_plan_ledger SET owner=?,pid=? WHERE id=?',('orphan-fixture',os.getpid(),p['plan_id']))
        other=self.make(self.mock);r=other.plans.get({});self.assertEqual(r['last_outcome'],'outcome_unknown');self.assertEqual(r['ledger'][0]['state'],'outcome_unknown');self.assertEqual(r['budget']['provider_requests_attempted'],1);self.assertEqual(r['next_query_index'],1)
        self.next(p,'synthetic_click_02',other,resume=True);self.assertEqual(self.mock.call_count,1);self.assertNotEqual(self.mock.call_args.args[1]['query'],p['queries'][0]['executed_query'])
    def test_interrupted_before_send_does_not_charge_attempt(self):
        p=self.plan();self.service.plans.ledger.acquire(p['plan_id'],'before_send_click')
        with self.store.db() as db:db.execute('UPDATE search_plan_ledger SET owner=?,pid=? WHERE id=?',('orphan-fixture',os.getpid(),p['plan_id']))
        r=self.make(Mock()).plans.get({});self.assertEqual(r['last_outcome'],'interrupted_before_send');self.assertEqual(r['budget']['provider_requests_attempted'],0);self.assertEqual(r['next_query_index'],0)
    def test_migration_preserves_legacy_business_and_does_not_save_key(self):
        before=self.store.factory();self.plan();self.assertEqual(self.store.factory(),before)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM customers').fetchone()[0],0)
            text=' '.join(r[0] for r in db.execute('SELECT data FROM search_plan_ledger'))
            self.assertNotIn('synthetic-no-real-credential',text)
            self.assertEqual({r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'search_%_ledger'")},{'search_plan_ledger','search_query_ledger','search_source_ledger'})
    def test_restart_does_not_restore_key_or_spend_before_manual_entry(self):
        p=self.plan();self.next(p);provider=DeepSeekTools(Mock(),key='',cooldown=0);other=DiscoveryService(self.store,None,'local',provider)
        restored=other.plans.get({});self.assertEqual(restored['budget']['provider_requests_attempted'],1)
        with self.assertRaises(AppError) as caught:self.next(p,'synthetic_click_02',other)
        self.assertEqual(caught.exception.code,'search_key_required');self.assertEqual(other.plans.get({})['budget']['provider_requests_attempted'],1);provider.transport.assert_not_called()
    def test_sqlite_reservation_failure_never_calls_provider_or_charges(self):
        p=self.plan()
        with self.store.db() as db:db.execute("CREATE TRIGGER reject_fixture_attempt BEFORE UPDATE ON search_plan_ledger WHEN json_extract(NEW.data,'$.attempts')>json_extract(OLD.data,'$.attempts') BEGIN SELECT RAISE(ABORT,'synthetic reservation fault'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.next(p)
        self.mock.assert_not_called()
        with self.store.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM search_query_ledger').fetchone()[0],0)
        self.assertEqual(self.service.plans.get({})['budget']['provider_requests_attempted'],0)
    def test_crash_after_response_receipt_retains_sources_but_not_false_success(self):
        p=self.plan()
        with patch.object(self.service.plans.ledger,'save',side_effect=SystemExit('synthetic process interruption')):
            with self.assertRaises(SystemExit):self.next(p)
        with self.store.db() as db:db.execute('UPDATE search_plan_ledger SET owner=?,pid=? WHERE id=?',('orphan-fixture',os.getpid(),p['plan_id']))
        other=self.make(Mock());restored=other.plans.get({})
        self.assertEqual(restored['last_outcome'],'outcome_unknown');self.assertEqual(restored['ledger'][0]['state'],'outcome_unknown');self.assertEqual(len(restored['batches']),0);self.assertEqual(len(other.jobs),1)
    def test_nonempty_legacy_database_migration_keeps_customer_and_factory(self):
        from backend.common import TYPES,STAGES
        legacy=ClosedStore(Path(self.temp.name)/'legacy.sqlite3',False)
        customer=legacy.save_customer({'name':'Synthetic Legacy Ltd','type':TYPES[0],'stage':STAGES[0],'facts':'retained synthetic facts'},False);factory=legacy.factory()
        DiscoveryService(legacy,None,'local',DeepSeekTools(Mock(),key='synthetic'))
        self.assertEqual(legacy.customer(customer['id']),customer);self.assertEqual(legacy.factory(),factory)

if __name__=='__main__':unittest.main()
