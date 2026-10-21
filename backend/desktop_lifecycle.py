"""Workspace-scoped Windows instance lease; reopen/exit never terminate a PID."""
import ctypes
from ctypes import wintypes
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import webbrowser

PROTOCOL='overseas-desktop-instance-v1'

class DesktopInstance:
    def __init__(self,workspace):
        self.workspace=Path(workspace).resolve()
        self.scope=hashlib.sha256(os.path.normcase(str(self.workspace)).encode()).hexdigest()
        self.path=self.workspace/'runtime/desktop-instance.json'
        self.nonce=secrets.token_urlsafe(32)
        self.server=None;self.published=False;self.closing=False
        self.browser=None;self.browser_profile=None;self.browser_debug=False;self.browser_hwnd=0
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype=wintypes.HANDLE
        self.kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        self.kernel.ReleaseMutex.argtypes=[wintypes.HANDLE]
        self.kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        self.handle=self.kernel.CreateMutexW(None,False,'Local\\Overseas-'+self.scope)
        if not self.handle:raise OSError('无法建立Overseas工作区运行锁')
        wait=self.kernel.WaitForSingleObject(self.handle,0)
        self.acquired=wait in (0,0x80)
        if wait not in (0,0x80,0x102):
            self.kernel.CloseHandle(self.handle);self.handle=None
            raise OSError('无法检查Overseas工作区运行锁')

    def __enter__(self):return self
    def __exit__(self,*args):self.close()

    def authorize(self,value):
        return isinstance(value,str) and hmac.compare_digest(value,self.nonce)

    def public_status(self):
        return {'available':True,'application':'Overseas','state':'stopping' if self.closing else 'running','qa_window':self.browser_debug}

    def identity(self):
        return {'protocol':PROTOCOL,'workspace_scope':self.scope,'state':'stopping' if self.closing else 'running',
                'browser_window':self.browser_window(),'browser_pid':self.browser.pid if self.browser else None}

    def publish(self,server):
        if not self.acquired:raise RuntimeError('Instance lease not owned')
        self.server=server
        self.url='http://127.0.0.1:'+str(server.server_port)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'protocol':PROTOCOL,'workspace_scope':self.scope,'url':self.url,'nonce':self.nonce}),encoding='utf8')
        temporary.replace(self.path);self.published=True

    def schedule_shutdown(self):
        if self.closing:return
        self.closing=True
        timer=threading.Timer(.2,self.server.shutdown);timer.daemon=True;timer.start()

    def active_record(self):
        if not self.path.is_file() or self.path.stat().st_size>4096:raise ValueError('运行信息尚未就绪')
        data=json.loads(self.path.read_text(encoding='utf8'))
        if data.get('protocol')!=PROTOCOL or data.get('workspace_scope')!=self.scope:raise ValueError('工作区运行信息不匹配')
        url=urlsplit(data.get('url',''))
        if url.scheme!='http' or url.hostname!='127.0.0.1' or url.username or url.password or url.path or url.query or url.fragment or not url.port or url.port==8765:
            raise ValueError('本机运行地址无效')
        if not isinstance(data.get('nonce'),str) or len(data['nonce'])!=43:raise ValueError('运行校验信息无效')
        return data

    def contact(self,action,show_window=False):
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),_NoRedirect())
        record=None;identity=None
        deadline=time.monotonic()+6
        while time.monotonic()<deadline:
            try:
                record=self.active_record()
                request=urllib.request.Request(record['url']+'/api/desktop/instance',headers={'X-Overseas-Instance':record['nonce']})
                with opener.open(request,timeout=.5) as response:identity=json.loads(response.read(2048))
                if identity.get('protocol')!=PROTOCOL or identity.get('workspace_scope')!=self.scope:raise ValueError('本机应用身份不匹配')
                break
            except (OSError,ValueError,urllib.error.URLError):time.sleep(.1)
        else:raise RuntimeError('Overseas正在启动或运行信息不可用；没有新建第二个服务，也没有关闭其他程序。请稍后重试。')
        if action=='quit':
            request=urllib.request.Request(record['url']+'/api/desktop/control-quit',data=b'{"confirm":true}',headers={'X-Overseas-Instance':record['nonce'],'Content-Type':'application/json'})
            with opener.open(request,timeout=3) as response:value=json.loads(response.read(2048))
            if value.get('state')!='stopping':raise RuntimeError('应用没有确认退出')
            return {'application':'Overseas','state':'stopping','url':record['url']}
        if action=='open' and show_window:
            request=urllib.request.Request(record['url']+'/api/desktop/control-open',data=b'{"confirm":true}',headers={'X-Overseas-Instance':record['nonce'],'Content-Type':'application/json'})
            with opener.open(request,timeout=5) as response:identity=json.loads(response.read(2048))
            if identity.get('browser_window'):self.activate_window(identity['browser_window'],identity.get('browser_pid'))
        return {'application':'Overseas','state':identity['state'],'url':record['url'],'reused_existing_instance':True}

    def browser_window(self):
        if self.browser is None or self.browser.poll() is not None:return 0
        user=ctypes.WinDLL('user32',use_last_error=True)
        user.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
        if self.browser_hwnd:
            owner=wintypes.DWORD();user.GetWindowThreadProcessId(wintypes.HWND(self.browser_hwnd),ctypes.byref(owner))
            if owner.value==self.browser.pid:return self.browser_hwnd
        user.IsWindowVisible.argtypes=[wintypes.HWND]
        user.GetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
        callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
        found=[]
        @callback_type
        def visit(hwnd,_):
            pid=wintypes.DWORD();user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
            if pid.value==self.browser.pid and user.IsWindowVisible(hwnd):
                title=ctypes.create_unicode_buffer(512);user.GetWindowTextW(hwnd,title,len(title))
                if 'overseas' in title.value.casefold():found.append(int(hwnd));return False
            return True
        user.EnumWindows.argtypes=[callback_type,wintypes.LPARAM];user.EnumWindows(visit,0)
        self.browser_hwnd=found[0] if found else 0
        return self.browser_hwnd

    @staticmethod
    def activate_window(hwnd,pid):
        if not hwnd or not pid:return False
        user=ctypes.WinDLL('user32',use_last_error=True)
        user.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
        actual=wintypes.DWORD();user.GetWindowThreadProcessId(wintypes.HWND(hwnd),ctypes.byref(actual))
        if actual.value!=pid:return False
        user.ShowWindow.argtypes=[wintypes.HWND,ctypes.c_int]
        user.SetForegroundWindow.argtypes=[wintypes.HWND]
        user.ShowWindow(wintypes.HWND(hwnd),9)
        return bool(user.SetForegroundWindow(wintypes.HWND(hwnd)))

    def open_browser(self):
        hwnd=self.browser_window()
        if hwnd:self.activate_window(hwnd,self.browser.pid);return True
        chrome=Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe')
        if not chrome.is_file():
            return webbrowser.open(self.url,new=0)
        if self.browser_profile is None:
            self.browser_profile=self.workspace/'runtime'/('app-browser-'+secrets.token_hex(5))
            self.browser_profile.mkdir(parents=True,exist_ok=False)
        command=[str(chrome),'--app='+self.url,'--user-data-dir='+str(self.browser_profile),
                 '--no-first-run','--no-default-browser-check','--disable-background-networking',
                 '--disable-sync','--disable-extensions','--disable-background-mode']
        if self.browser_debug:command+=['--remote-debugging-port=0']
        child=subprocess.Popen(command)
        if self.browser is None or self.browser.poll() is not None:
            self.browser=child;self.browser_hwnd=0
            threading.Thread(target=self.watch_browser,daemon=True).start()
        for _ in range(30):
            hwnd=self.browser_window()
            if hwnd:self.activate_window(hwnd,self.browser.pid);return True
            time.sleep(.1)
        return False

    def watch_browser(self):
        deadline=time.monotonic()+20
        while not self.closing and time.monotonic()<deadline:
            hwnd=self.browser_window()
            if hwnd:break
            if self.browser.poll() is not None:
                self.schedule_shutdown();return
            time.sleep(.15)
        else:return
        user=ctypes.WinDLL('user32',use_last_error=True)
        user.IsWindow.argtypes=[wintypes.HWND]
        while not self.closing:
            if self.browser.poll() is not None or not user.IsWindow(wintypes.HWND(hwnd)):
                self.schedule_shutdown();return
            time.sleep(.25)

    def close_browser(self):
        hwnd=self.browser_window()
        if hwnd:
            user=ctypes.WinDLL('user32',use_last_error=True)
            user.PostMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
            user.PostMessageW(wintypes.HWND(hwnd),0x10,0,0)

    def close(self):
        if self.published:
            self.close_browser()
            try:
                if self.authorize(self.active_record().get('nonce')):self.path.unlink()
            except (OSError,ValueError):pass
        if self.handle:
            if self.acquired:self.kernel.ReleaseMutex(self.handle)
            self.kernel.CloseHandle(self.handle);self.handle=None

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None
