import json,tempfile,threading,unittest,urllib.request,urllib.error
from pathlib import Path
from unittest.mock import Mock
from backend.common import AppError
from backend.production_runtime import ClosedStore
from backend.deepseek_tools import DeepSeekTools
from backend.search import TavilySearch
from backend.aliyun_search import AliyunSearch
from backend.search_connections import SearchConnections
from backend.discovery import DiscoveryService
from test_deepseek_tools import row
import server

class LiveContractTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=ClosedStore(Path(self.temp.name)/'synthetic.sqlite3',False)
        def respond(tool,payload,key):return (200,{'results':[row()]}) if tool=='search' else (200,{'title':'Synthetic','content':'# Mock public text'})
        self.transport=Mock(side_effect=respond);self.deep=DeepSeekTools(self.transport,cooldown=0)
        self.manager=SearchConnections(self.store,new_workspace=True,providers={'deepseek':self.deep,'aliyun':AliyunSearch(cooldown=0),'tavily':TavilySearch(cooldown=0)})
        self.deep.save({'revision':0,'api_key':'synthetic-live-contract-only','consent':True})
        service=DiscoveryService(self.store,None,'local',self.manager);self.http=server.make_server(self.store,'local',port=0,discovery_service=service);self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start();self.base='http://127.0.0.1:'+str(self.http.server_port)
        with urllib.request.urlopen(self.base+'/api/meta') as response:self.csrf=json.load(response)['csrf']
    def tearDown(self):self.http.shutdown();self.http.server_close();self.thread.join();self.temp.cleanup()
    def post(self,path,data):
        request=urllib.request.Request(self.base+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json','X-CSRF-Token':self.csrf})
        try:
            with urllib.request.urlopen(request) as response:return response.status,json.load(response)
        except urllib.error.HTTPError as error:return error.code,json.load(error)
    def test_exact_three_call_plan_forwards_count5_without_expansion(self):
        queries=['precision gear machining company Germany lang:en','CNC workholding manufacturer United States lang:en']
        for query in queries:
            status,value=self.post('/api/discovery/search',{'query':query,'count':5,'consent':True});self.assertEqual(status,200);self.assertEqual(value['provider_http_status'],200);self.assertEqual(len(value['source_pages']),1)
        self.assertEqual(self.post('/api/search/fetch',{'url':'https://www.schunk.com/','consent':True})[0],200)
        self.assertEqual(self.transport.call_count,3)
        for call,query in zip(self.transport.call_args_list[:2],queries):self.assertEqual(call.args[:2],('search',{'query':query,'count':5,'need_content':False}))
        self.assertEqual(self.transport.call_args_list[2].args[:2],('fetch',{'url':'https://www.schunk.com/'}))
        with self.store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM customers').fetchone()[0],0)
        usage=self.manager.settings()['usage']['deepseek_tools'];self.assertEqual(usage['search']['attempts'],2);self.assertEqual(usage['fetch']['attempts'],1);self.assertEqual(sum(v['estimated_cost_cny'] for v in usage.values()),.015)
    def test_invalid_count_never_calls_and_other_supplier_not_switched(self):
        for count in [0,51,True,'5']:
            status,_=self.post('/api/discovery/search',{'query':'synthetic','count':count,'consent':True});self.assertEqual(status,400)
        self.transport.assert_not_called();self.manager.save({'provider':'tavily','revision':0})
        status,_=self.post('/api/discovery/search',{'query':'synthetic','count':5,'consent':True});self.assertEqual(status,400);self.assertEqual(self.manager.selected,'tavily');self.transport.assert_not_called()
    def test_saved_key_is_unverified_until_actual_mock_success(self):
        self.assertEqual(self.deep.settings()['connection_validation']['state'],'not_verified')
        self.assertEqual(self.deep.settings()['status'],'configured_not_verified')
        self.deep.search({'query':'synthetic'});self.assertEqual(self.deep.settings()['connection_validation']['state'],'verified');self.assertEqual(self.deep.settings()['status'],'verified')
        self.deep.save({'revision':self.deep.revision,'api_key':'synthetic-replacement-only','consent':True});self.assertEqual(self.deep.settings()['connection_validation']['state'],'not_verified')
        self.assertEqual(DeepSeekTools().settings()['connection_validation']['state'],'not_verified')
    def test_provider_http_status_is_reported_without_secret_body(self):
        self.transport.side_effect=None;self.transport.return_value=(429,{'error':{'message':'synthetic-live-contract-only'}})
        status,value=self.post('/api/discovery/search',{'query':'synthetic','count':5,'consent':True});self.assertEqual(status,429);self.assertEqual(value['provider_http_status'],429);self.assertNotIn('synthetic-live-contract-only',json.dumps(value))
