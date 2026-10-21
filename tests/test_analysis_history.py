"""Isolated persistence, concurrency and bounded safe contents; no real provider."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from test_models import Stub,resolver,SYNTHETIC_KEY,completion,output
import urllib.error
import test_models
from backend.models import ModelService
from backend.store import Store
from backend.common import AppError
from backend.model_analysis import INSTRUCTION

class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'history.db'
        self.store=Store(self.path,False);self.stub=Stub();self.service=ModelService(self.store,'local',self.stub,resolver)
        self.service.save(dict(provider='deepseek',base_url='https://api.deepseek.com',model='synthetic',api_key=SYNTHETIC_KEY))
        self.c=self.store.save_customer(dict(name='FICTIONAL_HISTORY',type='终端加工厂',stage='需求确认',facts='FICTIONAL STEEL 80mm / 20mm vertical CNC, two tentative fixtures'),False)
        self.other=self.store.save_customer(dict(name='OTHER_SYNTHETIC',type='经销商',stage='待开发'),False)
        self.row=self.store.record(dict(customer_id=self.c['id'],kind='沟通',text='SYNTHETIC inquiry, unconfirmed'))
    def tearDown(self):self.tmp.cleanup()
    def data(self,module='deals',rid='request-0001'):
        p=self.service.context(self.c['id'])
        return dict(module=module,request_id=rid,consent=True,revision=p['revision'],context_token=p['context_token'])
    def history(self,module='deals'):return self.store.analyses(self.c['id'],dict(module=module))
    def assert_error(self,code,action):
        with self.assertRaises(AppError) as error:action()
        self.assertEqual(error.exception.code,code)
    def test_two_modules_reopen_complete_allowlisted_business_history(self):
        value=output(facts=[dict(statement='untrusted summary',evidence=[dict(source='record',record_id=self.row['id'],quote=self.row['text'])])])
        response=completion(value);response['choices'][0]['message']['reasoning_content']='HIDDEN_REASONING_NEVER_SAVE';self.stub.result=response
        for module in ('development','deals'):self.service.analyze(self.c['id'],self.data(module))
        reopened=Store(self.path,False)
        for module in ('development','deals'):
            r=reopened.analyses(self.c['id'],dict(module=module))['items'][0]
            self.assertEqual(r['status'],'ok');self.assertFalse(r['historical']);self.assertTrue(r['unsent'])
            self.assertEqual(r['input_scope']['record_ids'],[self.row['id']]);self.assertEqual(r['customer_version'],1)
            self.assertEqual(r['result']['facts'][0]['statement'],'用户记录：'+self.row['text'])
            self.assertEqual(r['input_stage'],'需求确认');self.assertEqual(r['provider'],'deepseek');self.assertTrue(r['completed_at'])
        with reopened.db() as db:serialized=''.join(r[0] for r in db.execute('SELECT data FROM analyses'))
        for forbidden in (SYNTHETIC_KEY,'HIDDEN_REASONING_NEVER_SAVE','Authorization','reasoning_content','OTHER_SYNTHETIC'):self.assertNotIn(forbidden,serialized)
        self.assertEqual(reopened.records(self.c['id'],{})['total'],1);self.assertEqual(reopened.customer(self.c['id']),self.c)
    def test_duplicate_and_simultaneous_request_never_retransmit(self):
        data=self.data();started=threading.Event();release=threading.Event();results=[]
        def blocked():started.set();release.wait(3)
        self.stub.before=blocked
        def run():results.append(self.service.analyze(self.c['id'],data))
        thread=threading.Thread(target=run);thread.start();self.assertTrue(started.wait(2))
        self.assert_error('analysis_pending',lambda:self.service.analyze(self.c['id'],data))
        release.set();thread.join(3);self.assertFalse(thread.is_alive())
        again=self.service.analyze(self.c['id'],data)
        self.assertEqual(again['analysis_id'],results[0]['analysis_id']);self.assertEqual(len(self.stub.calls),1);self.assertEqual(self.history()['total'],1)
    def test_failed_response_stores_only_safe_error_and_replay_no_call(self):
        self.stub.error=AppError('RAW_PROVIDER_AND_SECRET_'+SYNTHETIC_KEY,502,'provider_auth');data=self.data()
        self.assert_error('provider_auth',lambda:self.service.analyze(self.c['id'],data))
        row=self.history()['items'][0];self.assertIsNone(row['result']);self.assertEqual(row['status'],'failed')
        self.assertNotIn(SYNTHETIC_KEY,json.dumps(row));self.assertNotIn('RAW_PROVIDER',json.dumps(row))
        self.assert_error('provider_auth',lambda:self.service.analyze(self.c['id'],data));self.assertEqual(len(self.stub.calls),1)
    def test_changed_input_historical_factory_and_record_changes(self):
        original=self.data();self.service.analyze(self.c['id'],original);self.store.record(dict(customer_id=self.c['id'],kind='反馈',text='NEW SYNTHETIC INPUT'))
        self.assertTrue(self.history()['items'][0]['historical'])
        self.assert_error('request_conflict',lambda:self.service.analyze(self.c['id'],self.data()))
        self.assert_error('context_changed',lambda:self.service.analyze(self.c['id'],original))
        self.assertEqual(len(self.stub.calls),1)
        self.service.analyze(self.c['id'],self.data(rid='request-0002'))
        f=self.store.factory();self.store.save_factory(dict(f,focus='NEW SYNTHETIC FOCUS'))
        self.assertTrue(all(r['historical'] for r in self.history()['items']))
        self.assertEqual(self.store.customer(self.c['id']),self.c)
    def test_late_success_saved_as_history_never_overwrites_current(self):
        def mutate():self.store.save_customer(dict(self.c,blocker='NEW USER BLOCKER'),False)
        self.stub.before=mutate
        self.assert_error('context_changed',lambda:self.service.analyze(self.c['id'],self.data()))
        row=self.history()['items'][0];self.assertEqual(row['status'],'ok');self.assertTrue(row['historical']);self.assertTrue(row['historical_at_completion'])
        self.assertEqual(self.store.customer(self.c['id'])['blocker'],'NEW USER BLOCKER')
    def test_draft_versions_retry_conflict_scope_and_original_immutable(self):
        result=self.service.analyze(self.c['id'],self.data());original=result['draft']
        data=dict(customer_id=self.c['id'],module='deals',analysis_id=result['analysis_id'],draft_version=0,draft='MANUAL UNSENT DRAFT')
        saved=self.store.analysis_draft(data);self.assertEqual(saved['draft_version'],1)
        self.assertEqual(self.store.analysis_draft(data)['draft_version'],1)
        self.assert_error('draft_conflict',lambda:self.store.analysis_draft(dict(data,draft='OTHER TAB EDIT')))
        self.assert_error('not_found',lambda:self.store.analysis_draft(dict(data,customer_id=self.other['id'])))
        self.assert_error('not_found',lambda:self.store.analysis_draft(dict(data,module='development')))
        for text in ('   ','x'*12001):self.assert_error('invalid_input',lambda:self.store.analysis_draft(dict(data,draft_version=1,draft=text)))
        reopened=Store(self.path,False);r=reopened.analyses(self.c['id'],dict(module='deals'))['items'][0]
        self.assertEqual(r['result']['draft'],original);self.assertEqual(r['edited_draft'],'MANUAL UNSENT DRAFT')
        self.assertEqual(reopened.records(self.c['id'],{})['total'],1)
    def test_no_backfill_and_other_customer_and_module_isolation(self):
        self.store.call_log('analyze','ok',dict(provider='deepseek',model='synthetic'),cid=self.c['id'])
        self.assertEqual(self.history()['total'],0)
        self.service.analyze(self.c['id'],self.data())
        self.assertEqual(self.store.analyses(self.other['id'],dict(module='deals'))['total'],0)
        self.assertEqual(self.history('development')['total'],0)
        self.assert_error('invalid_input',lambda:self.service.analyze(self.c['id'],self.data(module='invented')))
    def test_prompt_progressive_first_email_and_scene_specific_interface(self):
        self.service.analyze(self.c['id'],self.data('development'))
        prompt=self.stub.calls[-1]['payload']['messages'][0]['content']
        for required in ('80至160','只确认合适联系人','工作台/夹具安装','不能默认索要主轴','不要求客户完成工程设计','下一轮','不能虚构能力','客户开发'):self.assertIn(required,prompt)

    def test_unfinished_request_survives_restart_without_auto_retry(self):
        self.store.analysis_begin(self.c['id'],self.data(),dict(provider='deepseek',model='synthetic',revision=1),'model')
        restarted=ModelService(Store(self.path,False),'local',self.stub,resolver)
        self.assertEqual(restarted.store.analyses(self.c['id'],dict(module='deals'))['items'][0]['status'],'pending')
        self.assertEqual(self.stub.calls,[]);self.assertFalse(restarted.settings()['configured'])

class HistoryAPITests(unittest.TestCase):
    setUp=test_models.ModelAPITests.setUp
    tearDown=test_models.ModelAPITests.tearDown
    call=test_models.ModelAPITests.call
    def test_mock_module_persistence_and_http_guards(self):
        c=self.store.save_customer(dict(name='SYNTHETIC MOCK',type='终端加工厂',stage='待开发'),False)
        for module in ('development','deals'):
            data=dict(customer_id=c['id'],module=module,request_id='mock-request-001',provider='mock')
            r=self.call('/api/analyze',data);again=self.call('/api/analyze',data)
            self.assertEqual(r['analysis_id'],again['analysis_id']);self.assertEqual(r['mode'],'mock')
            rows=self.call('/api/analyses?customer_id='+c['id']+'&module='+module)
            self.assertEqual(rows['total'],1);self.assertEqual(rows['items'][0]['result']['draft'],r['draft'])
        self.assertEqual(self.stub.calls,[])
        for headers in ({'Origin':'https://evil.example'},{'Sec-Fetch-Site':'cross-site'},{'Host':'evil.example'}):
            with self.assertRaises(urllib.error.HTTPError) as error:self.call('/api/analyses?customer_id='+c['id']+'&module=deals',headers=headers)
            self.assertEqual(error.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError) as error:self.call('/api/analysis/draft',{},headers={'X-CSRF-Token':'incorrect'})
        self.assertEqual(error.exception.code,403)
        self.assertEqual(self.store.customer(c['id']),c);self.assertEqual(self.store.records(c['id'],{})['total'],0)

if __name__=='__main__':unittest.main()
