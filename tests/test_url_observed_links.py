import json,time,unittest
from backend.website_text import VisibleHTML
from pathlib import Path
from backend.optional_process import extract_controlled, DEFAULT_PYTHON

class ObservedQueryLinkTests(unittest.TestCase):
    @unittest.skipUnless(Path(DEFAULT_PYTHON).is_file(), 'Optional extraction runtime is not included in source repository')
    def test_existing_courlan_adapter_preserves_observed_query_meaning(self):
        text='<html><body><main><h1>Gear products</h1><p>We manufacture precision gears using CNC machining, gear hobbing and grinding for industrial machinery customers.</p><a href="/products?id=1#details">Products one</a><a href="/products?id=2">Products two</a><a href="/products?token=synthetic-secret">Products restricted</a><a href="/#/products/3">Products client route</a></main></body></html>'
        parser=VisibleHTML();parser.feed(text)
        result=extract_controlled(text.encode(),text,parser,'https://gear-works.com/?id=10',time.monotonic()+8)
        urls={c['url'] for c in result['link_discovery']['candidates']}
        self.assertIn('https://gear-works.com/products?id=1',urls);self.assertIn('https://gear-works.com/products?id=2',urls)
        self.assertFalse(any('token=' in url or '#/products/' in url for url in urls));self.assertNotIn('synthetic-secret',json.dumps(result))
        self.assertEqual(result['link_discovery']['requests'],0)
