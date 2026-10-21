import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.entity_candidates import summarize
from backend.search_identity import reference_url
from backend.source_evidence import assessment, legal_names


def source(url='https://example.invalid/about', title='Alpha Gears Ltd.', summary='We manufacture gears. We provide gear hobbing services.'):
    return {'candidate_id':url, 'source_kind':'search_result', 'url':url, 'suggested_name':title,
            'search_provenance':{'source_url':url, 'title':title, 'summary':summary, 'snippet':'', 'searched_at':'offline'}}


class SourceDiagnosisTests(unittest.TestCase):
    def test_terminal_legal_punctuation_preserves_exact_claim(self):
        for name in ('Alpha Gears Ltd.', 'Alpha Gears Inc.', 'Alpha Gears Co., Ltd.'):
            item=source(title=name)
            claim=assessment(item)['identity_claim']
            self.assertEqual(claim['name'],name)
            self.assertFalse(claim['identity_verified'])

    def test_legal_suffix_requires_word_boundary(self):
        for name in ('Alpha Gears LtdExtra', 'Alpha Gears IncorporatedBrand', 'Alpha Gears LimitedEdition'):
            self.assertEqual(legal_names(name,''),[])
            self.assertIsNone(assessment(source(title=name))['identity_claim'])

    def test_bounded_page_label_is_not_part_of_company_name(self):
        item=source(title='Gears - Fictional Example Gears Ltd.')
        self.assertEqual(assessment(item)['identity_claim']['name'],'Fictional Example Gears Ltd.')

    def test_page_label_does_not_hide_multiple_legal_attributions(self):
        for title,summary in [('Gears - Alpha Ltd. - Beta Ltd.','We manufacture gears.'),
                              ('Gears - Alpha Ltd.','We manufacture gears for Beta Ltd.')]:
            self.assertIsNone(assessment(source(title=title,summary=summary))['identity_claim'])

    def test_product_category_stays_reference_even_with_strong_company_claim(self):
        for path in ('product-category/gears','product-categories/gears'):
            item=source('https://example.invalid/'+path)
            result=assessment(item)
            self.assertEqual(result['classification']['page_role'],'product_material')
            self.assertIsNone(result['identity_claim'])
            self.assertTrue(all(e['status']=='reference_only' for e in result['capability_evidence']))
            self.assertEqual(result['classification']['single_company_candidate'],False)

    def test_similar_path_is_not_treated_as_product_category(self):
        self.assertIsNotNone(assessment(source('https://example.invalid/product-category-policy'))['identity_claim'])

    def test_chinaz_reference_does_not_borrow_listed_company_identity(self):
        url='https://top.chinaz.com/Html/site_example.com.html'
        result=assessment(source(url))
        self.assertTrue(reference_url(url))
        self.assertEqual(result['classification']['page_role'],'directory_list')
        self.assertIsNone(result['identity_claim'])
        self.assertFalse(reference_url('https://top.chinaz.com.evil.invalid/Html/site_example.com.html'))

    def test_modal_statement_not_silently_promoted(self):
        self.assertIsNone(assessment(source(summary='We can produce gears.'))['identity_claim'])



if __name__=='__main__':
    unittest.main()
