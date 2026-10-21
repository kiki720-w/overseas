import gc
import json
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from backend.common import AppError,TYPES,STAGES
from backend.store import Store
from backend.website import WebsiteService
from backend.website_fetch import Collector
from backend.discovery import DiscoveryService
from backend.model_analysis import build_context
from test_website import fixture,BASE
from server import make_server

class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'synthetic.sqlite';self.store=Store(self.path,False);self.websites=WebsiteService(self.store,Collector(fixture));self.s=DiscoveryService(self.store,self.websites)
    def tearDown(self):gc.collect();self.tmp.cleanup()
    def preview(self,**fields):return self.s.preview(dict(url=BASE,consent=True,**fields))
    def confirm(self,p,**fields):return self.s.confirm(dict(dict(candidate_id=p['candidate_id'],name='DEMO synthetic new',type=TYPES[0],consent=True),**fields))
    def count(self,table):
        db=self.store.db()
        try:return db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
        finally:db.close()
    def test_product_material_capture_cannot_be_confirmed_as_company(self):
        p=self.preview();self.s.jobs[p['candidate_id']]['capture']['pages'][0]['url']=BASE+'/products/gear'
        with self.assertRaises(AppError) as caught:self.confirm(p)
        self.assertEqual(caught.exception.code,'candidate_not_company');self.assertEqual(self.count('customers'),0)
    def test_preview_without_customer_consent_and_no_database_changes(self):
        with self.assertRaises(AppError):self.s.preview({'url':BASE})
        p=self.preview();self.assertEqual(p['state'],'preview');self.assertFalse(p['saved']);self.assertNotIn('expires',p);self.assertEqual(len(p['capture']['pages']),3)
        for t in ['customers','records','website_captures','discovery_candidates','discovery_events']:self.assertEqual(self.count(t),0)
    def test_stash_persists_without_creating_customer_and_resumes(self):
        p=self.preview();s=self.s.stash({'candidate_id':p['candidate_id'],'consent':True});self.assertEqual(s['state'],'draft');self.assertTrue(self.s.stash({'candidate_id':p['candidate_id'],'consent':True})['replayed']);self.assertEqual(self.count('customers'),0);self.assertEqual(self.count('discovery_events'),1)
        reopened=Store(self.path,False);service=DiscoveryService(reopened,WebsiteService(reopened));saved=reopened.discovery_candidates({'state':'draft'})['items'];self.assertEqual(saved[0]['capture'],p['capture']);r=service.confirm({'candidate_id':p['candidate_id'],'name':'DEMO synthetic resumed','type':TYPES[2],'consent':True});self.assertEqual(r['state'],'accepted')
    def test_confirm_atomic_customer_provenance_record_and_replay(self):
        p=self.preview();r=self.confirm(p);again=self.confirm(p);self.assertTrue(again['replayed']);self.assertEqual(r['customer_id'],again['customer_id']);self.assertEqual(self.count('customers'),1);self.assertEqual(self.count('records'),1);self.assertEqual(self.count('website_captures'),1);self.assertEqual(self.count('discovery_events'),1);self.assertNotIn('request_signature',r)
        customer=self.store.customer(r['customer_id']);self.assertEqual(customer['country'],'');self.assertEqual(customer['contact_name'],'');self.assertEqual(customer['signals'],'');self.assertIn(BASE,customer['source']);self.assertIn('官网自述',customer['facts']);self.assertEqual(customer['email'],'demo@fixture.example')
        _,ctx,_,_,_=build_context(self.store,customer['id']);self.assertEqual(ctx['records'][0]['website_capture_id'],p['candidate_id']);self.assertIn('非采购意向',ctx['records'][0]['text'])
        reopened=DiscoveryService(Store(self.path,False),self.websites);self.assertTrue(reopened.confirm({'candidate_id':p['candidate_id'],'name':'DEMO synthetic new','type':TYPES[0],'consent':True})['replayed'])
    def test_confirmation_requires_explicit_consent_name_and_type(self):
        p=self.preview()
        for extra in [{'consent':False},{'name':''},{'type':''},{'type':'Made up type'},{'name':'x'*201},{'country':'x'*101}]:
            body=dict(candidate_id=p['candidate_id'],name='DEMO',type=TYPES[0],consent=True);body.update(extra)
            with self.subTest(extra=extra),self.assertRaises(AppError):self.s.confirm(body)
        self.assertEqual(self.count('customers'),0)
    def test_name_duplicate_requires_explicit_existing_link_and_never_overwrites(self):
        old=self.store.save_customer({'name':'DEMO EXISTING','type':TYPES[1],'stage':STAGES[1],'facts':'ORIGINAL FACT','country':'Unknown'},False);p=self.preview()
        with self.assertRaises(AppError) as caught:self.confirm(p,name='  demo existing  ')
        self.assertEqual(caught.exception.code,'candidate_duplicate');self.assertEqual(caught.exception.details['duplicate_customer']['id'],old['id']);self.assertEqual(self.count('customers'),1);self.assertEqual(self.count('records'),0)
        r=self.s.confirm({'candidate_id':p['candidate_id'],'existing_customer_id':old['id'],'expected_customer_version':old['version'],'consent':True});self.assertEqual(r['customer_id'],old['id']);self.assertEqual(self.store.customer(old['id']),old);self.assertEqual(self.count('records'),1)
    def test_domain_duplicate_www_case_and_unchanged_existing(self):
        old=self.store.save_customer({'name':'Different display name','type':TYPES[2],'stage':STAGES[0],'website':'https://WWW.Fixture.example/old?x=y'},False);p=self.preview()
        with self.assertRaises(AppError) as caught:self.confirm(p)
        self.assertEqual(caught.exception.code,'candidate_duplicate');self.assertEqual(self.store.customer(old['id']),old);self.assertEqual(self.count('customers'),1)
    def test_existing_link_requires_current_revision_and_known_id(self):
        old=self.store.save_customer({'name':'Old','type':TYPES[0],'stage':STAGES[0]},False);p=self.preview()
        for expected in [None,True,0]:
            with self.assertRaises(AppError):self.s.confirm({'candidate_id':p['candidate_id'],'existing_customer_id':old['id'],'expected_customer_version':expected,'consent':True})
        with self.assertRaises(AppError):self.s.confirm({'candidate_id':p['candidate_id'],'existing_customer_id':'missing','expected_customer_version':1,'consent':True})
        self.assertEqual(self.count('records'),0)
    def test_idempotent_replay_rejects_changed_content_or_customer(self):
        p=self.preview();self.confirm(p)
        with self.assertRaises(AppError):self.confirm(p,name='Changed display name')
        old=self.store.save_customer({'name':'Other','type':TYPES[0],'stage':STAGES[0]},False)
        with self.assertRaises(AppError):self.s.confirm({'candidate_id':p['candidate_id'],'existing_customer_id':old['id'],'expected_customer_version':1,'consent':True})
        self.assertEqual(self.count('records'),1)
    def test_preview_expiry_stashed_draft_no_expiry_and_capacity(self):
        p=self.preview();self.s.jobs[p['candidate_id']]['expires']=0
        with self.assertRaises(AppError):self.confirm(p)
        for _ in range(5):self.preview()
        with self.assertRaises(AppError):self.preview()
        q=next(iter(self.s.jobs));self.s.stash({'candidate_id':q,'consent':True});self.preview();self.assertEqual(len(self.s.jobs),5)
    def test_reject_preserves_source_and_prevents_confirmation(self):
        p=self.preview();self.s.reject({'candidate_id':p['candidate_id']});self.assertTrue(self.s.reject({'candidate_id':p['candidate_id']})['replayed']);self.assertEqual(self.store.discovery_candidates({'state':'rejected'})['items'][0]['capture'],p['capture'])
        with self.assertRaises(AppError):self.confirm(p)
        self.assertEqual(self.count('customers'),0)
        p=self.preview();self.confirm(p)
        with self.assertRaises(AppError):self.s.reject({'candidate_id':p['candidate_id']})
        self.assertEqual(self.count('customers'),1)
    def test_local_keyword_match_has_exact_evidence_and_no_ai_probability(self):
        f=self.store.factory();self.store.save_factory(dict(f,focus='CNC',products='CNC,不存在产品'))
        p=self.preview();hint=p['match_hint'];self.assertEqual(hint['matched_terms'],['CNC']);e=hint['evidence'][0];self.assertIn('CNC',e['quote']);self.assertEqual(e['source_url'],BASE);self.assertNotIn('score',hint);self.assertNotIn('probability',hint)
    def test_search_dependency_is_explicit_and_does_not_generate_candidates(self):
        m=self.s.meta();self.assertFalse(m['search']['configured']);self.assertEqual(m['protocol'],'discovery-v1')
        with self.assertRaises(AppError) as caught:self.s.search({'query':'CNC manufacturers','consent':True})
        self.assertEqual(caught.exception.status,503);self.assertEqual(caught.exception.code,'search_not_connected');self.assertEqual(self.count('discovery_candidates'),0)
        for data in [{'query':''},{'query':'x'*501},{'query':'CNC','consent':False}]:
            with self.assertRaises(AppError):self.s.search(data)
    def test_shared_network_slots_and_private_address_rejection(self):
        self.websites.slots.acquire();self.websites.slots.acquire()
        with self.assertRaises(AppError):self.preview()
        self.assertEqual(self.s.jobs,{});self.websites.slots.release();self.websites.slots.release()
        with self.assertRaises(AppError):self.s.preview({'url':'http://127.0.0.1','consent':True})
    def test_listing_validation_and_candidate_identifiers(self):
        with self.assertRaises(AppError):self.store.discovery_candidates({'state':'invalid'})
        with self.assertRaises(AppError):self.store.discovery_candidates({'page_size':'101'})
        with self.assertRaises(AppError):self.s.reject({'candidate_id':'x'})
    def test_candidate_audit_is_immutable(self):
        p=self.preview();self.s.stash({'candidate_id':p['candidate_id'],'consent':True});db=self.store.db()
        try:
            with self.assertRaises(sqlite3.IntegrityError):db.execute("UPDATE discovery_events SET data='{}'")
            with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM discovery_events')
        finally:db.close()
    def test_failed_confirmation_rolls_back_customer_and_all_children(self):
        p=self.preview();db=self.store.db();db.execute('CREATE TRIGGER synthetic_fail BEFORE INSERT ON website_captures BEGIN SELECT RAISE(ABORT,"synthetic failure");END;');db.commit();db.close()
        with self.assertRaises(sqlite3.IntegrityError):self.confirm(p)
        for table in ['customers','records','website_captures','discovery_candidates','discovery_events']:self.assertEqual(self.count(table),0)
    def test_concurrent_separate_candidates_same_domain_do_not_duplicate(self):
        a=self.preview();b=self.preview();results=[]
        def act(p):
            try:results.append(self.confirm(p))
            except AppError as e:results.append(e)
        threads=[threading.Thread(target=act,args=(p,)) for p in [a,b]]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(self.count('customers'),1);self.assertEqual(self.count('records'),1);self.assertEqual(sum(isinstance(x,AppError) and x.code=='candidate_duplicate' for x in results),1)
    def test_demo_candidate_stays_demo(self):
        self.s.mode='demo';r=self.confirm(self.preview());self.assertTrue(r['customer']['demo'])
    def test_http_candidate_contract_csrf_duplicate_details_and_search_disabled(self):
        server=make_server(self.store,'local',port=0,website_service=self.websites,discovery_service=self.s);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();origin='http://127.0.0.1:'+str(server.server_port)
        def api(path,data=None,token=''):
            request=urllib.request.Request(origin+path,json.dumps(data).encode() if data is not None else None,{'Content-Type':'application/json','X-CSRF-Token':token} if data is not None else {})
            with urllib.request.urlopen(request) as r:return json.load(r)
        try:
            token=api('/api/meta')['csrf'];self.assertEqual(api('/api/discovery/meta')['protocol'],'discovery-v1')
            with self.assertRaises(urllib.error.HTTPError):api('/api/discovery/preview',{'url':BASE,'consent':True},'wrong')
            p=api('/api/discovery/preview',{'url':BASE,'consent':True},token);r=api('/api/discovery/confirm',{'candidate_id':p['candidate_id'],'name':'DEMO','type':TYPES[0],'consent':True},token);p=api('/api/discovery/preview',{'url':BASE,'consent':True},token)
            try:api('/api/discovery/confirm',{'candidate_id':p['candidate_id'],'name':'Another','type':TYPES[0],'consent':True},token);self.fail('Duplicate accepted')
            except urllib.error.HTTPError as e:body=json.load(e);self.assertEqual(e.code,409);self.assertEqual(body['duplicate_customer']['id'],r['customer_id'])
            self.assertEqual(api('/api/discovery/candidates?state=accepted')['total'],1)
            try:api('/api/discovery/search',{'query':'CNC','consent':True},token);self.fail('Search falsely connected')
            except urllib.error.HTTPError as e:self.assertEqual(e.code,503);self.assertEqual(json.load(e)['code'],'search_not_connected')
        finally:server.shutdown();server.server_close();thread.join()
