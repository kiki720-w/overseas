# -*- coding: utf-8 -*-
"""HTTPS only, verified public DNS, pinned destination, no proxies or redirects."""
import http.client
import ipaddress
import json
import re
import socket
import ssl
from urllib.parse import urlsplit
from .common import AppError

MAX_RESPONSE=256*1024

def is_public(addr):
    if not addr.is_global or addr.is_multicast or addr.is_reserved or addr.is_unspecified or addr.is_loopback or addr.is_link_local:return False
    mapped=getattr(addr,'ipv4_mapped',None)
    embedded=getattr(addr,'sixtofour',None)
    if mapped is not None and not is_public(mapped) or embedded is not None and not is_public(embedded):return False
    teredo=getattr(addr,'teredo',None)
    return not teredo or all(is_public(x) for x in teredo)

def normalize_endpoint(value):
    if not isinstance(value,str) or len(value)>500 or any(ord(c)<33 for c in value):
        raise AppError('端点必须是有效 HTTPS 地址',code='endpoint_invalid')
    try:
        u=urlsplit(value);port=u.port
        host=(u.hostname or '').encode('idna').decode('ascii').lower()
    except (ValueError,UnicodeError):
        raise AppError('端点格式无效',code='endpoint_invalid')
    if u.scheme!='https' or not host or u.username is not None or u.password is not None or port not in (None,443) or u.query or u.fragment:
        raise AppError('只支持无用户名、参数和片段的 HTTPS 公网端点（443）',code='endpoint_invalid')
    path=u.path.rstrip('/')
    if not re.fullmatch(r'(?:/[A-Za-z0-9_.-]+)*',path) or any(p in ('.','..') for p in path.split('/')):
        raise AppError('端点路径无效',code='endpoint_invalid')
    if host in ('localhost','metadata.google.internal','metadata','instance-data') or host.endswith(('.localhost','.local','.internal')):
        raise AppError('禁止本机、私网或元数据端点',code='unsafe_endpoint')
    try:
        addr=ipaddress.ip_address(host)
        if not is_public(addr):raise AppError('禁止本机、私网或元数据地址',code='unsafe_endpoint')
    except ValueError:
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?',host) or '.' not in host:
            raise AppError('需要有效公网主机名',code='endpoint_invalid')
    rendered='['+host+']' if ':' in host else host
    return 'https://'+rendered+path

def public_addresses(base_url,resolver=socket.getaddrinfo):
    host=urlsplit(normalize_endpoint(base_url)).hostname
    try:entries=resolver(host,443,type=socket.SOCK_STREAM)
    except (OSError,ValueError):raise AppError('无法解析供应商地址，请检查端点与网络',502,'provider_unavailable')
    addresses=[]
    for entry in entries:
        value=entry[4][0].split('%')[0]
        try:addr=ipaddress.ip_address(value)
        except ValueError:raise AppError('供应商 DNS 返回无效地址',400,'unsafe_endpoint')
        if not is_public(addr):
            raise AppError('供应商 DNS 指向非公网地址，已拒绝请求',400,'unsafe_endpoint')
        if value not in addresses:addresses.append(value)
    if not addresses:raise AppError('供应商 DNS 无可用公网地址',502,'provider_unavailable')
    return addresses

class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self,host,ip,timeout):
        super().__init__(host,port=443,timeout=timeout,context=ssl.create_default_context())
        self.pinned_ip=ip
    def connect(self):
        sock=socket.create_connection((self.pinned_ip,443),self.timeout)
        try:self.sock=self._context.wrap_socket(sock,server_hostname=self.host)
        except Exception:sock.close();raise

def provider_error(status,task):
    if status in (301,302,303,307,308):return AppError('供应商重定向已拒绝，不会将密钥发往新地址',502,'redirect_blocked')
    if status==401:return AppError('供应商认证失败，请检查 API Key',401,'provider_auth')
    if status==403:return AppError('供应商拒绝访问，请检查账号或模型权限',403,'provider_forbidden')
    if task=='models' and status in (404,405,501):return AppError('此端点不支持模型发现，可手填模型 ID',404,'models_unsupported')
    if status==404:return AppError('供应商模型或生成端点不存在，请检查模型 ID 和地址',404,'model_not_found')
    if status==429:return AppError('供应商限流或额度不足，请查看供应商控制台后重试',429,'provider_rate_limit')
    return AppError('供应商请求失败，请检查其服务状态或兼容性',502,'provider_unavailable')

class HTTPSJSONTransport:
    def __call__(self,base_url,path,key,payload,addresses,task):
        u=urlsplit(base_url);conn=PinnedHTTPSConnection(u.hostname,addresses[0],35)
        body=None if payload is None else json.dumps(payload,ensure_ascii=False).encode('utf-8')
        try:
            conn.request('GET' if body is None else 'POST',u.path.rstrip('/')+'/'+path.lstrip('/'),body=body,headers={'Authorization':'Bearer '+key,'Content-Type':'application/json','Accept':'application/json'})
            response=conn.getresponse()
            if response.status!=200:raise provider_error(response.status,task)
            raw=response.read(MAX_RESPONSE+1)
            if len(raw)>MAX_RESPONSE:raise AppError('供应商响应超过大小上限',502,'invalid_response')
            try:value=json.loads(raw.decode('utf-8'))
            except (UnicodeError,ValueError):raise AppError('供应商未返回有效 JSON',502,'invalid_response')
            if not isinstance(value,dict):raise AppError('供应商响应必须是 JSON 对象',502,'invalid_response')
            return value
        except AppError:raise
        except (TimeoutError,socket.timeout):raise AppError('供应商请求超时；没有回退到演示结果',504,'provider_timeout')
        except (OSError,ssl.SSLError,http.client.HTTPException):raise AppError('无法安全连接供应商；没有回退到演示结果',502,'provider_unavailable')
        finally:conn.close()
