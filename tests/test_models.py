# -*- coding: utf-8 -*-
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server
from backend.common import AppError
from backend.models import ModelService
from backend.model_transport import normalize_endpoint, public_addresses, HTTPSJSONTransport, provider_error
from backend.model_analysis import validate_analysis, INSTRUCTION

SYNTHETIC_KEY='sk-SYNTHETIC-only-not-a-real-key'
def resolver(host,port,**kwargs):return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))]
def output(**changes):
    value={'match':'品类需核实','angle':'请确认机床接口','next_step':'先确认加工规格','draft':'Subject: Clarifying requirements\nHello, could you share the machine interface?','unknowns':'数量未知','stage_blocker':'规格待确认','notice':'待审核','facts':[],'hypotheses':['可能需要夹具，尚未核实'],'questions':['需要什么接口？']}
    value.update(changes);return value
def completion(value):return {'choices':[{'finish_reason':'stop','message':{'content':value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)}}]}

class Stub:
    def __init__(self):self.calls=[];self.result=completion(output());self.error=None;self.before=None
    def __call__(self,base,path,key,payload,addresses,task):
        self.calls.append({'base':base,'path':path,'payload':payload,'task':task})
        if self.before:self.before()
        if self.error:raise self.error
        return self.result

class ModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=server.Store(Path(self.tmp.name)/'local.db',False)
        self.stub=Stub();self.service=ModelService(self.store,'local',self.stub,resolver)
        self.customer=self.store.save_customer({'name':'合成客户A','type':'终端加工厂','stage':'待开发','facts':'使用合成接口A','product':'CNC 夹具'},False)
        self.other=self.store.save_customer({'name':'合成客户B不得发送','type':'经销商','stage':'待开发'},False)
        self.record=self.store.record({'customer_id':self.customer['id'],'kind':'沟通','text':'请提供合成接口图纸'} )
        self.other_record=self.store.record({'customer_id':self.other['id'],'kind':'沟通','text':'其他客户秘密合成文本不得发送'})
    def tearDown(self):self.tmp.cleanup()
    def configured(self,**extra):
        data={'provider':'deepseek','base_url':'https://api.deepseek.com','model':'synthetic-model-A','api_key':SYNTHETIC_KEY};data.update(extra);return self.service.save(data)
    def request(self):return {'consent':True,'revision':self.service.settings()['revision']}
    def analysis_request(self):return dict(self.request(),context_token=self.service.context(self.customer['id'])['context_token'])
    def error_code(self,code,action,status=None):
        with self.assertRaises(AppError) as caught:action()
        self.assertEqual(caught.exception.code,code)
        if status:self.assertEqual(caught.exception.status,status)
        self.assertNotIn(SYNTHETIC_KEY,str(caught.exception))
    def test_presets_manual_model_and_no_automatic_request(self):
        for p,url in [('deepseek','https://api.deepseek.com/v1'),('kimi-cn','https://api.moonshot.cn/v1'),('kimi-global','https://api.moonshot.ai/v1'),('custom','https://compatible.example/v1')]:
            value=self.configured(provider=p,base_url=url,model='manually-chosen-2026')
            self.assertEqual(value['model'],'manually-chosen-2026');self.assertTrue(value['configured'])
        self.assertEqual(self.stub.calls,[])
    def test_memory_key_nonsecret_config_restart_and_no_echo(self):
        settings=self.configured();self.assertNotIn(SYNTHETIC_KEY,json.dumps(settings))
        self.stub.result=completion('OK');self.service.test(self.request())
        self.assertNotIn(SYNTHETIC_KEY,json.dumps(self.store.calls({})))
        reopened=ModelService(self.store,'local',self.stub,resolver).settings()
        self.assertEqual(reopened['model'],'synthetic-model-A');self.assertFalse(reopened['configured']);self.assertEqual(reopened['last_status'],'key_required');self.assertEqual(reopened['generation_status'],'not_tested')
        with self.store.db() as db:stored=db.execute('select data from model_config').fetchone()[0]
        self.assertNotIn(SYNTHETIC_KEY,stored);self.assertNotIn(SYNTHETIC_KEY,repr(self.service.snapshot(self.request())))
    def test_host_or_provider_change_cannot_reuse_old_key(self):
        self.configured();v=self.service.save({'provider':'custom','base_url':'https://different.example/v1','model':'other'})
        self.assertFalse(v['configured']);self.error_code('key_required',lambda:self.service.models(self.request()))
    def test_model_change_keeps_key_resets_validation(self):
        self.configured();self.stub.result=completion('OK');self.service.test(self.request())
        v=self.service.save({'provider':'deepseek','base_url':'https://api.deepseek.com','model':'synthetic-model-B'})
        self.assertTrue(v['configured']);self.assertEqual(v['generation_status'],'not_tested')
    def test_connection_name_is_nonsecret_bounded_and_persists(self):
        name='合成连接 <b>仅文本</b>'
        self.assertEqual(self.configured(name=name)['name'],name)
        self.assertEqual(ModelService(self.store,'local',self.stub,resolver).settings()['name'],name)
        value=self.service.save({'provider':'deepseek','base_url':'https://api.deepseek.com','model':'next-model'})
        self.assertEqual(value['name'],name)
        self.error_code('invalid_input',lambda:self.configured(name='x'*81))
        self.error_code('invalid_input',lambda:self.configured(name=SYNTHETIC_KEY))
    def test_clear_key_and_unavailable_persistence(self):
        self.configured();self.error_code('key_persistence_unavailable',lambda:self.configured(persist=True))
        v=self.service.save({'provider':'deepseek','base_url':'https://api.deepseek.com','model':'A','clear_key':True});self.assertFalse(v['configured'])
    def test_validation_and_consent_and_cas(self):
        self.configured();self.error_code('consent_required',lambda:self.service.test({'revision':1}))
        self.error_code('profile_changed',lambda:self.service.test({'consent':True,'revision':0}),409)
        self.error_code('profile_changed',lambda:self.configured(revision=0),409)
        self.error_code('invalid_input',lambda:self.configured(api_key='header\nattack'))
        self.error_code('invalid_input',lambda:self.configured(model=SYNTHETIC_KEY))
        self.error_code('endpoint_invalid',lambda:self.configured(base_url='https://different.example/v1'))
    def test_discovery_auth_and_generation_are_distinct(self):
        self.configured(model='');self.stub.result={'data':[{'id':'dynamic-model-a','name':'Dynamic A'},{'id':'dynamic-model-b'}]}
        v=self.service.models(self.request());self.assertEqual(len(v['models']),2)
        s=self.service.settings();self.assertEqual(s['models_status'],'ok');self.assertEqual(s['generation_status'],'not_tested');self.assertEqual(s['model'],'')
        self.assertIsNone(self.stub.calls[-1]['payload']);self.error_code('model_required',lambda:self.service.test(self.request()))
    def test_models_unsupported_still_allows_manual_generation(self):
        self.configured();self.stub.error=provider_error(404,'models')
        self.error_code('models_unsupported',lambda:self.service.models(self.request()),404)
        self.assertEqual(self.service.settings()['models_status'],'unsupported')
        self.stub.error=None;self.stub.result=completion('OK');self.assertEqual(self.service.test(self.request())['status'],'ok')
    def test_provider_status_mapping_no_raw_errors(self):
        self.configured()
        for status,code in [(401,'provider_auth'),(403,'provider_forbidden'),(404,'model_not_found'),(429,'provider_rate_limit'),(500,'provider_unavailable'),(302,'redirect_blocked')]:
            self.stub.error=provider_error(status,'generation_test')
            self.error_code(code,lambda:self.service.test(self.request()))
        self.stub.error=AppError('供应商请求超时',504,'provider_timeout');self.error_code('provider_timeout',lambda:self.service.test(self.request()),504)
    def test_empty_invalid_truncated_and_long_completion(self):
        self.configured()
        for bad in [{},{'choices':[]},completion(''),completion('x'*20001),{'choices':[{'finish_reason':'length','message':{'content':'OK'}}]}]:
            self.stub.result=bad;self.error_code('invalid_response',lambda:self.service.test(self.request()),502)
        for bad in ['not json','[]','{}',json.dumps(output(draft='x'*6001))]:
            self.stub.result=completion(bad);self.error_code('invalid_response',lambda:self.service.analyze(self.customer['id'],self.analysis_request()),502)
    def test_models_invalid_and_secret_echo_rejected(self):
        self.configured()
        for value in [{'data':[]},{'data':[{'id':'<img onerror=alert(1)>'}]},{'data':[{'id':'ok','name':SYNTHETIC_KEY}]},{'data':'bad'}]:
            self.stub.result=value;self.error_code('invalid_response',lambda:self.service.models(self.request()))
    def test_success_selected_customer_only_with_verified_record_evidence(self):
        self.configured();self.stub.result=completion(output(facts=[{'statement':'fake unrestricted conclusion','evidence':[{'source':'record','record_id':self.record['id'],'quote':'合成接口图纸'}]}]))
        result=self.service.analyze(self.customer['id'],self.analysis_request())
        self.assertEqual(result['mode'],'model');self.assertEqual(result['facts'][0]['statement'],'用户记录：合成接口图纸')
        raw=json.dumps(self.stub.calls[-1]['payload'],ensure_ascii=False)
        self.assertNotIn(self.other['name'],raw);self.assertNotIn(self.other_record['text'],raw);self.assertIn(INSTRUCTION, self.stub.calls[-1]['payload']['messages'][0]['content'])
        self.assertEqual(self.store.customer(self.customer['id'])['stage'],'待开发');self.assertEqual(len(self.store.records(self.customer['id'],{})['items']),1)
    def test_wrong_customer_reference_and_fabricated_quote_rejected(self):
        self.configured()
        for evidence in [[{'source':'record','record_id':self.other_record['id'],'quote':self.other_record['text']}],[{'source':'customer','field':'facts','quote':'invented fact'}],[{'source':'customer','field':'unknowns','quote':'unknown'}]]:
            self.stub.result=completion(output(facts=[{'statement':'incorrect','evidence':evidence}]))
            self.error_code('invalid_evidence',lambda:self.service.analyze(self.customer['id'],self.analysis_request()),502)
    def test_missing_evidence_is_unknown_not_a_fact(self):
        self.configured();self.stub.result=completion(output(facts=[{'statement':'采购量已确定','evidence':[]}]))
        result=self.service.analyze(self.customer['id'],self.analysis_request());self.assertEqual(result['facts'],[]);self.assertIn('待核实：采购量已确定',result['questions'])
    def test_unsupported_commercial_promises_are_rejected(self):
        self.configured()
        for value in [output(draft='We deliver within 10 days.'),output(next_step='Offer USD 100 per unit'),output(draft='We are ISO 9001 certified.')]:
            self.stub.result=completion(value);self.error_code('unsafe_output',lambda:self.service.analyze(self.customer['id'],self.analysis_request()))
    def test_html_remains_plain_text_not_interpreted_by_backend(self):
        self.configured();html='<img src=x onerror=alert(1)><script>window.injected=1</script>'
        self.stub.result=completion(output(angle=html,draft=html))
        self.assertEqual(self.service.analyze(self.customer['id'],self.analysis_request())['draft'],html)
    def test_demo_never_transmits_context(self):
        self.configured();self.service.mode='demo'
        self.assertFalse(self.service.context(self.customer['id'])['real_allowed'])
        self.error_code('demo_data_forbidden',lambda:self.service.analyze(self.customer['id'],self.analysis_request()))
        self.assertEqual(self.stub.calls,[])
    def test_context_change_requires_new_confirmation(self):
        self.configured();request=self.analysis_request();self.store.record({'customer_id':self.customer['id'],'kind':'反馈','text':'new input'})
        self.error_code('context_changed',lambda:self.service.analyze(self.customer['id'],request),409);self.assertEqual(self.stub.calls,[])
    def test_context_change_during_request_discards_result(self):
        self.configured();request=self.analysis_request()
        self.stub.before=lambda:self.store.record({'customer_id':self.customer['id'],'kind':'反馈','text':'changed during request'})
        self.error_code('context_changed',lambda:self.service.analyze(self.customer['id'],request),409)
    def test_switch_model_during_request_discards_old_response(self):
        self.configured();request=self.request();self.stub.result=completion('OK')
        self.stub.before=lambda:self.service.save({'provider':'deepseek','base_url':'https://api.deepseek.com','model':'synthetic-model-B'})
        self.error_code('profile_changed',lambda:self.service.test(request),409)
        self.assertEqual(self.service.settings()['generation_status'],'not_tested');self.assertEqual(self.service.settings()['model'],'synthetic-model-B')
    def test_context_too_large_is_not_silently_truncated(self):
        self.configured()
        for n in range(5):self.store.record({'customer_id':self.customer['id'],'kind':'沟通','text':'合成'*5000})
        self.error_code('context_too_large',lambda:self.service.context(self.customer['id']),413);self.assertEqual(self.stub.calls,[])
    def test_https_and_private_dns_are_rejected(self):
        for endpoint in ['http://api.example/v1','https://user:pass@api.example/v1','https://127.0.0.1/v1','https://169.254.169.254','https://[::1]','https://api.example:8443/v1','https://api.example/v1?q=1','https://api.example/../v1','https://metadata.google.internal','https://example.local']:
            with self.assertRaises(AppError):normalize_endpoint(endpoint)
        for ip in ['127.0.0.1','10.0.0.1','169.254.169.254','224.0.0.1','::1','fc00::1','ff02::1','::ffff:127.0.0.1','2002:7f00:1::']:
            bad=lambda *a,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,443))]
            self.error_code('unsafe_endpoint',lambda:public_addresses('https://public.example',bad))
        mixed=lambda *a,**kw:resolver(*a,**kw)+[(socket.AF_INET,socket.SOCK_STREAM,6,'',('192.168.1.1',443))]
        self.error_code('unsafe_endpoint',lambda:public_addresses('https://public.example',mixed))
    def test_dns_rechecked_each_request(self):
        self.configured();self.service.resolver=lambda *a,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]
        self.error_code('unsafe_endpoint',lambda:self.service.test(self.request()));self.assertEqual(self.stub.calls,[])
    def test_transport_maps_timeout_and_rejects_redirects(self):
        transport=HTTPSJSONTransport()
        with patch('backend.model_transport.PinnedHTTPSConnection') as cls:
            cls.return_value.request.side_effect=socket.timeout()
            self.error_code('provider_timeout',lambda:transport('https://public.example','models',SYNTHETIC_KEY,None,['93.184.216.34'],'models'),504)
            cls.return_value.request.side_effect=None;cls.return_value.getresponse.return_value.status=302
            self.error_code('redirect_blocked',lambda:transport('https://public.example','models',SYNTHETIC_KEY,None,['93.184.216.34'],'models'))
            self.assertEqual(cls.return_value.request.call_count,2)
    def test_transport_rejects_empty_invalid_and_oversized_json(self):
        transport=HTTPSJSONTransport()
        with patch('backend.model_transport.PinnedHTTPSConnection') as cls:
            cls.return_value.getresponse.return_value.status=200
            for raw in [b'',b'invalid JSON',b'[]',b'x'*(256*1024+1)]:
                cls.return_value.getresponse.return_value.read.return_value=raw
                self.error_code('invalid_response',lambda:transport('https://public.example','models',SYNTHETIC_KEY,None,['93.184.216.34'],'models'),502)

class ModelAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=server.Store(Path(self.tmp.name)/'local.db',False);self.stub=Stub();self.service=ModelService(self.store,'local',self.stub,resolver)
        self.http=server.make_server(self.store,'local',port=0,model_service=self.service);self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start();self.base='http://127.0.0.1:'+str(self.http.server_port)
        self.meta=self.call('/api/meta')
    def tearDown(self):self.http.shutdown();self.http.server_close();self.thread.join();self.tmp.cleanup()
    def call(self,path,data=None,headers=None):
        h={'Content-Type':'application/json','X-CSRF-Token':getattr(self,'meta',{}).get('csrf','')};h.update(headers or {})
        req=urllib.request.Request(self.base+path,data=json.dumps(data).encode() if data is not None else None,headers=h)
        with urllib.request.urlopen(req) as response:return json.load(response)
    def test_safe_api_settings_model_discovery_and_short_test(self):
        settings=self.call('/api/model/settings',{'provider':'deepseek','base_url':'https://api.deepseek.com','model':'synthetic','api_key':SYNTHETIC_KEY})
        self.assertNotIn(SYNTHETIC_KEY,json.dumps(self.call('/api/meta')));self.assertNotIn(SYNTHETIC_KEY,json.dumps(self.call('/api/state')))
        request={'consent':True,'revision':settings['revision']};self.stub.result={'data':[{'id':'dynamic'}]}
        self.assertEqual(self.call('/api/model/models',request)['models'][0]['id'],'dynamic')
        self.stub.result=completion('OK');self.assertEqual(self.call('/api/model/test',request)['status'],'ok')
        self.assertNotIn(SYNTHETIC_KEY,json.dumps(self.call('/api/model/calls')))
    def test_admin_get_origin_and_post_csrf_guards(self):
        for headers in [{'Origin':'https://evil.example'},{'Sec-Fetch-Site':'cross-site'},{'Host':'evil.example'}]:
            with self.assertRaises(urllib.error.HTTPError) as caught:self.call('/api/model/settings',headers=headers)
            self.assertEqual(caught.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError) as caught:self.call('/api/model/settings',{}, {'X-CSRF-Token':'incorrect'})
        self.assertEqual(caught.exception.code,403)
    def test_catalog_http_contract_and_origin_guards(self):
        catalog=self.call('/api/model/catalog')
        self.assertEqual(len(catalog['items']),12)
        self.assertEqual(self.stub.calls,[])
        for headers in [{'Origin':'https://evil.example'},{'Sec-Fetch-Site':'cross-site'},{'Host':'evil.example'}]:
            with self.assertRaises(urllib.error.HTTPError) as caught:self.call('/api/model/catalog',headers=headers)
            self.assertEqual(caught.exception.code,403)
        item=catalog['items'][0]
        value=self.call('/api/model/settings',dict(option_id=item['option_id'],catalog_version=catalog['version'],api_key=SYNTHETIC_KEY))
        self.assertEqual(value['model'],item['model']);self.assertEqual(value['selection_mode'],'preset')
        self.assertEqual(self.stub.calls,[])
    def test_analysis_endpoint_selected_context_and_no_implicit_calls(self):
        customer=self.store.save_customer({'name':'合成 API 客户','type':'终端加工厂','stage':'待开发'},False)
        preview=self.call('/api/model/context?customer_id='+customer['id']);self.assertTrue(preview['real_allowed']);self.assertEqual(self.stub.calls,[])
        settings=self.call('/api/model/settings',{'provider':'deepseek','base_url':'https://api.deepseek.com','model':'synthetic','api_key':SYNTHETIC_KEY})
        preview=self.call('/api/model/context?customer_id='+customer['id'])
        result=self.call('/api/analyze',{'customer_id':customer['id'],'provider':'model','consent':True,'revision':settings['revision'],'context_token':preview['context_token'],'context':{'other_customer':'must be ignored'}})
        self.assertEqual(result['mode'],'model');self.assertNotIn('must be ignored',json.dumps(self.stub.calls))

if __name__=='__main__':unittest.main()
