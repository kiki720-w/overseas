"""Temporary image fixtures and SQLite only; no model or user data access."""
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch
import server
from backend.static_images import image_asset,MAX_IMAGE_BYTES,IMAGE_ROUTES
from backend.common import AppError

class ImageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.assets=self.root/'static'/'assets';self.assets.mkdir(parents=True)
        self.png=b'\x89PNG\r\n\x1a\nSYNTHETIC IMAGE FIXTURE'
        self.webp=b'RIFF\x10\x00\x00\x00WEBPSYNTHETIC'
        (self.assets/'silver-route-map.png').write_bytes(self.png)
        (self.assets/'silver-route-map.webp').write_bytes(self.webp)
        (self.root/'static'/'index.html').write_text('<html>synthetic page</html>')
        self.root_patch=patch.object(server,'ROOT',self.root);self.root_patch.start()
        store=server.Store(self.root/'synthetic.db',False)
        self.http=server.make_server(store,'local',port=0)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.http.server_port)
    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.thread.join();self.root_patch.stop();self.tmp.cleanup()
    def error(self,path,status):
        with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(self.base+path)
        self.assertEqual(e.exception.code,status)
    def test_exact_image_routes_types_and_headers(self):
        for path,mime,body in [('/assets/silver-route-map.png','image/png',self.png),('/assets/silver-route-map.webp','image/webp',self.webp)]:
            with urllib.request.urlopen(self.base+path) as r:
                self.assertEqual(r.read(),body);self.assertEqual(r.headers['Content-Type'],mime)
                self.assertEqual(r.headers['X-Content-Type-Options'],'nosniff')
                self.assertEqual(r.headers['Cross-Origin-Resource-Policy'],'same-origin')
                self.assertEqual(int(r.headers['Content-Length']),len(body))
        with urllib.request.urlopen(self.base+'/assets/silver-route-map.png?file=server.py') as r:self.assertEqual(r.read(),self.png)
    def test_confirmed_artwork_allowlist_each_route_and_missing_assets(self):
        names=('silver-route-map.png','overseas-sail.png','nav-development.png','nav-deals.png','nav-settings.png','compass.png','precision-fixture.png','row-arrow.png')
        self.assertEqual(set(IMAGE_ROUTES),{'/assets/'+name for name in names}|{'/assets/silver-route-map.webp'})
        for name in names:
            path=self.assets/name
            if not path.exists():self.error('/assets/'+name,404)
            path.write_bytes(self.png)
            with urllib.request.urlopen(self.base+'/assets/'+name) as r:
                self.assertEqual(r.headers['Content-Type'],'image/png');self.assertEqual(r.read(),self.png)
            path.write_bytes(self.webp)
            self.error('/assets/'+name,415)
            path.unlink()
            self.error('/assets/'+name,404)
        for route in ('/assets/nav-deals.svg','/assets/compass.webp','/assets/precision-fixture.PNG','/assets/subdir/row-arrow.png','/assets/../nav-settings.png','/assets/%2e%2e/nav-settings.png'):
            self.error(route,404)
    def test_csp_only_adds_self_image_directive(self):
        with urllib.request.urlopen(self.base+'/') as r:csp=r.headers['Content-Security-Policy']
        self.assertEqual(csp,"default-src 'self'; img-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
        for forbidden in ('data:','blob:','unsafe-inline','unsafe-eval','https:'):self.assertNotIn(forbidden,csp)
    def test_traversal_arbitrary_files_encodings_and_types_unavailable(self):
        for route in ['/assets/../server.py','/assets/%2e%2e/server.py','/assets/%252e%252e/server.py','/assets/..%5cserver.py','/assets/silver-route-map.png/..','/assets/%73ilver-route-map.png','/assets/SILVER-ROUTE-MAP.png','/assets/.env','/assets/other.png','/assets/silver-route-map.svg','/static/assets/silver-route-map.png','/assets/']:
            self.error(route,404)
        (self.assets/'silver-route-map.png').write_text('<html>not an image</html>')
        self.error('/assets/silver-route-map.png',415)
        (self.assets/'silver-route-map.webp').write_bytes(self.png)
        self.error('/assets/silver-route-map.webp',415)
        (self.assets/'silver-route-map.png').unlink()
        self.error('/assets/silver-route-map.png',404)
    def test_oversized_and_resolved_escape_rejected(self):
        (self.assets/'silver-route-map.png').write_bytes(self.png+b'x'*MAX_IMAGE_BYTES)
        self.error('/assets/silver-route-map.png',413)
        # Simulates a symlink/junction resolution without Windows symlink privilege.
        original=Path.resolve;outside=self.root/'outside.png'
        def resolve(path,*args,**kwargs):
            return outside if path.name=='silver-route-map.png' else original(path,*args,**kwargs)
        with patch.object(Path,'resolve',resolve):
            with self.assertRaises(AppError) as e:image_asset(self.root,'/assets/silver-route-map.png')
            self.assertEqual(e.exception.status,404)

if __name__=='__main__':unittest.main()
