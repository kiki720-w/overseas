import copy,datetime as dt,unittest
from unittest.mock import Mock
from backend.common import AppError
from backend.customs import CustomsService,FIELDS,record

def fixture(stamp='2026-10-01'):
    return {'importer':{'declared_name':'Synthetic Declared Importer','enriched_name':'Synthetic Enrichment','name_evidence':'source_manifest','source_field':'consignee'},'exporter':None,'product_description':'Synthetic gear fixture','hs_code':None,'origin_country':None,'shipment_country':'Synthetic departure','destination_country':'Synthetic destination','trade_direction':'import','transport_mode':'sea','declaration_date':stamp,'quantity':None,'quantity_unit':None,'value':None,'currency':None,'source':{'provider':'Synthetic fixture only','record_id':'fixture-record-1','source_url':'https://example.invalid/fixture','source_date':'2026-10-17','retrieved_at':'2026-10-17T00:00:00Z','dataset_direction':'import','authorized_fields':list(FIELDS)}}

class CustomsContractTests(unittest.TestCase):
    def setUp(self):self.provider=Mock(configured=True);self.provider.fetch.return_value={'records':[fixture()]};self.service=CustomsService(self.provider,today=dt.date(2026,10,17))
    def run_query(self):return self.service.analyze({'query':'gear','consent':True})
    def error(self,code,fn):
        with self.assertRaises(AppError) as caught:fn()
        self.assertEqual(caught.exception.code,code)
    def test_unconfigured_empty_is_unknown_not_zero_and_never_fetches(self):
        empty=CustomsService();meta=empty.meta();self.assertFalse(meta['configured']);self.assertIsNone(meta['records']);self.assertIsNone(meta['metrics']);self.error('customs_not_configured',lambda:empty.analyze({'query':'gear','consent':True}));self.provider.fetch.assert_not_called()
    def test_explicit_consent_and_no_user_endpoint(self):
        self.error('consent_required',lambda:self.service.analyze({'query':'gear'}));self.error('consent_required',lambda:self.service.analyze({'query':'gear','consent':True,'endpoint':'https://not-authorized.invalid'}));self.provider.fetch.assert_not_called()
    def test_names_and_three_country_roles_remain_separate(self):
        row=self.run_query()['records'][0];self.assertEqual(row['importer']['declared_name'],'Synthetic Declared Importer');self.assertEqual(row['importer']['enriched_name'],'Synthetic Enrichment');self.assertIsNone(row['exporter']['declared_name']);self.assertIsNone(row['origin_country']);self.assertNotEqual(row['shipment_country'],row['destination_country']);self.assertEqual(row['source']['dataset_direction'],'import')
    def test_enriched_name_cannot_be_substituted_as_declared(self):
        row=fixture();row['importer']['name_evidence']='supplier_enrichment';self.error('customs_invalid_response',lambda:record(row))
    def test_authorized_field_projection_and_source_required(self):
        row=fixture();row['source']['authorized_fields']=['declaration_date'];result=record(row);self.assertIsNone(result['product_description']);self.assertIsNone(result['importer']['declared_name']);row['source'].pop('authorized_fields');self.error('customs_authorization_missing',lambda:record(row))
    def test_nullable_amounts_unknown_hs_and_no_fabricated_totals(self):
        value=self.run_query();row=value['records'][0];self.assertIsNone(row['hs_code']);self.assertEqual(row['hs_verification'],'unknown');self.assertIsNone(row['quantity']);self.assertIsNone(row['value']);self.assertIsNone(value['metrics']['quantity_total']);self.assertIsNone(value['metrics']['value_total']);self.assertEqual(row['match_explanation']['current_demand'],'unknown');self.assertEqual(row['match_explanation']['matched_raw_product_terms'],['gear'])
    def test_unknown_coverage_empty_results_and_missing_dates_do_not_prove_no_purchases(self):
        value=self.run_query();self.assertEqual(value['metrics']['trend'],'unknown');self.assertIsNone(value['metrics']['recent_90_days_records']);self.provider.fetch.return_value={'records':[]};value=self.run_query();self.assertEqual(value['metrics']['visible_record_count'],0);self.assertIn('不',value['notice']);self.assertEqual(value['metrics']['current_demand'],'unknown')
    def test_trend_requires_full_comparable_coverage_and_valid_dates(self):
        coverage={'complete_for_query':True,'date_start':'2026-01-01','date_end':'2026-10-17'};self.provider.fetch.return_value={'records':[fixture('2026-06-01'),fixture('2026-10-01'),fixture('2026-10-02')],'coverage':coverage};self.assertEqual(self.run_query()['metrics']['trend'],'more_records');self.provider.fetch.return_value['records'].append(fixture(None));self.assertEqual(self.run_query()['metrics']['trend'],'unknown')
    def test_malformed_hs_numbers_dates_and_caps_rejected(self):
        for key,value in [('hs_code','guessed'),('quantity',-1),('value',float('nan')),('declaration_date','unknown')]:
            row=fixture();row[key]=value;self.error('customs_invalid_response',lambda row=row:record(row))
        self.provider.fetch.return_value={'records':[fixture()]*501};self.error('customs_invalid_response',self.run_query)

if __name__=='__main__':unittest.main()
