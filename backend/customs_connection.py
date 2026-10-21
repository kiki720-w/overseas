"""Nonsecret customs profile plus session-only scoped key; never connects on save."""
import hashlib,ipaddress,json,os,re,tempfile,threading
from urllib.parse import urlsplit
from .common import AppError
from .credential_vault import CredentialVault

def endpoint(value,optional=False):
    if optional and not value:return ''
    if not isinstance(value,str) or len(value)>1000:raise AppError('接口地址格式无效',400,'invalid_endpoint')
    try:
        url=urlsplit(value);port=url.port;host=url.hostname or ''
        if url.scheme!='https' or not host or '.' not in host or url.username or url.password or url.query or url.fragment or any(c.isspace() for c in value):raise ValueError()
        if host.lower().endswith(('.localhost','.local')) or host.lower()=='localhost':raise ValueError()
        try:
            address=ipaddress.ip_address(host)
        except ValueError:address=None
        if address and not address.is_global:raise ValueError()
    except (ValueError,TypeError):raise AppError('请填写公共 HTTPS 地址，不含用户名、查询参数或片段；Key 请放入独立字段',400,'invalid_endpoint') from None
    return value

class CustomsConnection:
    def __init__(self,store):
        self.path=store.path.parent/('customs-connection-'+hashlib.sha256(str(store.path.resolve()).encode()).hexdigest()[:16]+'.json')
        self.lock=threading.RLock();self.revision=0;self.key='';self.profile={'provider_name':'','endpoint':'','auth_type':'bearer','header_name':'X-API-Key','documentation_url':''}
        if self.path.exists():
            try:
                if self.path.is_symlink() or self.path.stat().st_size>8192:raise ValueError()
                self.profile=self.validate(json.loads(self.path.read_text(encoding='utf8')))
            except (OSError,ValueError,TypeError,AppError):raise AppError('海关连接配置无效，原文件未改动',500,'customs_config_invalid') from None
    def validate(self,data):
        if not isinstance(data,dict) or set(data)!=set(self.profile):raise AppError('海关配置字段无效',400,'invalid_input')
        value=dict(data);name=value['provider_name']
        if not isinstance(name,str) or not 1<=len(name.strip())<=100:raise AppError('请填写服务商名称',400,'invalid_input')
        value['provider_name']=name.strip();value['endpoint']=endpoint(value['endpoint']);value['documentation_url']=endpoint(value['documentation_url'],True)
        if value['auth_type'] not in ('none','bearer','api_key_header'):raise AppError('不支持的认证方式',400,'invalid_input')
        header=value['header_name']
        if not isinstance(header,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{0,79}',header) or header.lower() in ('host','cookie','authorization','origin','content-length','content-type') or header.lower().startswith('proxy-'):raise AppError('请使用专用 API Key 请求头名称，例如 X-API-Key',400,'invalid_input')
        return value
    def settings(self):
        with self.lock:
            saved=bool(self.profile['endpoint'])
            return {**self.profile,'revision':self.revision,'profile_saved':saved,'key_configured':bool(self.key),'key_storage':'memory','automatic_key_restore':False,'credential_persistence_available':False,'adapter_supported':False,'query_available':False,'status':'adapter_required' if saved else 'not_configured','message':'配置已保存，供应商协议尚待适配；未验证连接、未发出请求。' if saved else '填写服务商、接口地址与认证方式；保存不会访问该地址。','required_information':['供应商官方 API 文档及样例请求/响应','请求方法、查询参数、分页与限流','国家/时间覆盖和字段授权','计费与账号权限说明']}
    def save(self,data):
        if not isinstance(data,dict) or set(data)-{'revision','action','profile','api_key'}:raise AppError('海关连接操作无效',400,'invalid_input')
        with self.lock:
            if type(data.get('revision')) is not int or data['revision']!=self.revision:raise AppError('海关连接已变更，请刷新后核对',409,'customs_config_changed')
            action=data.get('action','save')
            if action=='clear':
                if set(data)-{'revision','action'}:raise AppError('请单独清除海关连接',400,'invalid_input')
                self.path.unlink(missing_ok=True);self.key='';self.profile={'provider_name':'','endpoint':'','auth_type':'bearer','header_name':'X-API-Key','documentation_url':''}
            elif action=='save':
                value=self.validate(data.get('profile'));key=data.get('api_key')
                if key is not None and key!='' and not CredentialVault.valid_key(key):raise AppError('密钥格式无效',400,'invalid_input')
                scope=lambda p:tuple(p[k] for k in ('provider_name','endpoint','auth_type','header_name'))
                next_key=(self.key if scope(value)==scope(self.profile) else '') if not key else key
                if value['auth_type']=='none':next_key=''
                self.path.parent.mkdir(parents=True,exist_ok=True)
                if self.path.is_symlink():raise AppError('海关配置路径无效',400,'invalid_input')
                name=None
                try:
                    fd,name=tempfile.mkstemp(prefix='.customs-',dir=self.path.parent)
                    with os.fdopen(fd,'w',encoding='utf8') as f:json.dump(value,f);f.flush();os.fsync(f.fileno())
                    os.replace(name,self.path);name=None
                except OSError:raise AppError('海关连接保存失败，原配置保持不变',500,'customs_config_save_failed') from None
                finally:
                    if name:os.unlink(name)
                self.profile=value;self.key=next_key
            else:raise AppError('海关连接操作无效',400,'invalid_input')
            self.revision+=1;return self.settings()
