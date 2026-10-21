"""Synthetic DB HTTP regression for every script that research.html declares."""
import gc
from html.parser import HTMLParser
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
from urllib.parse import urlsplit
from unittest.mock import patch
import server

class Scripts(HTMLParser):
    def __init__(self):super().__init__();self.sources=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='script' and attrs.get('src'):self.sources.append(attrs['src'])

class ResearchStaticRoutes(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=server.Store(self.root/'synthetic.sqlite3',False)
        self.frontend=Path(server.ROOT)/'runtime/frontend/static'
        if not self.frontend.is_dir():self.frontend=Path(server.ROOT)/'static'
        self.static_patch=patch.object(server,'STATIC_ROOT',self.frontend);self.static_patch.start()
        self.http=server.make_server(self.store,'demo',False,0)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.http.server_port)
    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.thread.join(2);self.static_patch.stop();gc.collect();self.temp.cleanup()
    def test_all_research_declared_script_resources_load(self):
        # Homepage and operational results deliberately have separate initializers.
        for route,required in [('/research','/research-home.js'),('/research/results','/research-known.js')]:
            with urllib.request.urlopen(self.base+route) as response:html=response.read().decode('utf-8')
            parser=Scripts();parser.feed(html);self.assertTrue(parser.sources)
            self.assertTrue(any(urlsplit(path).path==required for path in parser.sources),(route,required))
            for path in parser.sources:
                with self.subTest(page=route,path=path),urllib.request.urlopen(self.base+path) as response:
                    self.assertEqual(response.status,200);self.assertIn('javascript',response.headers['Content-Type'])
                    self.assertEqual(response.read(),(self.frontend/urlsplit(path).path.lstrip('/')).read_bytes())
    def check_declared_assets(self,frontend):
        class Assets(HTMLParser):
            def __init__(self):super().__init__();self.items=[]
            def handle_starttag(self,tag,attrs):
                attrs=dict(attrs)
                if tag=='script' and attrs.get('src'):self.items.append((attrs['src'],'javascript'))
                if tag=='link' and 'stylesheet' in attrs.get('rel','').split() and attrs.get('href'):self.items.append((attrs['href'],'css'))
        with patch.object(server,'STATIC_ROOT',frontend):
            for route in ['/','/deals','/settings','/knowledge','/research']:
                with urllib.request.urlopen(self.base+route) as response:html=response.read().decode('utf-8')
                parser=Assets();parser.feed(html);self.assertTrue(parser.items,route)
                for path,mime in parser.items:
                    with self.subTest(root=str(frontend),page=route,asset=path):
                        self.assertTrue(path.startswith('/'), 'Only local declared assets are allowed')
                        with urllib.request.urlopen(self.base+path) as response:
                            self.assertEqual(response.status,200);self.assertIn(mime,response.headers['Content-Type'])
                            self.assertEqual(response.read(),(frontend/urlsplit(path).path.lstrip('/')).read_bytes())
    def test_published_five_page_scripts_and_styles_load(self):
        self.check_declared_assets(self.frontend)
    def test_shared_five_page_scripts_and_styles_load_without_changes(self):
        self.check_declared_assets(Path(server.ROOT)/'static')
    def test_versioned_known_resource_serves_complete_initializer(self):
        with urllib.request.urlopen(self.base+'/research-known.js?v=20261005-search2') as response:
            self.assertEqual(response.status,200);self.assertIn('javascript',response.headers['Content-Type'])
            body=response.read().decode('utf-8')
        self.assertEqual(body.encode('utf-8'),(self.frontend/'research-known.js').read_bytes())
        self.assertIn('async function initKnown()',body);self.assertIn("tools.addEventListener('toggle'",body)

if __name__=='__main__':unittest.main()
