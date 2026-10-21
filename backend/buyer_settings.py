"""Noncredential, workspace-local search intent; never authorizes provider calls."""
import hashlib,json,threading
from .buyer_search import resolve,PROFILES
from .common import AppError

class BuyerSettings:
    def __init__(self,store):self.store=store;self.lock=threading.RLock()
    def read(self):
        with self.lock,self.store.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS buyer_intent_settings(id INTEGER PRIMARY KEY,data TEXT NOT NULL)')
            row=db.execute('SELECT data FROM buyer_intent_settings WHERE id=1').fetchone()
        config=json.loads(row[0]) if row else {'target':'terminal_processor','process_terms':[]}
        raw=json.dumps(config,sort_keys=True,ensure_ascii=False)
        return {'protocol':'buyer-intent-v1','config':config,'revision':hashlib.sha256(raw.encode()).hexdigest(),
                'profiles':PROFILES,'saved':bool(row),'provider_requests':0}
    def save(self,data):
        config=data.get('config')
        resolve({'query':'configuration validation only','buyer_intent':config})
        if not isinstance(config,dict):raise AppError('用途配置无效')
        config=dict(config,process_terms=[s.strip() for s in config.get('process_terms',[])])
        with self.lock:
            if data.get('revision')!=self.read()['revision']:raise AppError('用途设置已改变，请刷新后重试',409,'buyer_settings_changed')
            with self.store.db() as db:db.execute('INSERT OR REPLACE INTO buyer_intent_settings VALUES(1,?)',(json.dumps(config,ensure_ascii=False),))
            return self.read()
