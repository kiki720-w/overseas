import copy
import hashlib
import json
import time
import tempfile
import threading
from backend.production_runtime import ClosedStore
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from types import SimpleNamespace
from backend import buyer_search
from backend.common import AppError
from backend.discovery_quality import classify_page
from backend.optional_process import extract_controlled,DEFAULT_PYTHON
from backend.optional_reply import validate_links
from backend.search_plan import SearchPlans
from backend.search import compose_query
from backend.website_text import VisibleHTML,evidence_lines

BODY=('We provide precision gear hobbing services for custom gears and splines. '
      'Our shop manufactures gears for industrial machinery using CNC machining and grinding. ')
HTML=('''<html><head><title>Synthetic Gear Works</title></head><body><nav>
<a href="/products/">Products</a><a href="/capabilities/">Capabilities</a>
<a href="/about/">About us</a><a href="/products/">Duplicate</a></nav>
<main><h1>Gear machining capabilities</h1><p>'''+BODY+'''</p>
<p>'''+BODY.replace('gears','gear components')+'''</p>
<a href="https://machine-tools.com/">Official website</a>
<div hidden><a href="/products/secret">Products hidden</a></div>
<a href="http://127.0.0.1/products/">Products private</a>
<a href="https://user:pass@machine-tools.com/products/">Products credentials</a>
<a href="javascript:alert(1)">Products invalid</a></main></body></html>''')

class BuyerReuseTests(unittest.TestCase):
    def test_process_plan_and_custom_industry_no_calls(self):
        provider=Mock();provider.compose_query=compose_query;provider.settings.return_value={}
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        service=SimpleNamespace(search_provider=provider,store=ClosedStore(Path(temporary.name)/'synthetic.sqlite3',False),jobs={},lock=threading.RLock())
        plans=SearchPlans(service)
        plan=plans.create({'query':'gear workholding manufacturer','products':'gear fixture','countries':'Germany','max_queries':8})
        self.assertTrue(plan['buyer_intent']['enabled'])
        all_queries=' '.join(q['executed_query'] for q in plan['queries'])
        self.assertIn('hobbing',all_queries);self.assertIn('grinding',all_queries)
        self.assertNotIn('fixture',all_queries);self.assertNotIn('workholding manufacturer',all_queries)
        self.assertIn('Germany',all_queries);provider.search.assert_not_called()
        plans.stop({'plan_id':plan['plan_id']})
        custom=plans.create({'query':'my products','products':'industrial coatings','buyer_intent':{'process_terms':['metal finishing services','powder coating services']}})
        self.assertEqual(custom['buyer_intent']['basis'],'user_configured_processes')
        self.assertIn('metal finishing',custom['queries'][0]['executed_query'])

    def test_as_entered_and_unknown_remain_explicit(self):
        data={'query':'gear fixture manufacturer','products':'gear fixture','buyer_intent':{'target':'as_entered'}}
        intent=buyer_search.resolve(data)
        self.assertFalse(intent['enabled']);self.assertEqual(buyer_search.request(data,intent),data)
        self.assertFalse(buyer_search.resolve({'query':'unknown','products':'unknown'})['enabled'])
        for config in ({'process_terms':['x']},{'profile':'invented'},{'profile':[]},{'process_terms':'bad'},{'target':'invented'}):
            with self.assertRaises(AppError):buyer_search.resolve({'query':'test','buyer_intent':config})

    def test_markdown_cannot_reconstruct_html_or_start_worker(self):
        with patch('backend.website.run_website_worker') as worker:
            with self.assertRaises(AppError) as error:extract_controlled(None,'x'*173,None,'https://gear-works.com/',time.monotonic()+8)
            self.assertEqual(error.exception.code,'raw_html_unavailable');worker.assert_not_called()

    def test_directory_sources_stay_directory(self):
        for url in ('https://crustdata.com/profiles/company/5th-axis','https://directory.imts.com/8_0/exhibitor/exhibitor-details.cfm','https://www.bloomberg.com/profile/company/123:US'):
            self.assertEqual(classify_page(url,'Synthetic company','')['kind'],'directory')

    @unittest.skipUnless(Path(DEFAULT_PYTHON).is_file(), 'Optional extraction runtime is not included in source repository')
    def test_controlled_saved_html_extraction_links_and_resource_evidence(self):
        self.assertTrue(Path(DEFAULT_PYTHON).is_file(),'Development-only Python3.13 dependency must be explicit')
        parser=VisibleHTML();parser.feed(HTML)
        start=time.perf_counter()
        value=extract_controlled(HTML.encode(),HTML,parser,'https://gear-works.com/',time.monotonic()+8)
        elapsed=time.perf_counter()-start
        links=value['link_discovery'];validate_links(links)
        urls={item['url'] for item in links['candidates']}
        self.assertTrue({'https://gear-works.com/products/','https://gear-works.com/capabilities/','https://gear-works.com/about/'}<=urls)
        self.assertIn('https://machine-tools.com/',urls)
        self.assertFalse(any('secret' in u or '127.0.0.1' in u or '@' in u for u in urls))
        self.assertEqual(links['requests'],0)
        for item in links['candidates']:
            self.assertFalse(item['official_site_verified']);self.assertFalse(item['entity_identity_verified'])
            self.assertEqual(item['source_html_sha256'],hashlib.sha256(HTML.encode()).hexdigest())
        bad=copy.deepcopy(links);bad['candidates'][0]['official_site_verified']=True
        with self.assertRaises(AppError):validate_links(bad)
        evidence={'fixture':'synthetic HTML only','raw_bytes':len(HTML.encode()),'elapsed_seconds':elapsed,
                  'native_visible_lines':evidence_lines(parser)[0],'optional':value,
                  'runtime':DEFAULT_PYTHON,'distribution_ready':False,'network_requests':0}
        Path('evidence/buyer-reuse-comparison.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':unittest.main()
