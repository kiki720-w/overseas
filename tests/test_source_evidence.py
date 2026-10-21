import copy,json,unittest
from pathlib import Path
from unittest.mock import patch
from backend.entity_candidates import summarize
from backend.source_evidence import assessment,attach

def item(url='https://first.example/about',name='Alpha Gears GmbH',summary=None):
    return {'candidate_id':url,'source_kind':'search_result','url':url,'suggested_name':name,'search_provenance':{'source_url':url,'title':name,'summary':summary if summary is not None else '# '+name+'\nWe manufacture gears.\nWe provide gear hobbing services.','snippet':'','searched_at':'synthetic-time'}}

class SourceEvidenceTests(unittest.TestCase):
    def test_generic_exact_company_on_two_pages_keeps_all_sources(self):
        rows=[item(),item('https://first.example/services')];s=summarize(rows,True)
        self.assertEqual(s['entity_candidate_count'],1);self.assertEqual(len(s['groups'][0]['source_pages']),2)
        self.assertEqual(len(rows[0]['source_assessment']['related_source_urls']),2)
        self.assertTrue(all(not e['capability_verified'] and e['purchase_intent']=='unknown' for e in s['groups'][0]['capability_evidence']))
    def test_different_name_same_domain_no_merge(self):
        self.assertEqual(summarize([item(),item('https://first.example/subsidiary','Alpha Gear GmbH')])['entity_candidate_count'],2)
    def test_same_name_different_publisher_no_merge(self):
        self.assertEqual(summarize([item(),item('https://second.example/about')])['entity_candidate_count'],2)
    def test_same_name_different_addresses_no_merge(self):
        a=item(summary='# Alpha Gears GmbH\nWe manufacture gears.\nExample Str. 1\n12345 City');b=item('https://first.example/branch',summary='# Alpha Gears GmbH\nWe manufacture gears.\nExample Str. 2\n12345 City')
        self.assertEqual(summarize([a,b])['entity_candidate_count'],2)
    def test_multiple_names_prevent_grouping(self):
        s=summarize([item(summary='# Alpha Gears GmbH\n# Beta Gears GmbH\nWe manufacture gears.')]);self.assertEqual(s['entity_candidate_count'],0)
    def test_directory_list_never_counts_company_despite_single_excerpt(self):
        a=item('https://new-directory.example/companies/gears');r=assessment(a)
        self.assertEqual(r['classification']['page_role'],'directory_list');self.assertIsNone(r['identity_claim']);self.assertEqual(summarize([a])['reference_source_page_count'],1)
    def test_directory_company_detail_is_separate_reference(self):
        a=item('https://www.wlw.de/company/alpha');r=assessment(a)
        self.assertEqual(r['classification']['page_role'],'company_detail_reference');self.assertIsNone(r['identity_claim'])
    def test_pdf_and_html_product_material_never_count_companies(self):
        for url,role in [('https://first.example/alpha.pdf','document_reference'),('https://first.example/products/gear','product_material')]:
            a=item(url);r=assessment(a);self.assertEqual(r['classification']['page_role'],role);self.assertIsNone(r['identity_claim']);self.assertTrue(all(e['status']=='reference_only' for e in r['capability_evidence']))
    def test_brand_only_title_does_not_invent_legal_identity(self):
        self.assertIsNone(assessment(item(name='Alpha',summary='We manufacture gears.'))['identity_claim'])
    def test_negation_condition_and_equipment_preserve_uncertainty(self):
        for text,status in [('We do not provide hobbing services.','negated_or_uncertain'),('We plan hobbing services.','negated_or_uncertain'),('We sell hobbing machines.','equipment_mention_unverified')]:
            r=assessment(item(summary=text));self.assertTrue(r['capability_evidence']);self.assertEqual(r['capability_evidence'][0]['status'],status);self.assertIsNone(r['identity_claim'])
    def test_quotation_and_source_provenance_exact(self):
        a=item();r=assessment(a);e=r['capability_evidence'][0];self.assertIn(e['quote'],a['search_provenance']['summary']);self.assertEqual(e['source_url'],a['url']);self.assertEqual(e['evidence_kind'],'search_summary')
    def test_read_projection_does_not_mutate_saved_input(self):
        a=item();original=copy.deepcopy(a);projected=attach(copy.deepcopy(a));self.assertEqual(a,original);self.assertIn('source_assessment',projected)

    def test_inline_multiple_legal_names_do_not_form_one_entity(self):
        for text in ['# Alpha Gears GmbH and Beta Gears GmbH\nWe manufacture gears.','We manufacture gears for Beta Gears GmbH.']:
            self.assertIsNone(assessment(item(summary=text))['identity_claim'])
    def test_multiple_addresses_do_not_bridge_groups(self):
        a=item(summary='# Alpha Gears GmbH\nWe manufacture gears.\nExample Str. 1\n12345 City\nOther Str. 2\n23456 Town');self.assertIsNone(assessment(a)['identity_claim'])
    def test_title_summary_identity_conflict_is_unresolved(self):
        a=item(summary='# Beta Gears GmbH\nWe manufacture gears.');self.assertIsNone(assessment(a)['identity_claim'])
    def test_new_publisher_directory_description_is_reference(self):
        a=item('https://new.example/profile/alpha',summary='Business directory listing\n# Alpha Gears GmbH\nWe manufacture gears.');r=assessment(a);self.assertEqual(r['classification']['page_role'],'company_detail_reference');self.assertIsNone(r['identity_claim'])
    def test_powered_by_is_not_an_identity_claim(self):
        a=item(name='Brand',summary='Powered By Alpha Gears GmbH');self.assertIsNone(assessment(a)['identity_claim'])
    def test_title_and_operating_statement_not_confused_with_powered_by(self):
        a=item(summary='We manufacture gears.\nPowered By Alpha Gears GmbH');self.assertEqual(assessment(a)['identity_claim']['name'],'Alpha Gears GmbH')

if __name__=='__main__':unittest.main()
