"""Synthetic-only flow assertions; every provider request uses injected transport."""
import json
from pathlib import Path
import tempfile
import unittest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_models import Stub, resolver, SYNTHETIC_KEY, completion, output
from backend.models import ModelService
from backend.model_analysis import build_context, CUSTOMER_FIELDS, FACTORY_FIELDS, RECORD_FIELDS
from backend.common import AppError, dump
from backend.store import Store

class AnalysisFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'fictional.db'
        self.store=Store(self.path,False);self.stub=Stub()
        self.service=ModelService(self.store,'local',self.stub,resolver)
        self.service.save(dict(provider='deepseek',base_url='https://api.deepseek.com',model='deepseek-flash',api_key=SYNTHETIC_KEY))
        self.customer=self.store.save_customer(dict(name='FICTIONAL SELECTED',type='终端加工厂',stage='需求确认',facts='FICTIONAL GEAR FIXTURE INQUIRY'),False)
        other=self.store.save_customer(dict(name='OTHER_NOT_AUTHORIZED',type='经销商',stage='待开发',facts='OTHER_PRIVATE_DATA'),False)
        self.store.record(dict(customer_id=other['id'],kind='沟通',text='OTHER_PRIVATE_RECORD'))
        self.rows=[self.store.record(dict(customer_id=self.customer['id'],kind='沟通',text='FICTIONAL RECORD '+str(i))) for i in range(35)]
        with self.store.db() as db:
            c=dict(self.customer,private_internal_field='PRIVATE_FIELD_NOT_ALLOWED')
            db.execute('UPDATE customers SET data=? WHERE id=?',(dump(c),c['id']))
            f=self.store.factory();f.update(name='FICTIONAL FACTORY',private_internal_field='PRIVATE_FACTORY_FIELD')
            db.execute('UPDATE config SET data=? WHERE id=1',(dump(f),))
    def tearDown(self):self.tmp.cleanup()
    def request(self):
        p=self.service.context(self.customer['id'])
        return dict(consent=True,revision=p['revision'],context_token=p['context_token'])
    def test_bounded_payload_field_isolation_and_verified_evidence(self):
        customer,context,total,size,token=build_context(self.store,self.customer['id'])
        self.assertEqual(total,35);self.assertEqual(len(context['records']),30)
        self.assertEqual({r['id'] for r in context['records']},{r['id'] for r in self.rows[-30:]})
        self.assertEqual(set(context['customer']),set(CUSTOMER_FIELDS));self.assertEqual(set(context['factory']),set(FACTORY_FIELDS))
        for row in context['records']:self.assertEqual(set(row),set(RECORD_FIELDS))
        evidence=dict(source='record',record_id=self.rows[-1]['id'],quote=self.rows[-1]['text'])
        self.stub.result=completion(output(facts=[dict(statement='UNTRUSTED summary must be replaced',evidence=[evidence])]))
        result=self.service.analyze(self.customer['id'],self.request())
        sent=self.stub.calls[-1]['payload']['messages'][1]['content']
        for forbidden in ('OTHER_PRIVATE_DATA','OTHER_PRIVATE_RECORD','OTHER_NOT_AUTHORIZED','PRIVATE_FIELD_NOT_ALLOWED','PRIVATE_FACTORY_FIELD'):
            self.assertNotIn(forbidden,sent)
        self.assertEqual(result['facts'][0]['statement'],'用户记录：'+self.rows[-1]['text'])
        self.assertEqual(result['facts'][0]['evidence'][0],evidence)
        self.assertEqual(result['mode'],'model')
    def test_repeated_analysis_has_no_stage_or_record_side_effects_and_store_reopens(self):
        before=self.store.customer(self.customer['id']);ids={r['id'] for r in self.rows}
        self.stub.result=completion(output(stage_blocker='合成建议：需求确认；不自动变更阶段'))
        for _ in range(2):self.service.analyze(self.customer['id'],self.request())
        self.assertEqual(self.store.customer(self.customer['id']),before)
        self.assertEqual({r['id'] for r in self.store.records(self.customer['id'],{'page_size':'100'})['items']},ids)
        self.assertEqual(self.store.calls({})['total'],2)
        edited='【合成草稿 · 未发送】\nSubject: FICTIONAL TEST\nEdited by user.'
        saved=self.store.record(dict(customer_id=self.customer['id'],kind='跟进',text=edited))
        reopened=Store(self.path,False)
        self.assertEqual(reopened.customer(self.customer['id'])['stage'],'需求确认')
        self.assertEqual(reopened.records(self.customer['id'],{'page_size':'100'})['total'],36)
        self.assertEqual(reopened.records(self.customer['id'],{'page_size':'100'})['items'][0],saved)
    def test_changed_record_requires_repreview_not_implicit_send(self):
        stale=self.request();self.store.record(dict(customer_id=self.customer['id'],kind='反馈',text='FICTIONAL UPDATED REQUIREMENT'))
        with self.assertRaises(AppError) as error:self.service.analyze(self.customer['id'],stale)
        self.assertEqual(error.exception.code,'context_changed');self.assertEqual(self.stub.calls,[])
        self.service.analyze(self.customer['id'],self.request());self.assertEqual(len(self.stub.calls),1)

if __name__=='__main__':unittest.main()
