"""Optional per-workspace shortcut; never replaces an existing desktop entry."""
import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
from .common import AppError


class DesktopSetup:
    def __init__(self, workspace, app, profile, stage, directory=None):
        self.workspace = Path(workspace).resolve()
        self.app = Path(app).resolve()
        self.profile = dict(profile, protocol='overseas-launch-v1', workspace=str(self.workspace))
        self.stage = stage
        self.directory = Path(directory) if directory else None
        self.state = self.workspace/'runtime/desktop-setup.json'
        self.lock = threading.Lock()

    def status(self):
        state = json.loads(self.state.read_text(encoding='utf-8')) if self.state.is_file() else {}
        return {'prompt': not state.get('dismissed'), 'created': bool(state.get('shortcut')),
                'shortcut': state.get('shortcut', ''), 'workspace': str(self.workspace)}

    def dismiss(self):
        with self.lock:
            state = self.status()
            state['dismissed'] = True
            self.state.parent.mkdir(parents=True, exist_ok=True)
            self.state.write_text(json.dumps(state), encoding='utf-8')
            return self.status()

    def create(self):
        with self.lock:
            target = self.app/'Overseas.exe'
            if not target.is_file():
                raise AppError('请使用完整打包桌面版创建快捷方式', 409, 'desktop_required')
            directory = self.directory
            if directory is None:
                value = ctypes.create_unicode_buffer(32768)
                if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, value) != 0:
                    raise AppError('无法确定当前用户桌面目录', 503, 'desktop_directory')
                directory = Path(value.value)
            directory.mkdir(parents=True, exist_ok=True)
            scope = hashlib.sha256(str(self.workspace).encode()).hexdigest()[:8]
            destination = None
            for number in range(100):
                suffix = '' if number == 0 else '-' + str(number)
                candidate = directory/('Overseas 候选 '+scope+suffix+'.lnk')
                try:
                    descriptor = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.close(descriptor)
                    destination = candidate
                    break
                except FileExistsError:
                    continue
            if destination is None:
                raise AppError('已有过多同名入口；未覆盖任何快捷方式', 409, 'shortcut_exists')
            config = self.workspace/'runtime/shortcut-launch.json'
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text(json.dumps(self.profile, ensure_ascii=False), encoding='utf-8')
            data = {'link': str(destination), 'target': str(target), 'working': str(self.app),
                    'arguments': '--auto-port --stage '+self.stage+' --config "'+str(config)+'"',
                    'icon': str(target)+',0'}
            payload = base64.b64encode(json.dumps(data).encode()).decode()
            script = "$ErrorActionPreference='Stop';$d=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"+payload+"'))|ConvertFrom-Json;$s=New-Object -ComObject WScript.Shell;$l=$s.CreateShortcut($d.link);$l.TargetPath=$d.target;$l.Arguments=$d.arguments;$l.WorkingDirectory=$d.working;$l.IconLocation=$d.icon;$l.Description='Overseas persistent workspace';$l.Save()"
            command = base64.b64encode(script.encode('utf-16le')).decode()
            powershell = Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
            try:
                subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-EncodedCommand', command],
                               check=True, capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
            except (OSError, subprocess.SubprocessError) as error:
                destination.unlink(missing_ok=True)
                raise AppError('快捷方式未创建；请继续使用原启动入口', 503, 'shortcut_failed') from error
            self.state.write_text(json.dumps({'dismissed': True, 'shortcut': str(destination)}), encoding='utf-8')
            return self.status()
