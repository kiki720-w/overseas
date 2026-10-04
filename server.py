# -*- coding: utf-8 -*-
"""Local-only factory trade workspace. Python standard library; no paid dependencies."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import secrets
import sqlite3
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import urllib.request
import urllib.error
from urllib.parse import urlsplit, parse_qs
from backend.common import AppError
from backend.importing import Importer
from backend.models import ModelService
from backend.knowledge import KnowledgeService
from backend.static_images import IMAGE_ROUTES, image_asset

ROOT = Path(__file__).resolve().parent
TYPES = ['终端加工厂', '机床厂家', '经销商']
STAGES = ['待开发', '已联系', '需求确认', '报价沟通', '样品验证', '商务谈判', '成交', '暂缓']
KINDS = ['沟通', '报价', '反馈', '跟进']

class ConflictError(Exception):
    pass

def read_customer(raw):
    c = json.loads(raw)
    c.setdefault('version', 0)
    return c

def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')

def field(data, key, limit=200, required=False):
    value = data.get(key, '')
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f'{key} 必须是文本，最多 {limit} 字符')
    value = value.strip()
    if required and not value:
        raise ValueError(f'{key} 不能为空')
    return value

def mock_analysis(c, factory, records):
    products = [p.strip() for p in factory['products'].replace('、',',').replace('，',',').split(',') if p.strip()]
    overlap = [p for p in products if p in c['product']]
    return {'mode':'mock','match':f"{'潜在品类匹配：'+ '、'.join(overlap) if overlap else '品类匹配未知'}；仅根据手动填写字段的规则提示，非采购概率。",'angle':{'终端加工厂':'先询问工件、工序、机床接口和现有装夹难点。','机床厂家':'先确认机床型号、夹持接口和配套验证流程。','经销商':'先确认经营品类、服务客户及样品评估流程。'}[c['type']], 'next_step':f"当前阶段：{c['stage']}。先核实：{c['blocker'] or '需求、技术规格及决策人'}。已有 {len(records)} 条输入记录；建议人工确认具体需求后安排下一次沟通。", 'draft':'Subject: Clarifying your workholding requirements\n\nHello,\n\nCould you share the workpiece drawing, machine interface and key workholding requirements for your application? We would like to assess whether our capabilities are a suitable fit.\n\nPlease also let us know your evaluation process and preferred next step.\n\nBest regards,\n[Your name / factory]\n\n[人工审核：补齐真实称呼和签名；不承诺价格、精度、交期或认证。]', 'unknowns':c['unknowns'] or '联系人、规格、数量、预算和交期尚未核实', 'notice':'演示规则输出，不是 DeepSeek 生成；不会发送邮件或正式报价。'}

class DeepSeekAdapter:
    """Compatibility stub: legacy environment-key path is disabled."""
    def __init__(self, enabled=False):self.enabled=enabled
    def analyze(self, context):raise ValueError('旧模型入口已禁用，请在本机模型设置页配置并主动确认调用')

class SourceAdapter:
    """Extension point: adapters must return evidence and retrieval date, never invented leads."""
    def search(self, query):
        raise NotImplementedError('搜索、社媒和海关数据源尚未接入')

from backend.store import Store

def make_server(store, mode='demo', enabled=False, port=8765, model_service=None):
    if mode == 'demo' and enabled:
        raise ValueError('演示模式禁止启用真实模型')
    token = secrets.token_urlsafe(24)
    models = model_service or ModelService(store,mode)
    importer = Importer(store,mode=='demo')
    knowledge = KnowledgeService(store)
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)
        def log_message(self, *args):
            pass  # No client text or secrets in logs.
        def reply(self, data, status=200):
            body = json.dumps(data,ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.end_headers()
            try:self.wfile.write(body)
            except ConnectionError:pass  # Navigation/cancellation must not undo a committed result.
        def valid_host(self):
            return self.headers.get('Host') in [f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}']
        def do_GET(self):
            if not self.valid_host():
                return self.reply({'error':'无效 Host'},403)
            path = urlsplit(self.path).path
            origin=self.headers.get('Origin')
            if path.startswith('/api/') and (origin and origin not in [f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'] or self.headers.get('Sec-Fetch-Site')=='cross-site'):
                return self.reply({'error':'禁止跨站读取本地资料','code':'cross_site'},403)
            query = {k:v[-1] for k,v in parse_qs(urlsplit(self.path).query,keep_blank_values=True).items()}
            if path in IMAGE_ROUTES:
                try:body,mime=image_asset(ROOT,path)
                except AppError as e:return self.reply({'error':str(e),'code':e.code},e.status)
                self.send_response(200)
                self.send_header('Content-Type',mime)
                self.send_header('Content-Length',str(len(body)))
                self.send_header('X-Content-Type-Options','nosniff')
                self.send_header('Cross-Origin-Resource-Policy','same-origin')
                self.send_header('Cache-Control','no-store')
                self.end_headers()
                try:self.wfile.write(body)
                except ConnectionError:pass
                return
            templates = {'/templates/customers.csv':('客户导入模板.csv','text/csv; charset=utf-8','customers-template.csv'),'/templates/customers.xlsx':('客户导入模板.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','customers-template.xlsx'),'/templates/demo.csv':('演示客户样例.csv','text/csv; charset=utf-8','demo-customers.csv')}
            if path in templates:
                filename,mime,download=templates[path]
                p=ROOT/'templates'/filename
                if not p.is_file():return self.reply({'error':'模板文件不存在'},404)
                self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Disposition','attachment; filename="'+download+'"');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(p.read_bytes());return
            try:
                if path == '/api/knowledge/meta':return self.reply(knowledge.meta())
                if path == '/api/knowledge/documents':return self.reply(store.knowledge_documents(query))
                if path == '/api/knowledge/search':return self.reply(store.knowledge_search(query))
                if path == '/api/knowledge/document':return self.reply(store.knowledge_document(field(query,'id',32,True),int(query['version']) if query.get('version') else None))
                if path == '/api/knowledge/original':
                    body,name=store.knowledge_original(field(query,'id',32,True),int(query.get('version','0')))
                    from urllib.parse import quote
                    self.send_response(200);self.send_header('Content-Type','application/octet-stream');self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+quote(name,safe=''));self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body);return
                if path == '/api/model/catalog':
                    from backend.model_catalog import catalog
                    return self.reply(catalog())
                if path == '/api/model/settings':return self.reply(models.settings())
                if path == '/api/model/context':return self.reply(models.context(field(query,'customer_id',32,True),json.loads(query.get('knowledge_selections','[]'))))
                if path == '/api/model/calls':return self.reply(store.calls(query))
                if path == '/api/analyses':return self.reply(store.analyses(field(query,'customer_id',32,True),query))
                if path == '/api/meta':
                    return self.reply({'factory':store.factory(),'metrics':store.metrics(),'mode':mode,'model_enabled':models.settings()['configured'],'model_status':'内存密钥已配置 · 需主动测试' if models.settings()['configured'] else '未连接 · 本地规则演示','model':models.settings(),'sources':'搜索 / 社媒 / 海关：未接入','csrf':token})
                if path == '/api/customers':return self.reply(store.customers(query))
                if path == '/api/customer':return self.reply(store.customer(field(query,'id',32,True)))
                if path == '/api/records':return self.reply(store.records(field(query,'customer_id',32,True),query))
                if path == '/api/import/errors':
                    body=importer.errors(field(query,'job_id',32,True))
                    self.send_response(200);self.send_header('Content-Type','text/csv; charset=utf-8');self.send_header('Content-Disposition','attachment; filename="import-errors.csv"');self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body);return
            except AppError as e:return self.reply({'error':str(e),'code':e.code},e.status)
            except (ValueError,TypeError) as e:return self.reply({'error':str(e)},400)
            if path == '/api/state':
                return self.reply(store.state() | {'mode':mode,'model_enabled':models.settings()['configured'],'model_status':'内存密钥已配置 · 需主动测试' if models.settings()['configured'] else '未连接 · 本地规则演示','model':models.settings(),'sources':'搜索 / 社媒 / 海关：未接入','csrf':token})
            files = {'/knowledge':'knowledge.html','/knowledge.html':'knowledge.html','/knowledge.js':'knowledge.js','/knowledge.css':'knowledge.css','/knowledge-selection.js':'knowledge-selection.js','/':'index.html','/app.js':'app.js','/style.css':'style.css','/workspace.css':'workspace.css', '/deals':'deals.html','/deals.html':'deals.html','/deals.js':'deals.js','/deals.css':'deals.css','/settings':'settings.html','/settings.html':'settings.html','/settings.js':'settings.js','/settings.css':'settings.css','/analysis-history.js':'analysis-history.js'}
            if path not in files:
                return self.reply({'error':'资源不存在'},404)
            p = ROOT/'static'/files[path]
            if not p.is_file():
                return self.reply({'error':'此模块页面尚未完成'},404)
            self.send_response(200)
            self.send_header('Content-Type', {'html':'text/html; charset=utf-8','js':'text/javascript; charset=utf-8','css':'text/css; charset=utf-8'}[p.suffix[1:]])
            self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(p.read_bytes())
        def do_POST(self):
            if not self.valid_host() or self.headers.get('X-CSRF-Token') != token:
                return self.reply({'error':'请求校验失败，请刷新页面'},403)
            origin = self.headers.get('Origin')
            if origin and origin not in [f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}']:
                return self.reply({'error':'禁止跨站请求'},403)
            try:
                if self.headers.get('Content-Type','').split(';',1)[0].strip().lower()!='application/json':
                    raise AppError('请使用 application/json 请求',400,'invalid_input')
                length = int(self.headers.get('Content-Length','0'))
                limit = 7*1024*1024 if self.path in ('/api/import/upload','/api/knowledge/preview') else 100000
                if length <= 0 or length > limit:
                    # Drain bounded oversized requests so Windows can deliver the error response
                    # without resetting a socket with unread request bytes.
                    if 100000 < length <= 1000000:
                        self.connection.settimeout(5)
                        self.rfile.read(length)
                    raise ValueError('请求体为空或超过大小限制')
                data = json.loads(self.rfile.read(length))
                if not isinstance(data,dict):
                    raise ValueError('请求必须为 JSON 对象')
                if self.path == '/api/model/settings':result=models.save(data)
                elif self.path == '/api/knowledge/preview':result=knowledge.preview(data)
                elif self.path == '/api/knowledge/confirm':result=knowledge.confirm(data)
                elif self.path == '/api/knowledge/status':result=store.knowledge_status(data)
                elif self.path == '/api/knowledge/discard':result=knowledge.discard(field(data,'job_id',32,True))
                elif self.path == '/api/model/models':result=models.models(data)
                elif self.path == '/api/model/test':result=models.test(data)
                elif self.path == '/api/analysis/draft':result=store.analysis_draft(data)
                elif self.path == '/api/customer':
                    result = store.save_customer(data, mode=='demo')
                elif self.path == '/api/factory':
                    result = store.save_factory(data)
                elif self.path == '/api/record':
                    result = store.record(data)
                elif self.path == '/api/customers/bulk':result=store.bulk(data)
                elif self.path == '/api/import/upload':result=importer.upload(data)
                elif self.path == '/api/import/preview':result=importer.preview(data)
                elif self.path == '/api/import/confirm':result=importer.confirm(data)
                elif self.path == '/api/import/discard':result=importer.discard(field(data,'job_id',32,True))
                elif self.path == '/api/analyze':
                    c = store.customer(field(data,'customer_id',32,True))
                    s = {'factory':store.factory()}
                    records = store.records(c['id'],{'page_size':'30'})['items']
                    if data.get('provider') in ('model','deepseek'):
                        if mode=='demo' or c['demo']:
                            raise AppError('演示数据不能发送到真实模型',400,'demo_data_forbidden')
                        result=models.analyze(c['id'],data)
                    elif data.get('provider') == 'mock':
                        from backend.analysis_history import replay
                        from backend.model_analysis import build_context
                        mock_c,mock_context,_,_,mock_token=build_context(store,c['id'],data.get('knowledge_selections',[]))
                        entry,created=store.analysis_begin(c['id'],dict(data,context_token=mock_token),{'provider':'mock','model':'local-rules'},'mock')
                        if created:
                            result = mock_analysis(mock_c,mock_context['factory'],mock_context['records'])
                            result.update(stage_blocker=mock_c.get('blocker','') or '待核实',facts=[],hypotheses=[mock_c.get('hypotheses','')] if mock_c.get('hypotheses') else [],questions=[mock_c.get('unknowns','')] if mock_c.get('unknowns') else [])
                            entry=store.analysis_finish(entry,result)
                        result=dict(replay(entry),historical=store.analysis_view(entry)['historical'])
                    else:
                        raise AppError('请明确选择 mock 或 model')
                else:
                    return self.reply({'error':'接口不存在'},404)
                self.reply(result)
            except AppError as e:
                self.reply({'error':str(e),'code':e.code},e.status)
            except ConflictError as e:
                self.reply({'error':str(e),'code':'stale_customer'},409)
            except LookupError as e:
                self.reply({'error':str(e)},404)
            except (ValueError, TypeError) as e:
                self.reply({'error':str(e)},400)
            except Exception:
                self.reply({'error':'服务暂时失败；请重试并检查本地数据库权限'},500)
    return ThreadingHTTPServer(('127.0.0.1',port),Handler)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode',choices=['demo','local'],default='demo')
    parser.add_argument('--enable-deepseek',action='store_true',help='Legacy flag; keys are now entered only in the local model settings page')
    parser.add_argument('--port',type=int,default=8765)
    args = parser.parse_args()
    store = Store(ROOT/'data'/f'{args.mode}.sqlite3',args.mode=='demo')
    server = make_server(store,args.mode,args.enable_deepseek,args.port)
    print(f'Local workspace: http://127.0.0.1:{server.server_port} | mode={args.mode}',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
