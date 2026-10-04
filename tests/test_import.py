# -*- coding: utf-8 -*-
import base64
import csv
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.common import AppError,TYPES,STAGES
from backend.store import Store
from backend.importing import Importer

class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'local.db',False);self.imp=Importer(self.store,False)
    def tearDown(self):self.temp.cleanup()
    def upload(self,text,filename='test.csv'):
        raw=text.encode('utf-8') if isinstance(text,str) else text
        return self.imp.upload({'filename':filename,'content_base64':base64.b64encode(raw).decode()})
    def preview(self,job,mapping=None,action='skip'):
        return self.imp.preview({'job_id':job['job_id'],'mapping':mapping or {'name':'公司','country':'国家'},'defaults':{},'duplicate_action':action})
    def confirm(self,job):return self.imp.confirm({'job_id':job['job_id'],'consent':True})
    def test_preview_no_write_bad_rows_and_duplicate(self):
        job=self.upload('公司,国家,邮箱\nAcme,DE,a@b.example\nacme,DE,b@b.example\n,US,invalid\nOther,US,invalid\n')
        p=self.preview(job,{'name':'公司','country':'国家','email':'邮箱'})
        self.assertEqual((p['valid_count'],p['duplicate_count'],p['invalid_count']),(1,1,2));self.assertEqual(self.store.metrics()['total'],0)
        r=self.confirm(job);self.assertEqual((r['inserted'],r['skipped'],r['failed']),(1,1,2))
        self.assertTrue(self.confirm(job)['already_committed']);self.assertEqual(self.store.metrics()['total'],1)
        restarted=Importer(self.store,False)
        self.assertTrue(restarted.confirm({'job_id':job['job_id'],'consent':True})['already_committed'])
        self.assertIn('invalid',self.imp.errors(job['job_id']).decode('utf-8'))
    def test_keep_same_name_never_overwrites(self):
        old=self.store.save_customer({'name':'Acme','type':TYPES[0],'stage':STAGES[0],'country':'DE','facts':'保留原有事实'},False)
        job=self.upload('公司,国家\nAcme,DE\n');self.preview(job,action='keep');self.confirm(job)
        self.assertEqual(self.store.metrics()['total'],2);self.assertEqual(self.store.customer(old['id'])['facts'],'保留原有事实')
    def test_confirm_concurrent_idempotence(self):
        job=self.upload('公司,国家\nAcme,DE\n');self.preview(job)
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:self.confirm(job),range(2)))
        self.assertEqual(sum(r['already_committed'] for r in results),1);self.assertEqual(self.store.metrics()['total'],1)
    def test_recheck_duplicate_after_preview(self):
        job=self.upload('公司,国家\nAcme,DE\n');self.preview(job)
        self.store.save_customer({'name':'Acme','country':'DE','type':TYPES[0],'stage':STAGES[0]},False)
        self.assertEqual(self.confirm(job)['skipped'],1)
    def test_consent_and_mapping_required(self):
        job=self.upload('公司,国家\nAcme,DE\n')
        for data in [{'job_id':job['job_id']},{'job_id':job['job_id'],'consent':True}]:
            with self.assertRaises(AppError):self.imp.confirm(data)
        with self.assertRaises(AppError):self.imp.preview({'job_id':job['job_id'],'mapping':{'country':'国家'}})
    def test_csv_error_download_formula_escape_and_line_numbers(self):
        job=self.upload('公司,国家,邮箱\n\n=WEBSERVICE("bad"),US,invalid\n')
        p=self.preview(job,{'name':'公司','country':'国家','email':'邮箱'})
        self.assertEqual(p['rows'][0]['row'],3)
        errors=self.imp.errors(job['job_id']).decode('utf-8-sig')
        self.assertIn("'=WEBSERVICE",errors)
    def test_xlsx_formulas_not_executed(self):
        from openpyxl import Workbook
        wb=Workbook();ws=wb.active;ws.append(['公司','国家']);ws.append(['XLSX客户','DE']);ws.append(['=1+1','DE']);raw=io.BytesIO();wb.save(raw)
        job=self.upload(raw.getvalue(),'test.xlsx');p=self.preview(job)
        self.assertEqual(p['valid_count'],1);self.assertEqual(p['invalid_count'],1);self.assertEqual(self.confirm(job)['inserted'],1)
    def test_upload_boundaries(self):
        for text,filename in [('x,y\n','test.xls'),('公司,公司\nA,B\n','test.csv'),('公司,国家\n','test.csv'),('a'* (5*1024*1024+1),'test.csv')]:
            with self.assertRaises(AppError):self.upload(text,filename)
    def test_pagination_filter_and_bulk_conflict(self):
        for i in range(45):self.store.save_customer({'name':f'工厂{i}','type':TYPES[i%3],'stage':STAGES[i%2],'country':'DE' if i%2 else 'US','product':'CNC 夹具'},False)
        first=self.store.customers({'page':'1','page_size':'20'});second=self.store.customers({'page':'2','page_size':'20'})
        self.assertEqual(first['total'],45);self.assertEqual(len(first['items']),20);self.assertFalse({x['id'] for x in first['items']}&{x['id'] for x in second['items']})
        self.assertEqual(self.store.customers({'country':'DE'})['total'],22)
        self.assertEqual(self.store.customers({'q':'工厂4'})['total'],6)
        items=[{'id':c['id'],'version':c['version']} for c in first['items'][:2]]
        self.store.save_customer(dict(first['items'][0],name='别的页面更新'),False)
        r=self.store.bulk({'items':items,'action':'priority','value':True});self.assertEqual(r['updated'],1);self.assertEqual(r['conflicts'],[items[0]['id']])
    def test_5000_synthetic_import_and_queries(self):
        output=io.StringIO();writer=csv.writer(output);writer.writerow(['公司','国家','客户类型','阶段','产品'])
        for i in range(5000):writer.writerow([f'合成客户{i:05d}','测试国A' if i%2 else '测试国B',TYPES[i%3],STAGES[i%4],'CNC 夹具'])
        start=time.perf_counter();job=self.upload(output.getvalue());p=self.preview(job,{'name':'公司','country':'国家','type':'客户类型','stage':'阶段','product':'产品'});self.assertEqual(p['valid_count'],5000);self.assertEqual(len(p['rows']),100);r=self.confirm(job);import_seconds=time.perf_counter()-start
        self.assertEqual(r['inserted'],5000)
        queries=[]
        for q in [{},{'page':'50','page_size':'100'},{'country':'测试国A'},{'q':'合成客户04999'},{'type':TYPES[0],'stage':STAGES[0]}]:
            start=time.perf_counter();result=self.store.customers(q);queries.append({'query':q,'total':result['total'],'returned':len(result['items']),'seconds':round(time.perf_counter()-start,4)});self.assertLessEqual(len(result['items']),100)
        self.assertEqual(queries[2]['total'],2500);self.assertEqual(queries[3]['total'],1)
        evidence=Path(__file__).resolve().parents[1]/'evidence';evidence.mkdir(exist_ok=True)
        (evidence/'bulk-5000-results.json').write_text(json.dumps({'synthetic_only':True,'customers':5000,'import_seconds':round(import_seconds,4),'queries':queries},ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':unittest.main()
