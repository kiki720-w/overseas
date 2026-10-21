"""Explicit 8766 formal launcher; enhanced retrieval starts disabled on every launch."""
import argparse
import json
import os
import sys
sys.dont_write_bytecode=True
if getattr(sys,'frozen',False) and len(sys.argv)>1 and sys.argv[1]=='--internal-worker':
    from backend.worker_entry import dispatch
    dispatch(sys.argv[2:])
    raise SystemExit(0)
from pathlib import Path
from backend.production_runtime import ClosedStore,NoKeyStartupModels,make_enhanced
from server import make_server

ROOT=Path(__file__).resolve().parent
def configured_ocr(store, profile_path,workspace_root=None):
    """Trusted explicit portable roots; HTTP clients cannot select runtime paths."""
    from backend.knowledge import KnowledgeService
    from backend.ocr_process import LocalOCRRunner,available_ram
    from backend.ocr_tasks import OCRTasks
    from backend.component_paths import ocr_profile,inspect_ocr
    paths=ocr_profile(profile_path,workspace_root or store.path.parent)
    inspection=inspect_ocr(paths)
    runner=LocalOCRRunner(paths['python'],paths['models'])
    if not inspection['files_present'] or not runner.available():
        return None,dict(inspection,protocol=paths['protocol'],native_runner_configured=False,status='component_missing')
    class ResourceVisibleOCRTasks(OCRTasks):
        def meta(self):
            value=super().meta();free=available_ram()
            value.update(free_ram_bytes=free,start_free_ram_bytes=4*1024**3,resource_ready=free>=4*1024**3,
                         commit_cap_bytes=2*1024**3,max_result_bytes=self.MAX_RESULT,local_only=True)
            return value
    service=ResourceVisibleOCRTasks(KnowledgeService(store),paths['staging'],runner)
    return service,{'protocol':paths['protocol'],'python':str(paths['python']),'models':str(paths['models']),
                    'staging':str(paths['staging']),'native_runner_configured':True,'startup_inference_calls':0,
                    'cloud_calls':0,'downloads':0,'max_upload_bytes':service.MAX_BYTES}

def configured_website(store, requested='native', python=None):
    """Trusted startup selection only; probe is synthetic, local, and never saved."""
    from backend.website import WebsiteService
    from backend.optional_process import extract_controlled,DEFAULT_PYTHON
    from backend.website_text import VisibleHTML
    from backend.common import AppError
    import time
    receipt={'requested':requested,'active':'native','local_only_extraction':True,
             'downloads':0,'model_calls':0,'startup_probe_synthetic':requested=='trafilatura'}
    if requested=='native':return WebsiteService(store),receipt
    runtime=python or DEFAULT_PYTHON
    raw=b'<main><h2>CNC products</h2><p>Our company manufactures precision CNC fixtures and gears for industrial production.</p></main>'
    text=raw.decode('utf8');parser=VisibleHTML();parser.feed(text)
    try:
        probe=extract_controlled(raw,text,parser,'https://startup-fixture.invalid/',time.monotonic()+8,runtime)
    except AppError as error:
        receipt.update(failure_code=error.code,notice='Optional startup probe failed; native website path remains active')
        return WebsiteService(store),receipt
    receipt.update(active='trafilatura',python=str(runtime),primary_version=probe['primary_version'],
                   probe_status=probe['status'],worker_boundary=probe['worker_boundary'],
                   native_fallback_available=True)
    return WebsiteService(store,optional_body=True,extraction_python=runtime),receipt

