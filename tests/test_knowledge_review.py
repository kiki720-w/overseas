# -*- coding: utf-8 -*-
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from backend.store import Store
from backend.knowledge import KnowledgeService
from backend.common import AppError
from backend.model_analysis import build_context,validate_analysis
import test_models as fixtures

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'synthetic.sqlite3';self.store=Store(self.path,False);self.k=KnowledgeService(self.store)
        p=self.k.preview({'title':'Synthetic catalog','category':'product','text':'Synthetic catalog lists CNC fixture. Accuracy remains unverified.'});self.saved=self.k.confirm({'job_id':p['job_id'],'consent':True});self.did=self.saved['document_id']
        self.customer=self.store.save_customer({'name':'Synthetic','type':'终端加工厂','stage':'待开发'},False)
    def tearDown(self):self.tmp.cleanup()
    def payload(self,**changes):
        d=self.store.knowledge_document(self.did);data={'document_id':self.did,'expected_version':d['version'],'expected_revision':d['revision'],'review_status':'source_checked','review_reason':'Only checked the catalog name in the source; no technical proof.','reviewed_fields':['title','content.product_name'],'review_source':{'filename':'synthetic-source.pdf','sha256':'a'*64,'pdf_pages':[7,8],'printed_pages':['09','10']}};data.update(changes);return data
    def test_source_check_audit_filters_and_no_proof(self):
        d=self.store.knowledge_status(self.payload());self.assertEqual(d['review_status'],'source_checked');self.assertFalse(d['independently_proven']);self.assertIn('企业自述',d['review_label']);self.assertEqual(d['version'],1)
        history=self.store.knowledge_document(self.did)['review_history'];self.assertEqual(history[0]['action'],'status_change');self.assertEqual(history[0]['review']['source']['pdf_pages'],[7,8]);self.assertTrue(history[0]['review']['reviewed_at']);self.assertEqual(history[0]['before']['review_status'],'unverified')
        self.assertEqual(self.store.knowledge_documents({'review_status':'source_checked'})['total'],1);self.assertEqual(self.store.knowledge_search({'q':'fixture','review_status':'unverified'})['items'],[])
        hit=self.store.knowledge_search({'q':'fixture','review_status':'source_checked'})['items'][0];self.assertEqual(hit['review_info']['reviewed_fields'],['title','content.product_name'])
    def test_required_scope_reason_source_and_revision(self):
        for changes in [{'review_reason':''},{'reviewed_fields':[]},{'expected_revision':0},{'expected_revision':True},{'review_source':{'filename':'../bad.pdf','sha256':'a'*64,'pdf_pages':[1],'printed_pages':[]}},{'review_source':{'filename':'source.pdf','sha256':'invalid','pdf_pages':[1],'printed_pages':[]}},{'review_source':{'filename':'source.pdf','sha256':'a'*64,'pdf_pages':[True],'printed_pages':[]}}]:
            with self.subTest(keys=list(changes)),self.assertRaises(AppError):self.store.knowledge_status(self.payload(**changes))
        self.assertEqual(self.store.knowledge_document(self.did)['review_status'],'unverified')
    def test_audit_immutable_and_cross_version_cas(self):
        self.store.knowledge_status(self.payload());old=self.payload();before=self.store.knowledge_document(self.did)['review_history']
        p=self.k.preview({'document_id':self.did,'expected_version':1,'title':'Synthetic changed','category':'product','text':'New synthetic version'});self.k.confirm({'job_id':p['job_id'],'consent':True})
        d=self.store.knowledge_document(self.did);self.assertEqual(d['review_status'],'unverified');self.assertIsNone(d['review_info']);self.assertEqual(self.store.knowledge_document(self.did,1)['review_history'],before)
        with self.assertRaises(AppError):self.store.knowledge_status(old)
        with self.store.db() as db:
            with self.assertRaises(sqlite3.IntegrityError):db.execute('UPDATE kb_reviews SET data=?',('{}',))
            with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM kb_reviews')
    def test_context_reference_scope_and_historical_result_immutable(self):
        d=self.store.knowledge_document(self.did);selections=[{'document_id':self.did,'version':1,'chunk_id':d['chunks'][0]['chunk_id']}]
        before=build_context(self.store,self.customer['id'],selections);entry,_=self.store.analysis_begin(self.customer['id'],{'module':'deals','request_id':'synthetic-review-original','context_token':before[4],'knowledge_selections':selections},{'provider':'mock','model':'rules'},'mock');row=self.store.analysis_finish(entry,fixtures.output())
        self.store.knowledge_status(self.payload());after=build_context(self.store,self.customer['id'],selections);self.assertNotEqual(before[4],after[4]);self.assertTrue(self.store.analysis_view(row)['historical']);self.assertEqual(self.store.analyses(self.customer['id'],{'module':'deals'})['items'][0]['result'],row['result'])
        output=validate_analysis(fixtures.output(facts=[{'statement':'Independently proven capability','evidence':[{'ref_id':'K001'}]}]),after[1]);f=output['facts'][0];self.assertIn('已核对原文',f['statement']);self.assertIn('非独立证明',f['statement']);self.assertFalse(f['evidence'][0]['independently_proven']);self.assertEqual(f['evidence'][0]['review_info']['reviewed_fields'],['title','content.product_name'])
    def test_migration_preserves_user_confirmed_and_reviews(self):
        self.store.knowledge_status({'document_id':self.did,'expected_version':1,'review_status':'confirmed'})
        with self.store.db() as db:doc_before=db.execute('SELECT data FROM kb_documents WHERE id=?',(self.did,)).fetchone()[0];history_before=db.execute('SELECT data FROM kb_reviews ORDER BY rowid').fetchall()
        reopened=Store(self.path,False)
        with reopened.db() as db:self.assertEqual(doc_before,db.execute('SELECT data FROM kb_documents WHERE id=?',(self.did,)).fetchone()[0]);self.assertEqual(history_before,db.execute('SELECT data FROM kb_reviews ORDER BY rowid').fetchall())
        self.assertEqual(reopened.knowledge_document(self.did)['review_status'],'confirmed');self.assertIn('非独立证明',reopened.knowledge_document(self.did)['review_label'])
