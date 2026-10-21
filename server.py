# -*- coding: utf-8 -*-
"""Local-only factory trade workspace. Python standard library; no paid dependencies."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import sys
import traceback
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import urllib.request
import urllib.error
from urllib.parse import urlsplit, parse_qs
from backend.common import AppError
from backend.importing import Importer
from backend.models import ModelService
from backend.knowledge import KnowledgeService
from backend.knowledge_sources import KnowledgeSources
from backend.context_knowledge import project_knowledge
from backend.website import WebsiteService
from backend.discovery import DiscoveryService
from backend.search import TavilySearch
from backend.credential_vault import CredentialVault
from backend.static_images import IMAGE_ROUTES, image_asset

ROOT = Path(__file__).resolve().parent
STATIC_ROOT = ROOT/'static'
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

def make_server(store, mode='demo', enabled=False, port=8765, model_service=None, website_service=None, discovery_service=None, search_provider=None, enhanced_service=None, offline_boundary=True, ocr_service=None, structured_validator=None, history_access=None, desktop_service=None, customs_service=None, backup_service=None, desktop_setup=None):
    if mode == 'demo' and enabled:
        raise ValueError('演示模式禁止启用真实模型')
    token = secrets.token_urlsafe(24)
    staging_sessions=set()
    staging_session_lock=threading.Lock()
    models = model_service or ModelService(store,mode)
    from backend.deal_rag import DealRAGService
    deal_rag = DealRAGService(store,models,enhanced_service) if enhanced_service is not None else None
    from backend.structured_deals import StructuredDeals
    structured = StructuredDeals(store,models,structured_validator,offline_boundary)
    importer = Importer(store,mode=='demo')
    knowledge = KnowledgeService(store)
    from backend.ocr_tasks import OCRTasks
    ocr = ocr_service or OCRTasks(knowledge,store.path.parent/"ocr-staging")
    knowledge_sources = KnowledgeSources(store)
    websites = website_service or WebsiteService(store)
    from backend.search_connections import SearchConnections
    discovery = discovery_service or DiscoveryService(store,websites,mode,search_provider or (SearchConnections(store,new_workspace=getattr(store,'is_new_workspace',False)) if mode=='local' else None))
    from backend.customs import CustomsService
    from backend.customs_connection import CustomsConnection
    customs_connection=CustomsConnection(store)
    customs=customs_service or CustomsService(connection=customs_connection)
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
            if getattr(self,'new_staging_session',None):
                self.send_header('Set-Cookie','staging_session='+self.new_staging_session+'; Path=/; HttpOnly; SameSite=Strict')
            if getattr(self,'history_cookie',None) is not None:
                self.send_header('Set-Cookie','overseas_history='+self.history_cookie+'; Path=/api/deals/structured; HttpOnly; SameSite=Strict; Max-Age='+str(0 if not self.history_cookie else 30*24*60*60))
            self.end_headers()
            try:self.wfile.write(body)
            except ConnectionError:pass  # Navigation/cancellation must not undo a committed result.
        def valid_host(self):
            return self.headers.get('Host') in [f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}']
        def history_token(self):
            cookie=SimpleCookie()
            try:cookie.load(self.headers.get('Cookie',''))
            except Exception:return ''
            item=cookie.get('overseas_history');return item.value if item else ''
        def staging_session(self,create=False):
            cookie=SimpleCookie()
            try:cookie.load(self.headers.get('Cookie',''))
            except Exception:pass
            item=cookie.get('staging_session');value=item.value if item else ''
            with staging_session_lock:
                if value in staging_sessions:return value
                if create:
                    if len(staging_sessions)>=500:raise AppError('隔离浏览器会话已满，请重启此隔离服务',429,'staging_session_capacity')
                    value=secrets.token_hex(16);staging_sessions.add(value);self.new_staging_session=value;return value
            raise AppError('需要本地浏览器会话，请刷新页面',403,'staging_session_required')
        def do_GET(self):
            if not self.valid_host():
                return self.reply({'error':'无效 Host'},403)
            path = urlsplit(self.path).path
            origin=self.headers.get('Origin')
            if path.startswith('/api/') and (origin and origin not in [f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'] or self.headers.get('Sec-Fetch-Site')=='cross-site'):
                return self.reply({'error':'禁止跨站读取本地资料','code':'cross_site'},403)
            query = {k:v[-1] for k,v in parse_qs(urlsplit(self.path).query,keep_blank_values=True).items()}
            if path == '/api/customs/settings':return self.reply(customs_connection.settings())
            if path == '/api/customs/meta':return self.reply(customs.meta())
            if path == '/api/desktop/status':
                return self.reply(desktop_service.public_status() if desktop_service else {'available':False})
            if path == '/api/desktop/instance':
                if not desktop_service or not desktop_service.authorize(self.headers.get('X-Overseas-Instance')):
                    return self.reply({'error':'应用身份校验失败'},403)
                return self.reply(desktop_service.identity())
            if path == '/api/knowledge/parse/meta':return self.reply(ocr.meta())
            if path == '/api/knowledge/parse/tasks':return self.reply(ocr.listing())
            if path == '/api/knowledge/parse/original':
                try:source=ocr.original(query.get('job_id'))
                except AppError as e:return self.reply({'error':str(e),'code':e.code},e.status)
                self.send_response(200);self.send_header('Content-Type','application/pdf');self.send_header('Content-Length',str(source.seek(0,2)))
                self.send_header('Content-Disposition','attachment; filename="source.pdf"');self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers()
                self.connection.settimeout(30)
                try:
                    with source as original:
                        original.seek(0)
                        while True:
                            block=original.read(65536)
                            if not block:break
                            self.wfile.write(block)
                except (ConnectionError,OSError):pass
                return
            if path in ('/api/knowledge/parse/task','/api/knowledge/parse/draft'):
                try:return self.reply(ocr.status(query.get('job_id')) if path.endswith('/task') else ocr.draft(query.get('job_id')))
                except AppError as e:return self.reply({'error':str(e),'code':e.code},e.status)
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
            orbital_assets = {'/assets/overseas-compass.svg':'assets/overseas-compass.svg','/assets/overseas-compass.ico':'assets/overseas-compass.ico','/assets/orbital/brand-orbit.png': 'assets/orbital/brand-orbit.png', '/assets/orbital/countries-110m.json': 'assets/orbital/countries-110m.json', '/assets/orbital/countries-source.json': 'assets/orbital/countries-source.json', '/assets/orbital/countries.json': 'assets/orbital/countries.json', '/assets/orbital/generated-assets-provenance.json': 'assets/orbital/generated-assets-provenance.json', '/assets/orbital/icon-arrow-right.svg': 'assets/orbital/icon-arrow-right.svg', '/assets/orbital/icon-chart-bar.svg': 'assets/orbital/icon-chart-bar.svg', '/assets/orbital/icon-check.svg': 'assets/orbital/icon-check.svg', '/assets/orbital/icon-chevron-down.svg': 'assets/orbital/icon-chevron-down.svg', '/assets/orbital/icon-cube.svg': 'assets/orbital/icon-cube.svg', '/assets/orbital/icon-file-description.svg': 'assets/orbital/icon-file-description.svg', '/assets/orbital/icon-info-circle.svg': 'assets/orbital/icon-info-circle.svg', '/assets/orbital/icon-map-pin.svg': 'assets/orbital/icon-map-pin.svg', '/assets/orbital/icon-notebook.svg': 'assets/orbital/icon-notebook.svg', '/assets/orbital/icon-search.svg': 'assets/orbital/icon-search.svg', '/assets/orbital/icon-settings.svg': 'assets/orbital/icon-settings.svg', '/assets/orbital/icon-world.svg': 'assets/orbital/icon-world.svg', '/assets/orbital/icon-x.svg': 'assets/orbital/icon-x.svg', '/assets/orbital/orbital-globe.js': 'assets/orbital/orbital-globe.js', '/assets/orbital/starfield.png': 'assets/orbital/starfield.png', '/assets/orbital/three.core.min.js': 'assets/orbital/three.core.min.js', '/assets/orbital/three.module.js': 'assets/orbital/three.module.js', '/assets/orbital/topojson-client.min.js': 'assets/orbital/topojson-client.min.js', '/assets/orbital/countries-ADAPTATION.txt': 'assets/orbital/countries-ADAPTATION.txt', '/assets/orbital/countries-LICENSE.txt': 'assets/orbital/countries-LICENSE.txt', '/assets/orbital/credits.html': 'assets/orbital/credits.html', '/assets/orbital/earth-clouds-nasa.jpg': 'assets/orbital/earth-clouds-nasa.jpg', '/assets/orbital/earth-day-nasa.jpg': 'assets/orbital/earth-day-nasa.jpg', '/assets/orbital/earth-night-nasa.jpg': 'assets/orbital/earth-night-nasa.jpg', '/assets/orbital/icons-LICENSE.txt': 'assets/orbital/icons-LICENSE.txt', '/assets/orbital/imagery-NOTICE.txt': 'assets/orbital/imagery-NOTICE.txt', '/assets/orbital/imagery-provenance.json': 'assets/orbital/imagery-provenance.json', '/assets/orbital/three-LICENSE.txt': 'assets/orbital/three-LICENSE.txt', '/assets/orbital/topojson-LICENSE.txt': 'assets/orbital/topojson-LICENSE.txt', '/assets/orbital/world-atlas-LICENSE.txt': 'assets/orbital/world-atlas-LICENSE.txt'}
            orbital_assets.update({'/assets/orbital/coast.jpg':'assets/orbital/coast.jpg','/assets/orbital/coastal-imagery-NOTICE.txt':'assets/orbital/coastal-imagery-NOTICE.txt'})
            if path in orbital_assets:
                import mimetypes
                p = STATIC_ROOT/orbital_assets[path]
                if not p.is_file():return self.reply({'error':'Resource unavailable'},404)
                body=p.read_bytes();self.send_response(200)
                self.send_header('Content-Type',mimetypes.guess_type(p.name)[0] or 'application/octet-stream')
                self.send_header('Content-Length',str(len(body)));self.send_header('X-Content-Type-Options','nosniff')
                self.send_header('Cross-Origin-Resource-Policy','same-origin');self.end_headers();self.wfile.write(body);return
            templates = {'/templates/customers.csv':('客户导入模板.csv','text/csv; charset=utf-8','customers-template.csv'),'/templates/customers.xlsx':('客户导入模板.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','customers-template.xlsx'),'/templates/demo.csv':('演示客户样例.csv','text/csv; charset=utf-8','demo-customers.csv')}
            if path in templates:
                filename,mime,download=templates[path]
                p=ROOT/'templates'/filename
                if not p.is_file():return self.reply({'error':'模板文件不存在'},404)
                self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Disposition','attachment; filename="'+download+'"');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(p.read_bytes());return
            try:
                if path == '/api/backups/status':
                    if not backup_service:raise AppError('当前运行环境未启用本地备份',503,'backup_unavailable')
                    return self.reply(dict(backup_service.status(),csrf=token))
                if path == '/api/desktop/setup':
                    if not desktop_setup:raise AppError('当前环境没有桌面入口设置',503,'desktop_unavailable')
                    return self.reply(dict(desktop_setup.status(),csrf=token))
                if path == '/api/backups/download':
                    if not backup_service:raise AppError('当前运行环境未启用本地备份',503,'backup_unavailable')
                    file=backup_service.path(field(query,'id',100,True))
                    self.send_response(200);self.send_header('Content-Type','application/zip');self.send_header('Content-Disposition','attachment; filename="'+file.name+'"');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(file.stat().st_size));self.end_headers()
                    with file.open('rb') as stream:
                        while block:=stream.read(65536):self.wfile.write(block)
                    return
                if (enhanced_service is not None or path.startswith('/api/deals/structured')) and path.startswith('/api/'):
                    self.staging_session(create=True)
                if path == '/api/knowledge/enhanced/status':
                    if enhanced_service is None:raise AppError('增强检索未接入',503,'adapter_unavailable')
                    return self.reply(enhanced_service.status())
                if path == '/api/discovery/results':
                    from backend.discovery_results import listing
                    return self.reply(listing(discovery,query))
                if path == '/api/discovery/meta':return self.reply(discovery.meta())
                if path == '/api/search/settings':
                    provider=discovery.search_provider
                    if not hasattr(provider,'settings'):raise AppError('演示环境不配置搜索凭证',503,'search_not_connected')
                    return self.reply(provider.settings())
                if path == '/api/discovery/search-plan':return self.reply(discovery.plans.get(query))
                if path == '/api/discovery/buyer-settings':return self.reply(discovery.buyer_settings.read())
                if path == '/api/discovery/candidate':return self.reply(discovery.candidate(query))
                if path == '/api/discovery/candidates':return self.reply(store.discovery_candidates(query))
                if path == '/api/website/captures':return self.reply(store.website_captures(field(query,'customer_id',32,True)))
                if path == '/api/knowledge/sources':return self.reply(knowledge_sources.listing(query))
                if path == '/api/knowledge/source':return self.reply(knowledge_sources.detail(field(query,'id',64,True)))
                if path == '/api/knowledge/source-review':return self.reply(knowledge_sources.review_source(field(query,'document_id',32,True),int(query['version']) if query.get('version') else None))
                if path == '/api/knowledge/source-context-preview':
                    selections=json.loads(field(query,'selections',4000) or '[]')
                    return self.reply({'items':[project_knowledge(x) for x in store.knowledge_selection(selections)],'sent_to_model':False,'method':'local_projection_only'})
                if path == '/api/knowledge/meta':return self.reply(knowledge.meta())
                if path == '/api/knowledge/documents':return self.reply(store.knowledge_documents(query))
                if path == '/api/knowledge/search':return self.reply(store.knowledge_search(query))
                if path == '/api/knowledge/document':return self.reply(store.knowledge_document(field(query,'id',32,True),int(query['version']) if query.get('version') else None))
                if path == '/api/knowledge/original':
                    body,name=store.knowledge_original(field(query,'id',32,True),int(query.get('version','0')))
                    from urllib.parse import quote
                    self.send_response(200);self.send_header('Content-Type','application/octet-stream');self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+quote(name,safe=''));self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body);return
                if path == '/api/knowledge/index-status':
                    if enhanced_service is None or not hasattr(enhanced_service.snapshot,'status'):raise AppError('动态索引状态未接入',503,'index_adapter_unavailable')
                    return self.reply(enhanced_service.snapshot.status())
                if path == '/api/deals/rag/review-drafts/view':
                    return self.reply(models.review_draft_manager().view({'customer_id':field(query,'customer_id',32,True),'draft_id':field(query,'draft_id',32,True)},self.staging_session()))
                if path == '/api/deals/structured/meta':return self.reply(structured.meta())
                if path == '/api/deals/structured/history-access/meta':return self.reply(history_access.meta(self.history_token(),self.headers.get('X-History-Proof')) if history_access else {'configured':False,'connected':False,'scope':'history_only'})
                if path == '/api/deals/structured/history':
                    cid=field(query,'customer_id',32,True)
                    if history_access:return self.reply(structured.history_authorized_list(history_access,history_access.authenticate(self.history_token(),self.headers.get('X-History-Proof')),cid))
                    return self.reply(structured.history_list(self.staging_session(),cid))
                if path == '/api/deals/structured/history-view':
                    revision=field(query,'revision',10,True)
                    if not revision.isdigit():raise AppError('Invalid history revision',400,'invalid_input')
                    rid=field(query,'id',32,True);cid=field(query,'customer_id',32,True)
                    if history_access:return self.reply(structured.history_authorized_view(history_access,history_access.authenticate(self.history_token(),self.headers.get('X-History-Proof')),rid,cid,int(revision)))
                    return self.reply(structured.history_view(rid,self.staging_session(),cid,int(revision)))
                if path == '/api/deals/structured/view':return self.reply(structured.view(field(query,'id',32,True),self.staging_session(),field(query,'customer_id',32,True)))
                if path == '/api/model/catalog':
                    from backend.model_catalog import catalog
                    return self.reply(catalog())
                if path == '/api/model/settings':return self.reply(models.settings())
                if path == '/api/model/context':return self.reply(models.context(field(query,'customer_id',32,True),json.loads(query.get('knowledge_selections','[]'))))
                if path == '/api/model/calls':return self.reply(store.calls(query))
                if path == '/api/analyses':return self.reply(store.analyses(field(query,'customer_id',32,True),query))
                if path == '/api/meta':
                    return self.reply({'factory':store.factory(),'metrics':store.metrics(),'mode':mode,'model_enabled':models.settings()['configured'],'model_status':'内存密钥已配置 · 需主动测试' if models.settings()['configured'] else '未连接 · 本地规则演示','model':models.settings(),'sources':('搜索：搜索供应商独立 Bearer 凭证（实际认证与额度待请求验证）；社媒 / 海关：未接入' if discovery.search_provider.configured else '搜索 / 社媒 / 海关：未接入'),'csrf':token})
                if path == '/api/customers':return self.reply(store.customers(query))
                if path == '/api/customer':return self.reply(store.customer(field(query,'id',32,True)))
                if path == '/api/records':return self.reply(store.records(field(query,'customer_id',32,True),query))
                if path == '/api/import/errors':
                    body=importer.errors(field(query,'job_id',32,True))
                    self.send_response(200);self.send_header('Content-Type','text/csv; charset=utf-8');self.send_header('Content-Disposition','attachment; filename="import-errors.csv"');self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body);return
            except AppError as e:return self.reply({'error':str(e),'code':e.code},e.status)
            except (ValueError,TypeError) as e:return self.reply({'error':str(e)},400)
            if path == '/api/state':
                return self.reply(store.state() | {'mode':mode,'model_enabled':models.settings()['configured'],'model_status':'内存密钥已配置 · 需主动测试' if models.settings()['configured'] else '未连接 · 本地规则演示','model':models.settings(),'sources':('搜索：搜索供应商独立 Bearer 凭证（实际认证与额度待请求验证）；社媒 / 海关：未接入' if discovery.search_provider.configured else '搜索 / 社媒 / 海关：未接入'),'csrf':token})
            files = {'/customs':'customs.html','/customs.js':'customs.js','/workspace-popovers.js':'workspace-popovers.js','/knowledge':'knowledge.html','/knowledge.html':'knowledge.html','/knowledge.js':'knowledge.js','/knowledge.css':'knowledge.css','/knowledge-selection.js':'knowledge-selection.js','/':'index.html','/app.js':'app.js','/style.css':'style.css','/workspace.css':'workspace.css', '/deals':'deals.html','/deals.html':'deals.html','/deals.js':'deals.js','/deals.css':'deals.css','/settings':'settings.html','/settings.html':'settings.html','/settings.js':'settings.js','/settings.css':'settings.css','/analysis-history.js':'analysis-history.js','/deal-coverage.js':'deal-coverage.js'}
            files.update({'/deals-structured.js':'deals-structured.js','/deals-structured.css':'deals-structured.css'})
            files.update({'/coastal-workspace.css':'coastal-workspace.css','/workspace-motion.js':'workspace-motion.js','/workspace-desktop.js':'workspace-desktop.js'})
            files.update({'/orbital-system.css':'orbital-system.css','/orbital-shell.js':'orbital-shell.js','/orbital-selects.css':'orbital-selects.css'})
            files.update({'/research/results':'research-results.html','/research-home.js':'research-home.js','/research-results.js':'research-results.js','/research-results.css':'research-results.css'})
            files.update({'/research-orbital.js':'research-orbital.js','/research-orbital.css':'research-orbital.css'})
            files.update({'/research':'research.html','/research.js':'research.js','/research.css':'research.css','/research-entry.js':'research-entry.js','/research-known.js':'research-known.js','/search-settings.js':'search-settings.js'})
            files['/workspace-navigation.js']='workspace-navigation.js'
            files.update({'/backups':'backups.html','/backups.js':'backups.js','/backups.css':'backups.css'})
            files['/buyer-intent.js']='buyer-intent.js'
            files['/customs-connection.js']='customs-connection.js'
            files.update({'/knowledge-ocr.css':'knowledge-ocr.css','/knowledge-ocr.js':'knowledge-ocr.js'})
            if path not in files:
                return self.reply({'error':'资源不存在'},404)
            p = STATIC_ROOT/files[path]
            if not p.is_file():
                return self.reply({'error':'此模块页面尚未完成'},404)
            self.send_response(200)
            self.send_header('Content-Type', {'html':'text/html; charset=utf-8','js':'text/javascript; charset=utf-8','css':'text/css; charset=utf-8'}[p.suffix[1:]])
            self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self'; script-src 'self'; style-src 'self' 'sha256-5Vse2j0ZboZatsJhGN/Cmj7bbfsydejMwb18WsOu1kE='; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(p.read_bytes())
        def do_POST(self):
            def rejected(data,status=403):
                # Discard bounded opaque request bytes before replying. Windows can
                # otherwise reset early-denied requests with unread body bytes.
                # Do not parse/store a rejected credential or website payload.
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if 0<length<=1000000:
                        self.connection.settimeout(2)
                        self.rfile.read(length)
                except (OSError,ValueError):pass
                return self.reply(data,status)
            if urlsplit(self.path).path in ('/api/desktop/control-quit','/api/desktop/control-open'):
                if not self.valid_host() or not desktop_service or not desktop_service.authorize(self.headers.get('X-Overseas-Instance')):
                    return rejected({'error':'应用身份校验失败'})
                origin=self.headers.get('Origin')
                if origin and origin not in [f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}']:
                    return rejected({'error':'禁止跨站请求'})
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=256 or self.headers.get('Content-Type','').split(';',1)[0]!='application/json':raise ValueError()
                    payload=json.loads(self.rfile.read(size))
                    if not isinstance(payload,dict) or set(payload)!={'confirm'} or payload.get('confirm') is not True:raise ValueError()
                except ValueError:return self.reply({'error':'退出确认无效'},400)
                if urlsplit(self.path).path == '/api/desktop/control-open':
                    if not desktop_service.open_browser():return self.reply({'error':'工作区仍在运行，但未能打开窗口；请稍后从桌面重试。'},503)
                    return self.reply(desktop_service.identity())
                self.reply({'application':'Overseas','state':'stopping'})
                desktop_service.schedule_shutdown();return
            if not self.valid_host() or self.headers.get('X-CSRF-Token') != token:
                return rejected({'error':'请求校验失败，请刷新页面'})
            origin = self.headers.get('Origin')
            if origin and origin not in [f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}']:
                return rejected({'error':'禁止跨站请求'})
            try:
                if urlsplit(self.path).path == '/api/backups/preview':
                    if not backup_service:raise AppError('当前运行环境未启用本地备份',503,'backup_unavailable')
                    from backend.backups import MAX_BYTES
                    if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length',[]))!=1:raise AppError('备份上传长度无效')
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=MAX_BYTES:raise AppError('备份包大小超过 256 MiB 上限',413,'backup_too_large')
                    if self.headers.get('Content-Type','').split(';',1)[0]!='application/zip':raise AppError('请选择 ZIP 备份包')
                    self.connection.settimeout(60)
                    body=self.rfile.read(size)
                    if len(body)!=size:raise AppError('备份上传不完整')
                    return self.reply(backup_service.preview(body))
                if enhanced_service is not None and offline_boundary and (self.path in ('/api/model/settings','/api/search/settings','/api/model/models','/api/model/test','/api/website/preview','/api/discovery/preview','/api/discovery/search','/api/discovery/search-plan','/api/discovery/search-next','/api/search/fetch')):
                    return rejected({'error':'隔离 staging 禁用云模型设置、外网搜索及网站采集','code':'staging_external_disabled'})
                if urlsplit(self.path).path == '/api/knowledge/parse/upload':
                    self.close_connection=True
                    if self.headers.get('Transfer-Encoding'):raise AppError('上传须提供Content-Length',400,'invalid_upload')
                    if self.headers.get('Content-Type','').split(';',1)[0].lower()!='application/pdf':raise AppError('上传须为application/pdf',400,'invalid_upload')
                    if len(self.headers.get_all('Content-Length',[]))!=1:raise AppError('上传长度头无效',400,'invalid_upload')
                    query=parse_qs(urlsplit(self.path).query)
                    self.connection.settimeout(30)
                    result=ocr.upload(query.get('job_id',[''])[0],self.rfile,int(self.headers.get('Content-Length','0')),self.connection.settimeout)
                    return self.reply(result,202)
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
                if self.path == '/api/desktop/quit':
                    if not desktop_service:raise AppError('此入口没有托管Overseas桌面实例',503,'desktop_unavailable')
                    if set(data)!={'confirm'} or data.get('confirm') is not True:raise AppError('请明确确认退出',400,'exit_confirmation_required')
                    self.reply({'application':'Overseas','state':'stopping'})
                    desktop_service.schedule_shutdown();return
                if self.path in ('/api/desktop/setup/create','/api/desktop/setup/skip'):
                    if not desktop_setup:raise AppError('当前环境没有桌面入口设置',503,'desktop_unavailable')
                    result=desktop_setup.create() if self.path.endswith('/create') else desktop_setup.dismiss()
                elif self.path in ('/api/backups/export','/api/backups/restore','/api/backups/open'):
                    if not backup_service:raise AppError('当前运行环境未启用本地备份',503,'backup_unavailable')
                    if self.path.endswith('/export'):result=backup_service.create()
                    elif self.path.endswith('/open'):result=backup_service.open_restored(data)
                    else:result=backup_service.restore(data)
                elif self.path == '/api/knowledge/enhanced/settings' and enhanced_service is not None:
                    self.staging_session()
                    result=enhanced_service.settings(data)
                elif self.path == '/api/knowledge/enhanced/search' and enhanced_service is not None:result=enhanced_service.search(data,self.staging_session())
                elif self.path == '/api/knowledge/enhanced/context-preview' and enhanced_service is not None:result=enhanced_service.context_preview(data,self.staging_session())
                elif self.path == '/api/deals/rag/review-drafts/edit':result=models.review_draft_manager().edit(data,self.staging_session())
                elif self.path == '/api/deals/rag/review-drafts/discard':result=models.review_draft_manager().discard(data,self.staging_session())
                elif self.path == '/api/deals/rag/prepare':
                    if deal_rag is None:raise AppError('本地知识检索未接入',503,'adapter_unavailable')
                    result=deal_rag.prepare(data,self.staging_session())
                elif self.path == '/api/deals/rag/generate':
                    if deal_rag is None:raise AppError('本地知识检索未接入',503,'adapter_unavailable')
                    if offline_boundary:raise AppError('离线候选环境禁止云生成',403,'staging_external_disabled')
                    result=deal_rag.generate(data,self.staging_session())
                elif self.path == '/api/deals/structured/history-access/connect':
                    if not history_access:raise AppError('History owner access not configured',503,'history_access_unavailable')
                    credential,result=history_access.connect(data);self.history_cookie=credential
                elif self.path == '/api/deals/structured/history-access/disconnect':
                    if not history_access or data!={}:raise AppError('History disconnect unavailable',400,'invalid_input')
                    history_access.disconnect(self.history_token(),self.headers.get('X-History-Proof'));self.history_cookie='';result={'connected':False,'scope':'history_only'}
                elif self.path.startswith('/api/deals/structured/'):
                    result=structured.action(self.path.rsplit('/',1)[-1],data,self.staging_session())
                elif self.path == '/api/model/settings':result=models.save(data)
                elif self.path == '/api/customs/settings':result=customs_connection.save(data)
                elif self.path == '/api/customs/analyze':result=customs.analyze(data)
                elif self.path == '/api/search/settings':
                    provider=discovery.search_provider
                    if not hasattr(provider,'save'):raise AppError('演示环境不配置搜索凭证',503,'search_not_connected')
                    result=provider.save(data)
                elif self.path == '/api/search/fetch':
                    provider=discovery.search_provider
                    if not hasattr(provider,'fetch'):raise AppError('当前搜索连接不支持 DeepSeek Fetch',400,'search_provider_selection_required')
                    result=provider.fetch(data)
                elif self.path == '/api/discovery/search':result=discovery.search(data)
                elif self.path == '/api/discovery/search-plan':result=discovery.plans.create(data)
                elif self.path == '/api/discovery/buyer-settings':result=discovery.buyer_settings.save(data)
                elif self.path == '/api/discovery/search-next':result=discovery.plans.next(data)
                elif self.path == '/api/discovery/search-stop':result=discovery.plans.stop(data)
                elif self.path == '/api/discovery/preview':result=discovery.preview(data)
                elif self.path == '/api/discovery/stash':result=discovery.stash(data)
                elif self.path == '/api/discovery/reject':result=discovery.reject(data)
                elif self.path == '/api/discovery/confirm':result=discovery.confirm(data)
                elif self.path == '/api/website/preview':result=websites.preview(data)
                elif self.path == '/api/website/confirm':result=websites.confirm(data)
                elif self.path == '/api/website/discard':result=websites.discard(data)
                elif self.path == '/api/knowledge/parse/tasks':return self.reply(ocr.create(data),202)
                elif self.path == '/api/knowledge/parse/review':result=ocr.review(data)
                elif self.path == '/api/knowledge/parse/confirm':result=ocr.confirm(data)
                elif self.path == '/api/knowledge/parse/cancel':result=ocr.cancel(data.get('job_id'))
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
                    if 'deal_question' in data and data.get('module','deals')!='deals':
                        raise AppError('deal_question 仅支持 deals 模块；development 请求请移除此参数',400,'invalid_parameter_combination')
                    if 'deal_question' in data:
                        if data['deal_question'] is None:
                            raise AppError('显式deal_question不能为null；普通分析请省略此字段',400,'invalid_deal_question')
                        from backend.deal_scope import question_value
                        data=dict(data,deal_question=question_value(data['deal_question']))
                    c = store.customer(field(data,'customer_id',32,True))
                    s = {'factory':store.factory()}
                    records = store.records(c['id'],{'page_size':'30'})['items']
                    if data.get('provider') in ('model','deepseek'):
                        if enhanced_service is not None and offline_boundary:raise AppError('隔离 staging 禁止真实云模型请求；可使用本地 mock',403,'staging_external_disabled')
                        if mode=='demo' or c['demo']:
                            raise AppError('演示数据不能发送到真实模型',400,'demo_data_forbidden')
                        result=models.analyze(c['id'],data,review_session=self.staging_session() if data.get('module','deals')=='deals' and data.get('deal_question') else None)
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
                if backup_service and self.path in ('/api/customer','/api/record','/api/customers/bulk','/api/factory','/api/import/confirm','/api/knowledge/confirm','/api/knowledge/parse/confirm','/api/knowledge/status','/api/discovery/buyer-settings','/api/discovery/confirm'):
                    backup_service.automatic(changed=True)
                self.reply(result)
            except AppError as e:
                self.reply(dict({'error':str(e),'code':e.code},**getattr(e,'details',{})),e.status)
            except ConflictError as e:
                self.reply({'error':str(e),'code':'stale_customer'},409)
            except LookupError as e:
                self.reply({'error':str(e)},404)
            except (ValueError, TypeError) as e:
                self.reply({'error':str(e)},400)
            except Exception as error:
                # Log frame locations, never request bodies/headers, exception values,
                # locals, provider credentials, or source lines containing user data.
                incident=secrets.token_hex(6)
                frames=[{'file':Path(frame.filename).name,'line':frame.lineno,'function':frame.name} for frame in traceback.extract_tb(error.__traceback__)]
                print(json.dumps({'event':'request_internal_error','incident':incident,'method':'POST','path':urlsplit(self.path).path,'exception_type':type(error).__name__,'frames':frames}),file=sys.stderr,flush=True)
                self.reply({'error':'服务暂时失败，请重试；故障编号 '+incident,'code':'internal_error','incident':incident},500)
    class WorkspaceHTTPServer(ThreadingHTTPServer):
        # make_server takes lifecycle ownership of its OCR service, including an
        # explicitly injected service. OCRTasks.close is safe to call again.
        def server_close(self):
            try:super().server_close()
            finally:
                if history_access:history_access.close()
                ocr.close()
    try:result=WorkspaceHTTPServer(('127.0.0.1',port),Handler)
    except Exception:
        if history_access:history_access.close()
        ocr.close()
        raise
    result.ocr_tasks=ocr
    return result

if __name__ == '__main__':
    from run_production import main
    main()