def main(argv=None):
    from backend.launcher_config import settings,preflight
    app_root=Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else ROOT
    default_workspace=Path(os.environ.get('LOCALAPPDATA',str(app_root)))/'Overseas' if getattr(sys,'frozen',False) else app_root/'workspace'
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int)
    parser.add_argument('--config',help='Nonsecret local launch profile')
    parser.add_argument('--workspace',help='Stable writable workspace; defaults to workspace/ or per-user packaged storage')
    parser.add_argument('--database',help='Explicit existing workspace database; never created or replaced')
    parser.add_argument('--desktop-action',choices=['open','quit','status'],default='open',help='Open/reopen this workspace or request only its own instance to exit')
    parser.add_argument('--auto-port',action='store_true',help='Choose an available OS-assigned loopback port for the desktop entry')
    parser.add_argument('--browser-debug',action='store_true',help=argparse.SUPPRESS)
    browser_options=parser.add_mutually_exclusive_group()
    browser_options.add_argument('--open-browser',action='store_true',help='Open the local UI after startup')
    browser_options.add_argument('--no-browser',action='store_true',help='Keep the UI URL explicit without launching a browser')
    parser.add_argument('--stage',choices=['search','enhanced'],default='enhanced')
    parser.add_argument('--ocr-profile',help='Explicit portable OCR v2 profile; omitted keeps OCR unavailable')
    parser.add_argument('--website-extraction',choices=['native','trafilatura'])
    parser.add_argument('--website-python',help='Trusted local optional runtime; no HTTP caller can choose it')
    parser.add_argument('--structured-validator',help='Trusted absolute Python executable with existing Pydantic v2; no new packages installed')
    args=parser.parse_args(argv)
    import server as server_module
    published_static=ROOT/'runtime/frontend/static'
    try:
        config=settings(args,app_root,default_workspace)
    except (ValueError,OSError,json.JSONDecodeError) as error:parser.error(str(error))
    from backend.desktop_lifecycle import DesktopInstance
    with DesktopInstance(config['workspace']) as desktop:
        if not desktop.acquired:
            value=desktop.contact(args.desktop_action,show_window=args.desktop_action=='open' and not args.no_browser)
            print(json.dumps(value),flush=True)
            return
        if args.desktop_action in ('quit','status'):
            print(json.dumps({'application':'Overseas','state':'not_running'}),flush=True);return
        if args.auto_port and args.port is None:config['port']=0
        desktop.browser_debug=args.browser_debug
        try:preflight(config,published_static)
        except (ValueError,OSError) as error:parser.error(str(error))
        return serve(config,args,published_static,desktop)

def serve(config,args,published_static,desktop):
    from backend.launcher_config import initialize_store
    import server as server_module
    server_module.STATIC_ROOT=published_static
    store=initialize_store(config);models=NoKeyStartupModels(store,'local')
    enhanced=make_enhanced(store,config['workspace'],config['rag']) if args.stage=='enhanced' else None
    ocr,ocr_receipt=configured_ocr(store,config['ocr_profile'],config['workspace']) if config['ocr_profile'] else (None,{'native_runner_configured':False})
    websites,website_receipt=configured_website(store,config['website_extraction'],config['website_python'])
    from backend.backups import Backups
    from backend.desktop_setup import DesktopSetup
    backups=Backups(store,config['workspace'])
    shortcut_profile={k:config[k] for k in ('website_extraction','website_python','ocr_profile','structured_validator') if config.get(k) is not None}
    shortcut_profile={k:str(v) if isinstance(v,Path) else v for k,v in shortcut_profile.items()}
    if args.stage=='enhanced':shortcut_profile['rag']=config['rag']
    setup=DesktopSetup(config['workspace'],Path(sys.executable).parent if getattr(sys,'frozen',False) else ROOT,shortcut_profile,args.stage,
                       directory=config['workspace']/'runtime/qa-shortcuts' if args.browser_debug else None)
    try:server=make_server(store,'local',False,config['port'],model_service=models,enhanced_service=enhanced,offline_boundary=False,ocr_service=ocr,website_service=websites,structured_validator=config['structured_validator'],desktop_service=desktop,backup_service=backups,desktop_setup=setup)
    except Exception:
        if ocr:ocr.close()
        raise
    if server.server_port in (8765,8766) and args.auto_port and args.port is None:
        server.server_close();raise RuntimeError('系统分配到保留端口，请重新打开Overseas')
    desktop.publish(server)
    backups.automatic()
    receipt={'url':f'http://127.0.0.1:{server.server_port}','pid':os.getpid(),'database':str(config['database']),
        'workspace':str(config['workspace']),'first_database_created':config['create_database'],
        'stage':args.stage,'active_static_root':str(server_module.STATIC_ROOT),'enhanced_default_enabled':False,'credential_values_read':False,'startup_model_key':'empty_memory',
        'website_extraction':website_receipt,'SEARCH_V2':True,'automatic_answers':False,'ocr':ocr_receipt,'desktop_instance_managed':True}
    target=config['workspace']/'evidence/PRODUCTION_SERVER.json';target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(receipt,indent=2),encoding='utf-8');print(json.dumps(receipt),flush=True)
    if args.open_browser or getattr(sys,'frozen',False) and not args.no_browser:
        import threading
        threading.Thread(target=desktop.open_browser,daemon=True).start()
    try:server.serve_forever()
    finally:server.server_close()

if __name__=='__main__':main()
