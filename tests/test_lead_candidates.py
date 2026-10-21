import copy,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from backend.common import TYPES,STAGES
from backend.production_runtime import ClosedStore
from backend.discovery import DiscoveryService,public_candidate
from backend.entity_candidates import summarize
from backend.source_evidence import assessment,attach
from backend.lead_candidates import project
from backend.discovery_results import listing

def source(title='SyntheticExampleGears',summary='We provide gear hobbing services.',url='https://example.invalid/about'):
    return {'candidate_id':'1'*32,'source_kind':'search_result','state':'preview','suggested_name':title,'url':url,'domain':url.split('/')[2],
            'search_provenance':{'source_url':url,'title':title,'summary':summary,'snippet':'','searched_at':'offline'}}


class LeadCandidateTests(unittest.TestCase):
    def test_brand_without_legal_name_remains_visible_but_unverified(self):
        item=source();attach(item)
        self.assertTrue(item['lead']['eligible']);self.assertEqual(item['lead']['display_name'],'SyntheticExampleGears')
        self.assertIsNone(assessment(item)['identity_claim'])
        self.assertFalse(item['lead']['website_verified']);self.assertFalse(item['lead']['terminal_buyer_verified'])
        self.assertEqual(item['lead']['purchase_intent'],'unknown')

    def test_third_party_directory_and_pdf_cannot_be_promoted(self):
        for url in ('https://linkedin.com/posts/x','https://top.chinaz.com/Html/site_x.html','https://example.invalid/directory/gears','https://example.invalid/gear.pdf'):
            item=source(url=url);item['lead']={'eligible':True};attach(item)
            self.assertFalse(item['lead']['eligible'])

    def test_equipment_marketing_does_not_become_terminal_lead(self):
        for text in ('We manufacture hobbing machines. We provide gear hobbing services.',
                     'We offer a complete range of CNC gear machines including hobbing and grinding.'):
            item=source(summary=text);attach(item);self.assertFalse(item['lead']['eligible'])

    def test_negation_hypothetical_and_planned_services_do_not_qualify(self):
        for text in ('We do not provide gear hobbing services.','We could provide gear machining services.',
                     'If we provide gear grinding services, contact us.','We plan to provide gear manufacturing services.'):
            item=source(summary=text);self.assertFalse(project(item,assessment(item))['eligible'])

    def test_names_alone_and_business_without_name_are_insufficient(self):
        for item in (source(summary='Welcome to SyntheticExampleGears.'),source(title='Custom Gear Manufacturer')):
            self.assertFalse(project(item,assessment(item))['eligible'])

    def test_generic_marketing_heading_is_not_business_evidence(self):
        item=source(summary='# We provide gear hobbing services.\nWhy choose our precision machining solutions')
        self.assertFalse(project(item,assessment(item))['eligible'])

    def test_generic_service_heading_is_not_invented_as_brand(self):
        for title in ('Gear Manufacturing Solutions','Precision Machining Capabilities','Custom Gear Supplier'):
            item=source(title=title)
            self.assertFalse(project(item,assessment(item))['eligible'])

    def test_chinese_brand_requires_explicit_repeated_source_attribution(self):
        item=source(title='聚力齿轮',summary='聚力齿轮专注于精密齿轮加工。我们提供滚齿加工服务。')
        self.assertTrue(project(item,assessment(item))['eligible'])
        self.assertIsNone(assessment(item)['identity_claim'])
        item=source(title='精密齿轮',summary='精密齿轮专注于精密齿轮加工。我们提供滚齿加工服务。')
        self.assertFalse(project(item,assessment(item))['eligible'])

    def test_product_material_lead_does_not_change_identity_policy(self):
        item=source(title='Gears - Alpha Gears Ltd.',url='https://example.invalid/product-category/gears')
        attach(item);self.assertTrue(item['lead']['eligible'])
        self.assertEqual(item['source_assessment']['classification']['page_role'],'product_material')
        self.assertFalse(item['source_classification']['single_company_candidate'])
        self.assertIsNone(assessment(item)['identity_claim'])




    def test_changed_evidence_same_url_does_not_inflate_lead_page_count(self):
        a=source();b=source(summary='We manufacture precision gears.');b['candidate_id']='2'*32
        self.assertEqual(summarize([a,b])['unverified_lead_count'],1)

    def test_legacy_projection_never_mutates_saved_candidate(self):
        item=source();old=copy.deepcopy(item);value=public_candidate(item)
        self.assertTrue(value['lead']['eligible']);self.assertEqual(item,old)

    def test_captured_or_reference_record_cannot_inherit_stale_lead(self):
        item=source();item['capture']={'pages':[]};item['lead']={'eligible':True}
        self.assertNotIn('lead',public_candidate(item))
        self.assertEqual(summarize([item],True)['unverified_lead_count'],0)

    def test_existing_customer_domain_exclusion_still_applies(self):
        with tempfile.TemporaryDirectory() as folder:
            store=ClosedStore(Path(folder)/'test.sqlite3',False)
            store.save_customer({'name':'Existing Company','type':TYPES[0],'stage':STAGES[0],'website':'https://example.invalid'},False)
            service=DiscoveryService(store,None);item=source();item['expires']=time.monotonic()+600;service.jobs[item['candidate_id']]=item
            self.assertEqual(listing(service,{})['total'],0)

    def test_weak_brand_similarity_does_not_exclude_or_merge(self):
        with tempfile.TemporaryDirectory() as folder:
            store=ClosedStore(Path(folder)/'test.sqlite3',False)
            store.save_customer({'name':'SyntheticExampleGears Similar','type':TYPES[0],'stage':STAGES[0]},False)
            service=DiscoveryService(store,None);item=source();item['expires']=time.monotonic()+600;service.jobs[item['candidate_id']]=item
            result=listing(service,{})
            self.assertEqual(result['total'],1);self.assertEqual(result['entity_summary']['unverified_lead_count'],1)


if __name__=='__main__':unittest.main()
