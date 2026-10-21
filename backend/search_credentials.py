"""Independent search credential state; never reads model config or restores on startup."""
import threading
from .common import AppError

class SearchCredentials:
    SCOPE='tavily-search-bearer-v1'
    PROVIDER='tavily'
    LABEL='Tavily'
    ENDPOINT='https://api.tavily.com/search'
    def __init__(self,key='',vault=None):
        self._key=key;self.vault=vault;self.revision=0;self.config_lock=threading.RLock();self.last_error='';self.verified_at=None
    @property
    def configured(self):
        with self.config_lock:return bool(self._key)
    def settings(self):
        with self.config_lock:
            verified=bool(self._key and self.verified_at and not self.last_error)
            return {'provider':self.PROVIDER,'provider_label':self.LABEL,'endpoint':self.ENDPOINT,'access_mode':'bearer','configured':bool(self._key),
                'connection_validation':{'state':'verified' if verified else 'not_verified','verified_at':self.verified_at},
                'revision':self.revision,'key_storage':'memory','automatic_key_restore':False,
                'credential_persistence_available':bool(self.vault and self.vault.available),
                'credential_saved_encrypted':bool(self.vault and self.vault.contains(self.SCOPE)),
                'status':self.last_error or ('verified' if verified else 'configured_not_verified' if self._key else 'key_required'),
                'message':self.LABEL+(' 本次会话实测连接成功；不代表搜索覆盖或来源质量已验证。' if verified else ' 会话 Key 已保存，尚未验证连接。' if self._key else ' 尚未配置，请自行填写此供应商的 Key。')}
    def save(self,data):
        if not isinstance(data,dict) or set(data)-{'api_key','persist','restore_key','clear_key','consent','revision'}:raise AppError('搜索配置只接受独立凭证操作',400,'invalid_input')
        for name in ('persist','restore_key','clear_key'):
            if type(data.get(name,False)) is not bool:raise AppError('凭证操作必须为布尔值',400,'invalid_input')
        key=data.get('api_key')
        if key is not None and (not isinstance(key,str) or len(key)>4096 or any(ord(c)<33 or ord(c)>126 for c in key)):raise AppError('搜索 Key 格式无效',400,'invalid_input')
        if data.get('restore_key') and (data.get('consent') is not True or key or data.get('persist') or data.get('clear_key')):raise AppError('请单独明确确认恢复搜索凭证',400,'consent_required')
        if data.get('clear_key') and (key or data.get('persist')):raise AppError('不能同时保存与清除',400,'invalid_input')
        with self.config_lock:
            if type(data.get('revision')) is not int or data['revision']!=self.revision:raise AppError('搜索配置已变更，请刷新',409,'search_config_changed')
            next_key='' if data.get('clear_key') else (key or self._key)
            if data.get('restore_key'):
                if not self.vault:raise AppError('本机搜索凭证恢复不可用',400,'key_persistence_unavailable')
                next_key=self.vault.load(self.SCOPE)
            if data.get('persist'):
                if not next_key:raise AppError('请先填写独立 '+self.LABEL+' Key',400,'search_key_required')
                if not self.vault:raise AppError('本机搜索凭证加密保存不可用',400,'key_persistence_unavailable')
                self.vault.save(self.SCOPE,next_key)
            if data.get('clear_key') and self.vault:self.vault.delete(self.SCOPE)
            self._key=next_key;self.revision+=1;self.last_error='';self.verified_at=None
            return self.settings()
