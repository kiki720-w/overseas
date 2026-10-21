import ast,copy,json,socket,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from backend.common import AppError
from backend.webpage_url import inspect_page_url,normalize_page_url
from backend.website_fetch import Collector,fetch_public,public_ips,normalize_url
from backend.deepseek_tools import DeepSeekTools
from backend.search_identity import source_identity
from backend.discovery_quality import classify_page
from backend.entity_candidates import summarize
from test_deepseek_tools import row

class WebpageURLTests(unittest.TestCase):
    def test_ordinary_anchor_display_and_document_identity(self):
        p=inspect_page_url('https://gear.example/about#capabilities')
        self.assertEqual(p['display_url'],'https://gear.example/about#capabilities');self.assertEqual(p['fetch_url'],'https://gear.example/about');self.assertTrue(p['ordinary_anchor_removed_for_fetch'])
        self.assertEqual(source_identity(p['display_url'],'Company')['key'],source_identity('https://gear.example/about#products','Company')['key'])
    def test_hash_routes_not_silently_stripped_or_conflated(self):
        for fragment in ('/company/1','!/company/1','/company?id=1','section?view=details'):
            url='https://gear.example/#'+fragment;p=inspect_page_url(url);self.assertIsNone(p['fetch_url']);self.assertEqual(p['fragment_kind'],'client_route_or_ambiguous')
            with self.assertRaises(AppError):normalize_page_url(url)
        self.assertNotEqual(source_identity('https://gear.example/#/company/1','Company')['key'],source_identity('https://gear.example/#/company/2','Company')['key'])
    def test_query_semantics_order_duplicates_and_blank_values_retained(self):
        url='https://gear.example/products?id=1&id=2&lang=de&q=CNC+gear&page='
        self.assertEqual(normalize_page_url(url),url)
        self.assertNotEqual(source_identity('https://gear.example/?id=1','Company')['key'],source_identity('https://gear.example/?id=2','Company')['key'])
        self.assertNotEqual(source_identity('https://gear.example/?id=1&lang=de','Company')['key'],source_identity('https://gear.example/?lang=de&id=1','Company')['key'])
    def test_unicode_and_percent_encoded_paths_and_queries(self):
        self.assertEqual(normalize_page_url('https://gear.example/齿轮?q=磨齿'),'https://gear.example/%E9%BD%BF%E8%BD%AE?q=%E7%A3%A8%E9%BD%BF')
        self.assertEqual(normalize_page_url('https://gear.example/Zahnr%C3%A4der?id=42#products'),'https://gear.example/Zahnr%C3%A4der?id=42')
    def test_sensitive_queries_unknown_names_and_routes_are_rejected(self):
        for query in ('token=synthetic-secret','Api_Key=synthetic-secret','%61ccess_token=synthetic-secret','password=synthetic-secret','signature=synthetic-secret','sessionid=synthetic-secret','code=synthetic-secret','next=https%3A%2F%2F127.0.0.1%2F','id=http%3A%2F%2F127.0.0.1%2F','id=%252fprivate','unreviewed=42','q=token%3Dsynthetic-secret'):
            with self.subTest(query=query),self.assertRaises(AppError):inspect_page_url('https://gear.example/?'+query)
        for fragment in ('access_token=synthetic-secret','/company?token=synthetic-secret','https://127.0.0.1/private','abcdefghij.abcdefghij.abcdefghij'):
            with self.assertRaises(AppError):inspect_page_url('https://gear.example/#'+fragment)
    def test_original_authority_protocol_private_and_encoding_boundaries(self):
        for url in ('file:///etc/passwd','http://localhost/?id=1','https://a.internal/#products','http://127.0.0.1/#products','http://169.254.169.254/?id=1','http://2130706433/#products','https://user:synthetic-secret@gear.example/?id=1','https://gear.example:8443/#products','https://gear.example/%0d%0aHost:evil','https://gear.example/%2e%2e/private','https://gear.example/%252e%252e/private','https://gear.example/%2fprivate','https://gear.example/%ZZ','https://gear.example/?id=%FF'):
            with self.subTest(url=url),self.assertRaises(AppError):inspect_page_url(url)
    def test_safe_fetch_transmits_exact_query_without_fragment_and_keeps_pin(self):
        conn=Mock();response=Mock();response.status=200;response.getheaders.return_value=[('Content-Type','text/html')];response.read1.side_effect=[b'body',b''];conn.getresponse.return_value=response
        resolver=Mock(return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443))]);factory=Mock(return_value=conn)
        fetch_public('https://gear.example/products?id=42&lang=de#details',time.monotonic()+5,resolver,factory)
        self.assertEqual(conn.request.call_args.args[:2],('GET','/products?id=42&lang=de'));resolver.assert_called_once();factory.assert_called_once_with('https://gear.example/products?id=42&lang=de','8.8.8.8')
        self.assertNotIn('Authorization',conn.request.call_args.kwargs['headers']);self.assertNotIn('Cookie',conn.request.call_args.kwargs['headers'])
    def test_dns_mixed_public_private_still_rejected_with_query(self):
        resolver=Mock(return_value=[(2,1,6,'',('8.8.8.8',443)),(2,1,6,'',('127.0.0.1',443))]);factory=Mock()
        with self.assertRaises(AppError):fetch_public('https://gear.example/?id=1',time.monotonic()+5,resolver,factory)
        factory.assert_not_called()
        with self.assertRaises(AppError) as error:inspect_page_url('http://127.0.0.1/?id=42')
        from backend.url_rejection_diagnostics import explain
        self.assertEqual(explain('http://127.0.0.1/?id=42',error.exception)['reason_detail'],'page_url_authority_rejected')
    def test_redirect_rechecks_sensitive_private_cross_host_and_downgrade(self):
        for target in ('http://127.0.0.1/private','https://other.example/?id=1','http://gear.example/?id=1','https://gear.example/?token=synthetic-secret','https://gear.example/#/company/1'):
            calls=[]
            def fetch(url,deadline):
                calls.append(url)
                if url.endswith('/robots.txt'):return 200,{'content-type':'text/plain'},b'User-agent: *\nDisallow:'
                return 302,{'location':target},b''
            with self.subTest(target=target),self.assertRaises(AppError):Collector(fetch).collect('https://gear.example/start?id=1')
            self.assertEqual(calls,['https://gear.example/robots.txt','https://gear.example/start?id=1'])
    def test_same_host_redirect_query_is_preserved_and_robots_checked(self):
        calls=[]
        def fetch(url,deadline):
            calls.append(url)
            if url.endswith('/robots.txt'):return 200,{'content-type':'text/plain'},b'User-agent: *\nDisallow: /private'
            if '/start' in url:return 302,{'location':'/products?id=42#details'},b''
            return 200,{'content-type':'text/html'},b'<main><p>We manufacture precision gears using CNC machining and gear grinding for industrial customers.</p></main>'
        value=Collector(fetch).collect('https://gear.example/start?id=1');self.assertEqual(value['pages'][0]['url'],'https://gear.example/products?id=42');self.assertEqual(calls[-1],'https://gear.example/products?id=42')
        def private_redirect(url,deadline):
            if url.endswith('/robots.txt'):return 200,{'content-type':'text/plain'},b'User-agent: *\nDisallow: /private'
            return 302,{'location':'/private?id=42'},b''
        with self.assertRaises(AppError):Collector(private_redirect).collect('https://gear.example/start?id=1')
    def test_provider_keeps_display_hash_route_but_fetch_never_calls_transport(self):
        transport=Mock(return_value=(200,{'results':[row('https://gear.example/?id=42#products'),row('https://gear.example/#/company/1')]}));provider=DeepSeekTools(transport,key='synthetic',cooldown=0)
        with patch('socket.getaddrinfo',side_effect=AssertionError('No DNS')):result=provider.search({'query':'offline','count':5})
        self.assertEqual(len(result['results']),2);transport.reset_mock()
        with self.assertRaises(AppError):provider.fetch({'url':'https://gear.example/#/company/1','consent':True})
        transport.assert_not_called()
    def test_wlw_list_is_directory_never_single_company(self):
        url='https://www.wlw.be/de/suche/antriebselemente/dortmund?id=1';classification=classify_page(url,'Antriebselemente Dortmund','Multiple manufacturers and components')
        self.assertEqual(classification['kind'],'directory');self.assertEqual(classification['listing_scope'],'multiple_entities_or_search_results');self.assertFalse(classification['single_company_candidate'])
        candidate={'candidate_id':'a','url':url,'source_classification':classification,'search_provenance':{'source_url':url,'summary':'## Paul Pilgrim GmbH\nEckendorfer Str. 71\n33609 Bielefeld'}}
        self.assertEqual(summarize([candidate])['entity_candidate_count'],0)
        self.assertNotEqual(classify_page('https://wlw.be.attacker.example/de/suche/a','Listing','')['kind'],'directory')
    def test_sensitive_and_hash_routes_do_not_reach_collector_or_paid_fetch(self):
        for url in ('https://gear.example/?token=synthetic-secret','https://gear.example/#/factory/42'):
            native=Mock();paid=Mock();provider=DeepSeekTools(paid,key='synthetic')
            with self.assertRaises(AppError):Collector(native).collect(url)
            with self.assertRaises(AppError):provider.fetch({'url':url,'consent':True})
            native.assert_not_called();paid.assert_not_called()
    def test_official_fetch_sends_query_and_strips_only_ordinary_anchor(self):
        transport=Mock(return_value=(200,{'title':'Synthetic factory','content':'We manufacture gears using precision CNC machining. '*12}));provider=DeepSeekTools(transport,key='synthetic',cooldown=0)
        result=provider.fetch({'url':'https://gear.example/product?id=42#specifications','consent':True})
        self.assertEqual(transport.call_args.args[1],{'url':'https://gear.example/product?id=42'})
        self.assertEqual(result['url_policy']['display_url'],'https://gear.example/product?id=42#specifications')
