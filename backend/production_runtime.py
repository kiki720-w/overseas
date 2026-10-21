"""Formal local-owner bindings. No key values, frozen DB replacements or cloud calls."""
import contextlib
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
from .store import Store
from .models import ModelService, PRESETS
from .enhanced_preview import CanonicalSnapshot, ROOT

CANONICAL_MAP_SHA256='1158b398446e03d7856fc60eb4afcb2ee963a619b8cd58e4063e2925a371b074'

class ClosedStore(Store):
    @contextlib.contextmanager
    def db(self):
        connection=sqlite3.connect(self.path,timeout=15);connection.execute('PRAGMA busy_timeout=15000')
        try:
            with connection:yield connection
        finally:connection.close()

class NoKeyStartupModels(ModelService):
    def __init__(self,store,mode):
        # Read only the five noncredential profile fields, never whole model_config.
        self.store=store;self.mode=mode;self.lock=threading.RLock()
        from .credential_vault import CredentialVault
        # Constructing the vault reads no credential bytes and performs no restore.
        self.credential_vault=CredentialVault(Path(store.path).parent/'credentials',str(Path(store.path).resolve()))
        from .model_transport import HTTPSJSONTransport
        import socket
        self.transport=HTTPSJSONTransport();self.resolver=socket.getaddrinfo
        with store.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS model_config(id INTEGER PRIMARY KEY,data TEXT)')
            row=db.execute("SELECT json_extract(data,'$.provider'),json_extract(data,'$.base_url'),json_extract(data,'$.model'),json_extract(data,'$.revision'),json_extract(data,'$.name') FROM model_config WHERE id=1").fetchone()
        self.profile=dict(zip(['provider','base_url','model','revision','name'],row)) if row else {'provider':'deepseek','base_url':PRESETS['deepseek'],'model':'','revision':0,'name':'默认连接'}
        self.profile['name']=self.profile.get('name') or '默认连接'
        self.key='';self.reset_status()

class ProductionCanonicalSnapshot(CanonicalSnapshot):
    def __init__(self,store,map_path):
        self.db_path=Path(store.path).resolve(strict=True)
        if self.db_path.name=='demo.sqlite3':raise ValueError('8765 demo DB forbidden')
        if not self.db_path.is_relative_to(ROOT.resolve()):raise ValueError('Explicit project-local production DB required')
        map_path=Path(map_path).resolve(strict=True)
        if hashlib.sha256(map_path.read_bytes()).hexdigest()!=CANONICAL_MAP_SHA256:raise ValueError('Reviewed canonical map hash mismatch')
        self.lineage=json.loads(map_path.read_text(encoding='utf-8'));self.by_id={}
        for item in self.lineage:
            cid=item['candidate_chunk_id'];mapping=item.get('new',item)
            if cid in self.by_id and self.by_id[cid]!=mapping:raise ValueError('Ambiguous canonical mapping')
            self.by_id[cid]=mapping
        self.owner={'protocol':'local-owner-production-read-v1','principal':'local_owner',
            'scope':'local_factory_read_only','database':str(self.db_path),
            'read_document_ids':sorted({v['document_id'] for v in self.by_id.values()}),'send_document_ids':[]}
        self.details={};self.registry={}

def make_enhanced(store,workspace_root=None,local_config=None):
    from .enhanced_preview import EnhancedService
    # Preserve the production DB boundary, but let the current relational KB
    # supply all canonical identities, including ordinary subsequent uploads.
    db_path=Path(store.path).resolve(strict=True)
    if db_path.name=='demo.sqlite3':raise ValueError('8765 demo DB forbidden')
    boundary=Path(workspace_root).resolve() if workspace_root else ROOT.resolve()
    if not db_path.is_relative_to(boundary):raise ValueError('Explicit workspace-local production DB required')
    # This application has one local owner. This trusted policy grants read
    # access only; publication/version/review/expiry are checked independently.
    def local_owner_can_read(document):
        return True
    return EnhancedService(store,production=True,read_policy=local_owner_can_read,local_config=local_config,
        jobs_root=boundary/'runtime/enhanced/isolation/jobs')
