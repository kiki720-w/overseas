"""Existing website research/CRM transaction exercised with Aliyun-normalized hits."""
import unittest
from unittest.mock import Mock
import test_search_v1 as fixtures
from backend.aliyun_search import AliyunSearch

class AliyunPipelineTests(unittest.TestCase):
    def test_ali_hit_uses_existing_research_confirm_and_duplicate_pipeline(self):
        fixture=fixtures.SearchV1Tests('test_search_candidate_stash_research_confirm_and_source_retention');fixture.setUp()
        try:
            fixture.service.search_provider=AliyunSearch(Mock(return_value=(200,{'pageItems':[{'title':'ACME','link':'https://acme.example.com/','snippet':'Synthetic company CNC gears.'}]})),cooldown=0,key='synthetic-ali-pipeline',engine_type='GlobalAdvanced')
            result=fixture.service.search({'query':'CNC gears','products':'gears','consent':True});candidate=result['items'][0]
            fixture.service.stash({'candidate_id':candidate['candidate_id'],'consent':True})
            researched=fixture.service.preview({'candidate_id':candidate['candidate_id'],'url':candidate['url'],'consent':True})
            saved=fixture.service.confirm({'candidate_id':candidate['candidate_id'],'name':'Synthetic Aliyun Company','type':'终端加工厂','country':'','consent':True,'company_identity_review':{'confirmed':True,'basis':'Synthetic fixture company identity reviewed for protocol acceptance only.'}})
            self.assertEqual(saved['state'],'accepted');self.assertEqual(saved['search_provenance']['provider'],'aliyun');self.assertEqual(saved['customer']['stage'],'待开发');self.assertIn('采购',saved['customer']['unknowns'])
            replay=fixture.service.confirm({'candidate_id':candidate['candidate_id'],'name':'Synthetic Aliyun Company','type':'终端加工厂','country':'','consent':True,'company_identity_review':{'confirmed':True,'basis':'Synthetic fixture company identity reviewed for protocol acceptance only.'}})
            self.assertTrue(replay['replayed']);self.assertEqual(replay['customer']['id'],saved['customer']['id'])
        finally:fixture.tearDown()

if __name__=='__main__':unittest.main()
