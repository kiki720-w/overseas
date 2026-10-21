"""Opt-in per-Windows-user DPAPI storage; no automatic secret restoration."""
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import tempfile
from .common import AppError


class WindowsDPAPI:
    available = os.name == 'nt'

    def transform(self, data, entropy, decrypt=False):
        if not self.available:
            raise OSError('Windows user protection unavailable')

        class Blob(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

        def blob(value):
            buffer = ctypes.create_string_buffer(value)
            return Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer

        source, keep_source = blob(data)
        extra, keep_extra = blob(entropy)
        target = Blob()
        crypt = ctypes.WinDLL('crypt32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        if decrypt:
            function = crypt.CryptUnprotectData
            function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        else:
            function = crypt.CryptProtectData
            function.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        function.restype = wintypes.BOOL
        # CRYPTPROTECT_UI_FORBIDDEN; never LOCAL_MACHINE or weaker plaintext fallback.
        if not function(ctypes.byref(source), None, ctypes.byref(extra), None, None, 1, ctypes.byref(target)):
            raise OSError('Windows user protection failed')
        try:
            return ctypes.string_at(target.data, target.size)
        finally:
            kernel.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


class CredentialVault:
    MAX_BYTES = 65536
    MAGIC = b'OVERSEAS-DPAPI-1\x00'

    def __init__(self, directory, namespace, protector=None):
        self.directory = Path(directory)
        self.namespace = str(namespace)
        self.protector = protector or WindowsDPAPI()

    @property
    def available(self):
        return bool(self.protector.available)

    def identity(self, scope):
        return hashlib.sha256(('overseas-user-vault-v1\x00' + self.namespace + '\x00' + scope).encode('utf-8')).digest()

    def path(self, scope):
        return self.directory / (self.identity(scope).hex() + '.dpapi')

    def contains(self, scope):
        # Metadata only: never reads/decrypts a key on startup or settings GET.
        try:
            path = self.path(scope)
            return self.available and path.is_file() and not path.is_symlink() and path.stat().st_size <= self.MAX_BYTES
        except OSError:
            return False

    @staticmethod
    def valid_key(value):
        return isinstance(value, str) and 0 < len(value) <= 4096 and all(33 <= ord(c) <= 126 for c in value)

    def save(self, scope, key):
        if not self.available:
            raise AppError('本机无法使用 Windows 用户级加密；未保存明文', 400, 'key_persistence_unavailable')
        if not self.valid_key(key):
            raise AppError('密钥格式无效', 400, 'invalid_input')
        temporary = None
        try:
            protected = self.protector.transform(key.encode('ascii'), self.identity(scope))
            if not protected or len(protected) + len(self.MAGIC) > self.MAX_BYTES:
                raise OSError('Invalid protected length')
            self.directory.mkdir(parents=True, exist_ok=True)
            if self.directory.is_symlink() or self.path(scope).is_symlink():
                raise OSError('Linked storage rejected')
            fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=str(self.directory))
            with os.fdopen(fd, 'wb') as handle:
                handle.write(self.MAGIC + protected)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path(scope))
            temporary = None
        except (OSError, ValueError):
            raise AppError('本机加密保存失败；未降级为明文，请保留原密钥自行重试', 500, 'credential_storage_failed') from None
        finally:
            if temporary:
                try:
                    Path(temporary).unlink()
                except OSError:
                    pass

    def load(self, scope):
        if not self.available:
            raise AppError('本机不支持 Windows 用户级密钥恢复', 400, 'key_persistence_unavailable')
        try:
            path = self.path(scope)
            if path.is_symlink():
                raise OSError('Linked storage rejected')
            with path.open('rb') as handle:
                raw = handle.read(self.MAX_BYTES + 1)
            if len(raw) > self.MAX_BYTES or not raw.startswith(self.MAGIC):
                raise ValueError('Invalid encrypted envelope')
            key = self.protector.transform(raw[len(self.MAGIC):], self.identity(scope), decrypt=True).decode('ascii')
            if not self.valid_key(key):
                raise ValueError('Invalid restored key')
            return key
        except (OSError, ValueError, UnicodeError):
            raise AppError('无法恢复此 Windows 用户和连接范围的加密密钥，请重新填写', 400, 'credential_restore_failed') from None

    def delete(self, scope):
        try:
            self.path(scope).unlink(missing_ok=True)
        except OSError:
            raise AppError('本机加密密钥删除失败，请重试', 500, 'credential_storage_failed') from None


def model_scope(profile):
    from .model_catalog import credential_scope
    return json.dumps([profile['provider'], profile['base_url'], credential_scope(profile)], separators=(',', ':'))
