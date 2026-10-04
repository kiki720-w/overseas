"""Single-call evidence protocol with synthetic inputs and injected provider only."""
import json
import tempfile
import unittest
from pathlib import Path
from test_models import Stub,resolver,SYNTHETIC_KEY,completion,output
from backend.store import Store
from backend.models import ModelService
from backend.common import AppError,dump
from backend.model_analysis import build_context,evidence_catalog,validate_analysis

class EvidenceProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'synthetic.db'
        self.store=Store(self.path,False);self.stub=Stub();self.service=ModelService(self.store,'local',self.stub,resolver)
        self.service.save(dict(provider='deepseek',base_url='https://api.deepseek.com',model='synthetic',api_key=SYNTHETIC_KEY))
        self.c=self.store.save_customer(dict(name='FICTIONAL REFERENCE TEST',type='终端加工厂',stage='需求确认',facts='SYNTHETIC CNC request',hypotheses='HYPOTHESIS_NOT_EVIDENCE',unknowns='UNKNOWN_NOT_EVIDENCE'),False)
        self.original='FICTIONAL ONLY. Steel  gear “80mm” / 20mm.\r\nTwo tentative fixtures; no order confirmed.'
        self.record=self.store.record(dict(customer_id=self.c['id'],kind='沟通',text=self.original))
        self.other=self.store.save_customer(dict(name='OTHER_CUSTOMER_NOT_AUTHORIZED',type='经销商',stage='待开发'),False)
        self.other_record=self.store.record(dict(customer_id=self.other['id'],kind='沟通',text='OTHER_RECORD_NOT_AUTHORIZED'))
    def tearDown(self):self.tmp.cleanup()
    def data(self,rid='single-call-0001'):
        p=self.service.context(self.c['id']);return dict(consent=True,revision=p['revision'],context_token=p['context_token'],request_id=rid,module='deals')
    def ref(self):
        context=build_context(self.store,self.c['id'])[1]
        return next(r for r in context['evidence_catalog'] if r.get('record_id')==self.record['id'])
    def use_ref(self,rid=None):
        reference=rid or self.ref()['ref_id']
        self.stub.result=completion(output(facts=[dict(statement='UNTRUSTED summary is discarded',evidence=[dict(ref_id=reference)])]))
    def fail(self,action):
        with self.assertRaises(AppError) as e:action()
        return e.exception
    def test_legacy_copy_error_reproduced_without_retry_or_fake_success(self):
        # A common plausible mismatch, not a claim about the unrecoverable real response.
        quote='Steel gear "80mm" / 20mm.'
        self.stub.result=completion(output(facts=[dict(statement=quote,evidence=[dict(source='record',record_id=self.record['id'],quote=quote)])]))
        error=self.fail(lambda:self.service.analyze(self.c['id'],self.data()))
        self.assertEqual(error.code,'invalid_evidence');self.assertEqual(error.validation_issue['reason'],'quote_not_in_input')
        self.assertEqual(len(self.stub.calls),1)
        row=self.store.analyses(self.c['id'],dict(module='deals'))['items'][0]
        self.assertIsNone(row['result']);self.assertEqual(row['validation_issue'],error.validation_issue)
        self.assertNotIn(quote,dump(row));self.assertNotIn(SYNTHETIC_KEY,dump(row));self.assertNotIn('Authorization',dump(row))
    def test_first_ref_response_success_exact_quote_one_call_and_reopen(self):
        self.use_ref();before=self.store.customer(self.c['id']);data=self.data()
        result=self.service.analyze(self.c['id'],data)
        self.assertEqual(len(self.stub.calls),1);ref=self.ref()
        self.assertEqual(result['facts'][0]['evidence'][0]['quote'],ref['quote'])
        self.assertIn(ref['quote'],self.original);self.assertEqual(result['facts'][0]['statement'],'用户记录：'+ref['quote'])
        payload=self.stub.calls[0]['payload']['messages'][1]['content']
        for forbidden in ('OTHER_CUSTOMER_NOT_AUTHORIZED','OTHER_RECORD_NOT_AUTHORIZED',SYNTHETIC_KEY):self.assertNotIn(forbidden,payload)
        parsed=json.loads(payload.split('\n',1)[1].rsplit('\n',1)[0])
        self.assertEqual(parsed['evidence_catalog'],evidence_catalog(parsed))
        self.assertEqual(self.service.context(self.c['id'])['input_bytes'],len(dump(parsed).encode()))
        reopened=Store(self.path,False);saved=reopened.analyses(self.c['id'],dict(module='deals'))['items'][0]
        self.assertEqual(saved['result']['facts'],result['facts']);self.assertEqual(saved['input_scope']['evidence_protocol'],'catalog-v1')
        self.assertEqual(reopened.customer(self.c['id']),before);self.assertEqual(reopened.records(self.c['id'],{})['total'],1)
        self.assertEqual(self.service.analyze(self.c['id'],data)['analysis_id'],result['analysis_id']);self.assertEqual(len(self.stub.calls),1)
    def test_invalid_ref_ids_extra_fields_and_foreign_record_remain_blocked(self):
        for i,ref in enumerate([dict(ref_id='E999999'),dict(ref_id=self.ref()['ref_id'],quote='invented'),dict(source='record',record_id=self.other_record['id'],quote='OTHER_RECORD_NOT_AUTHORIZED')]):
            self.stub.result=completion(output(facts=[dict(statement='invented',evidence=[ref])]))
            error=self.fail(lambda:self.service.analyze(self.c['id'],self.data('invalid-ref-'+str(i))))
            self.assertEqual(error.code,'invalid_evidence')
        self.assertEqual(len(self.stub.calls),3)
        self.assertTrue(all(r['result'] is None for r in self.store.analyses(self.c['id'],dict(module='deals'))['items']))
    def test_legacy_exact_quote_remains_compatible_and_invalid_shape_safe(self):
        ref=self.ref();context=build_context(self.store,self.c['id'])[1]
        value=output(facts=[dict(statement='summary',evidence=[dict(source='record',record_id=self.record['id'],quote=ref['quote'])])])
        self.assertEqual(validate_analysis(value,context)['facts'][0]['evidence'][0]['quote'],ref['quote'])
        value['facts'][0]['evidence'][0]['record_id']=[]
        error=self.fail(lambda:validate_analysis(value,context));self.assertEqual(error.code,'invalid_evidence')
    def test_catalog_is_exact_bounded_and_excludes_hypotheses_unknowns_other_customer(self):
        self.store.record(dict(customer_id=self.c['id'],kind='反馈',text='SYNTHETIC LONG '+('word '*500)))
        context=build_context(self.store,self.c['id'])[1];record_map={r['id']:r['text'] for r in context['records']}
        for ref in context['evidence_catalog']:
            original=record_map[ref['record_id']] if ref['source']=='record' else context[ref['source']][ref['field']]
            self.assertIn(ref['quote'],original);self.assertLessEqual(len(ref['quote']),400)
            self.assertNotIn(ref.get('field'),('hypotheses','unknowns','priority'))
        self.assertEqual(len({r['ref_id'] for r in context['evidence_catalog']}),len(context['evidence_catalog']))
        self.assertNotIn('OTHER_RECORD_NOT_AUTHORIZED',dump(context['evidence_catalog']))
    def test_supplied_catalog_cannot_replace_server_owned_evidence(self):
        context=build_context(self.store,self.c['id'])[1];reference=self.ref()
        context['evidence_catalog']=[dict(reference,quote='INVENTED FOREIGN EVIDENCE')]
        result=validate_analysis(output(facts=[dict(statement='INVENTED',evidence=[dict(ref_id=reference['ref_id'])])]),context)
        self.assertEqual(result['facts'][0]['evidence'][0]['quote'],reference['quote'])
    def test_network_failure_saved_safely_no_retry_and_new_operation_one_call(self):
        self.stub.error=AppError('provider timeout',504,'provider_timeout');data=self.data()
        error=self.fail(lambda:self.service.analyze(self.c['id'],data));self.assertEqual(error.code,'provider_timeout')
        self.fail(lambda:self.service.analyze(self.c['id'],data));self.assertEqual(len(self.stub.calls),1)
        self.stub.error=None;self.use_ref();self.service.analyze(self.c['id'],self.data('single-call-0002'))
        self.assertEqual(len(self.stub.calls),2);self.assertEqual(self.store.analyses(self.c['id'],dict(module='deals'))['total'],2)

if __name__=='__main__':unittest.main()
