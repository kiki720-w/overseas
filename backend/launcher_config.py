"""Nonsecret local launch settings. No database, credential or network reads."""
import json
from pathlib import Path

KEYS={'protocol','workspace','port','rag','ocr_profile','website_extraction','website_python','structured_validator'}
RAG_KEYS={'local_python','embedding_dir','rerank_dir','local_model_cache','local_timeout_seconds'}

def local_path(value,base):
    if not isinstance(value,str) or not value.strip():raise ValueError('Local path must be nonempty text')
    path=Path(value).expanduser()
    return (path if path.is_absolute() else base/path).resolve()

def load_profile(path):
    if path is None:return {},None
    path=Path(path).resolve()
    if path.stat().st_size>16384:raise ValueError('Launch profile exceeds 16KiB')
    value=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value,dict) or set(value)-KEYS or value.get('protocol')!='overseas-launch-v1':
        raise ValueError('Unsupported nonsecret launch profile')
    if value.get('website_extraction','native') not in ('native','trafilatura'):raise ValueError('Invalid website extractor')
    return value,path.parent

def settings(args,app_root,default_workspace):
    profile,base=load_profile(args.config)
    base=base or app_root
    workspace=local_path(args.workspace or profile.get('workspace') or str(default_workspace),base)
    # A manually named legacy DB is resumed only when it already exists.
    explicit=args.database is not None
    database=local_path(args.database,app_root) if explicit else workspace/'data'/'overseas.sqlite3'
    if explicit and args.workspace is None and 'workspace' not in profile:workspace=app_root.resolve()
    if not database.is_relative_to(workspace) or database.name=='demo.sqlite3' or database.suffix!='.sqlite3':
        raise ValueError('Database must be a .sqlite3 file within the selected workspace; demo is excluded')
    if explicit and not database.is_file():raise ValueError('Explicit database must already exist; it will not be created')
    if database.exists() and not database.is_file():raise ValueError('Database path is not a regular file')
    port=args.port if args.port is not None else profile.get('port',8766)
    if type(port) is not int or not 1<=port<=65535 or port==8765:raise ValueError('Port must be 1..65535; 8765 is excluded')
    rag=profile.get('rag')
    if rag is None:
        # Missing model manifests produce the existing honest keyword fallback.
        rag={'local_python':str(workspace/'runtimes'/'rag'/'python.exe'),
             'embedding_dir':str(workspace/'models'/'e5-small'),
             'rerank_dir':str(workspace/'models'/'mmarco-minilm'),
             'local_model_cache':str(workspace/'cache'/'rag'),'local_timeout_seconds':90}
    else:
        if not isinstance(rag,dict) or set(rag)!=RAG_KEYS:raise ValueError('Invalid local RAG settings')
        rag=dict(rag)
        for key in RAG_KEYS-{'local_timeout_seconds'}:rag[key]=str(local_path(rag[key],base))
        timeout=rag['local_timeout_seconds']
        if type(timeout) is not int or not 10<=timeout<=180:raise ValueError('Invalid RAG timeout')
        if not Path(rag['local_model_cache']).is_relative_to(workspace):raise ValueError('RAG cache must stay in the workspace')
    result=dict(workspace=workspace,database=database,port=port,rag=rag,create_database=not database.exists(),
                website_extraction=args.website_extraction or profile.get('website_extraction','native'))
    for key in ('ocr_profile','website_python','structured_validator'):
        value=getattr(args,key,None) or profile.get(key)
        result[key]=str(local_path(value,base)) if value else None
    return result

def initialize_store(config):
    from .production_runtime import ClosedStore
    defaults={'name':'我的工厂（待完善）','products':'','capabilities':'','countries':'','focus':''}
    return ClosedStore(config['database'],False,factory_defaults=defaults)

def preflight(config,static_root):
    """Validate before SQLite initialization; never contact a provider."""
    import importlib.util
    import socket
    for file in ('index.html','research.html','deals.html','knowledge.html','settings.js'):
        if not (static_root/file).is_file():raise ValueError('Published frontend resource missing: '+file)
    for module in ('openpyxl','pypdf'):
        if importlib.util.find_spec(module) is None:raise ValueError('Required local dependency missing: '+module)
    validator=config['structured_validator']
    if validator and (not Path(validator).is_file() or Path(validator).suffix.lower()!='.exe'):
        raise ValueError('Configured local validator executable unavailable')
    if config['ocr_profile'] and not Path(config['ocr_profile']).is_file():raise ValueError('Configured OCR profile unavailable')
    if config['ocr_profile']:
        from .component_paths import ocr_profile
        ocr_profile(config['ocr_profile'],config['workspace'])
    with socket.socket() as probe:
        # Check local occupancy only, without connecting or starting a server.
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):probe.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        try:probe.bind(('127.0.0.1',config['port']))
        except OSError:raise ValueError('Local port is unavailable; existing services were not stopped') from None
