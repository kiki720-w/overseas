"""Local business snapshots. Credentials are outside the explicit table allowlist."""
import base64
from contextlib import closing
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import sys
import subprocess
import zipfile
from .common import AppError, now

TABLES = ('config', 'customers', 'records', 'analyses', 'website_captures',
          'discovery_candidates', 'discovery_events', 'kb_documents', 'kb_versions',
          'kb_chunks', 'kb_reviews',
          'buyer_intent_settings', 'search_plan_ledger', 'search_query_ledger',
          'search_source_ledger')
MAX_BYTES = 256 * 1024 * 1024
PROTOCOL = 'overseas-business-backup-v1'
EXCLUDED = ['Key / credential stores', 'API / model connection settings',
            'runtime sessions, structured session history and pending jobs',
            'OCR staging, browser profiles, logs, models and rebuildable vector indexes']


def encoded(value):
    return {'blob': base64.b64encode(value).decode('ascii')} if isinstance(value, bytes) else value


def decoded(value):
    if isinstance(value, dict) and set(value) == {'blob'}:
        return base64.b64decode(value['blob'], validate=True)
    if value is None or type(value) in (str, int, float):
        return value
    raise ValueError('Invalid snapshot cell')


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def reject_constant(value):
    raise ValueError('Nonfinite JSON number')


class Backups:
    def __init__(self, store, workspace):
        self.store = store
        self.workspace = Path(workspace).resolve()
        self.directory = self.workspace / 'backups'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.auto_error = ''

    def schemas(self, db):
        return {name: {'sql': sql, 'columns': [r[1] for r in db.execute(f'PRAGMA table_info("{name}")')],
                       'indexes': [r[0] for r in db.execute('SELECT sql FROM sqlite_master WHERE tbl_name=? AND type IN ("index","trigger") AND sql IS NOT NULL', (name,))]}
                for name, sql in db.execute('SELECT name,sql FROM sqlite_master WHERE type="table"') if name in TABLES}

    def status(self):
        files = sorted((p for p in self.directory.glob('*.zip') if not p.is_symlink()), key=lambda p: p.stat().st_mtime, reverse=True)
        return {'automatic': '启动时补齐当日快照；每次保存客户、记录或已入库资料后追加快照；不自动清理旧备份',
                'directory': str(self.directory), 'files': [{'id': p.stem, 'bytes': p.stat().st_size} for p in files],
                'automatic_error': self.auto_error, 'excluded': EXCLUDED,
                'restore_policy': '预检后恢复到新工作区；不覆盖当前库；恢复前另存保护快照',
                'knowledge': '包含所有已入库版本的原件、文本、分块和审核记录；向量索引重建',
                'max_bytes': MAX_BYTES}

    def automatic(self, changed=False):
        with self.lock:
            day = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d')
            if changed or not any(self.directory.glob('auto-' + day + '-*.zip')):
                try:
                    self.create('auto')
                    self.auto_error = ''
                except (OSError, sqlite3.Error, ValueError, AppError):
                    self.auto_error = '自动备份未完成；原数据未改动。请检查磁盘空间并手动导出。'

    def create(self, reason='manual'):
        if reason not in ('manual', 'auto', 'before-restore'):
            raise AppError('备份类型无效')
        with self.lock, self.store.db() as db:
            db.execute('BEGIN')
            schemas = self.schemas(db)
            tables = {}
            for name, schema in schemas.items():
                columns = schema['columns']
                if name == 'config':
                    fields = ('name', 'products', 'capabilities', 'countries', 'focus')
                    row = db.execute('SELECT ' + ','.join("json_extract(data,'$." + k + "')" for k in fields) + ' FROM config WHERE id=1').fetchone()
                    rows = [[1, json.dumps(dict(zip(fields, row)), ensure_ascii=False)]] if row else []
                else:
                    # Only business allowlisted tables are read; credential tables and files are never opened.
                    rows = [list(row) for row in db.execute(f'SELECT * FROM "{name}"')]
                if name == 'search_plan_ledger':
                    for row in rows:
                        for field in ('owner', 'pid', 'rid'):
                            if field in columns:
                                row[columns.index(field)] = None
                tables[name] = {'columns': columns, 'rows': [[encoded(v) for v in row] for row in rows]}
            content = json_bytes({'tables': tables})
            if len(content) > MAX_BYTES:
                raise AppError('业务数据超过本版本 256 MiB 备份上限；未生成不完整备份', 413, 'backup_too_large')
            manifest = {'protocol': PROTOCOL, 'created_at': now(), 'sha256': hashlib.sha256(content).hexdigest(),
                        'counts': {k: len(v['rows']) for k, v in tables.items()}, 'excluded': EXCLUDED,
                        'kb_originals': 'kb_versions.original: all committed versions', 'indexes': 'rebuild from kb_chunks'}
            identity = reason + '-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(4)
            target = self.directory / (identity + '.zip')
            temporary = target.with_suffix('.pending')
            try:
                with zipfile.ZipFile(temporary, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr('manifest.json', json_bytes(manifest))
                    archive.writestr('business.json', content)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            return {'id': identity, 'bytes': target.stat().st_size, 'manifest': manifest,
                    'download': '/api/backups/download?id=' + identity}

    def path(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r'(manual|auto|before-restore)-\d{8}-\d{6}-[a-f0-9]{8}', identity):
            raise AppError('备份标识无效', 400, 'invalid_backup_id')
        path = self.directory / (identity + '.zip')
        if not path.is_file() or path.is_symlink():
            raise AppError('备份文件不存在', 404, 'backup_missing')
        return path

    def inspect(self, body):
        try:
            if not body or len(body) > MAX_BYTES:
                raise ValueError('Backup size invalid')
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                infos = archive.infolist()
                # No extraction, arbitrary paths, duplicate members or symlinks are accepted.
                if len(infos) != 2 or {i.filename for i in infos} != {'manifest.json', 'business.json'}:
                    raise ValueError('Unexpected package members')
                if any(i.flag_bits & 1 or ((i.external_attr >> 16) & 0o170000) == 0o120000 for i in infos):
                    raise ValueError('Encrypted or symlink member')
                if sum(i.file_size for i in infos) > MAX_BYTES or archive.getinfo('manifest.json').file_size > 65536:
                    raise ValueError('Uncompressed backup too large')
                manifest = json.loads(archive.read('manifest.json'), parse_constant=reject_constant)
                if manifest.get('protocol') != PROTOCOL:
                    raise AppError('备份版本不兼容；当前库未改动', 409, 'backup_version')
                content = archive.read('business.json')
                if hashlib.sha256(content).hexdigest() != manifest.get('sha256'):
                    raise ValueError('Hash mismatch')
                data = json.loads(content, parse_constant=reject_constant)
            with self.store.db() as db:
                schemas = self.schemas(db)
            tables = data['tables']
            if not isinstance(tables, dict) or not {'config', 'customers', 'records', 'kb_documents', 'kb_versions', 'kb_chunks', 'kb_reviews'}.issubset(tables) or not set(tables).issubset(schemas):
                raise ValueError('Unexpected tables')
            if set(manifest['counts']) != set(tables):
                raise ValueError('Table count manifest mismatch')
            for name, table in tables.items():
                if table['columns'] != schemas[name]['columns']:
                    raise AppError('数据结构版本不兼容；当前库未改动', 409, 'backup_schema')
                if not isinstance(table['rows'], list) or manifest['counts'].get(name) != len(table['rows']):
                    raise ValueError('Count mismatch')
                for row in table['rows']:
                    if len(row) != len(table['columns']):
                        raise ValueError('Row width invalid')
                    for cell in row:
                        decoded(cell)
            return manifest, tables, schemas
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError, zipfile.BadZipFile, RuntimeError, OSError) as error:
            raise AppError('备份包缺失、损坏或内容不受支持；当前库未改动', 400, 'invalid_backup') from error

    def preview(self, body):
        manifest, _, _ = self.inspect(body)
        identity = secrets.token_hex(16)
        folder = self.workspace / 'backup-staging'
        folder.mkdir(exist_ok=True)
        (folder / (identity + '.zip')).write_bytes(body)
        return {'ticket': identity, 'manifest': manifest, 'policy': self.status()['restore_policy']}

    def restore(self, data):
        identity = data.get('ticket')
        if data.get('confirm') is not True:
            raise AppError('请先核对范围并明确确认恢复', 400, 'restore_confirmation_required')
        if not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{32}', identity):
            raise AppError('恢复预检标识无效')
        source = self.workspace / 'backup-staging' / (identity + '.zip')
        if not source.is_file() or source.is_symlink():
            raise AppError('预检包已不存在；请重新选择', 404, 'backup_missing')
        with self.lock:
            manifest, tables, schemas = self.inspect(source.read_bytes())
            root = self.workspace / 'restored-workspaces' / identity
            if root.exists():
                raise AppError('该预检包已恢复；未重复写入', 409, 'restore_exists')
            protection = self.create('before-restore')
            root.mkdir(parents=True)
            database = root / 'data' / 'overseas.sqlite3'
            database.parent.mkdir()
            try:
                with closing(sqlite3.connect(database)) as db, db:
                    for name, table in tables.items():
                        db.execute(schemas[name]['sql'])
                        marks = ','.join('?' for _ in table['columns'])
                        db.executemany(f'INSERT INTO "{name}" VALUES({marks})', ([decoded(v) for v in row] for row in table['rows']))
                    for name in tables:
                        for sql in schemas[name]['indexes']:
                            db.execute(sql)
                        if 'data' in tables[name]['columns'] and db.execute(f'SELECT 1 FROM "{name}" WHERE NOT json_valid(data) LIMIT 1').fetchone():
                            raise ValueError('Invalid business JSON')
                    for sql in (
                        'SELECT 1 FROM records r LEFT JOIN customers c ON r.customer_id=c.id WHERE c.id IS NULL LIMIT 1',
                        'SELECT 1 FROM kb_versions v LEFT JOIN kb_documents d ON v.document_id=d.id WHERE d.id IS NULL LIMIT 1',
                        'SELECT 1 FROM kb_chunks c LEFT JOIN kb_versions v ON c.document_id=v.document_id AND c.version=v.version WHERE v.document_id IS NULL LIMIT 1',
                        'SELECT 1 FROM kb_reviews r LEFT JOIN kb_versions v ON r.document_id=v.document_id AND r.version=v.version WHERE v.document_id IS NULL LIMIT 1',
                        "SELECT 1 FROM customers WHERE json_extract(data,'$.id') IS NOT id LIMIT 1",
                        "SELECT 1 FROM records WHERE json_extract(data,'$.id') IS NOT id OR json_extract(data,'$.customer_id') IS NOT customer_id LIMIT 1",
                        "SELECT 1 FROM kb_documents d LEFT JOIN kb_versions v ON d.id=v.document_id AND json_extract(d.data,'$.version')=v.version WHERE v.document_id IS NULL LIMIT 1",
                    ):
                        if db.execute(sql).fetchone():
                            raise ValueError('Broken business references')
                    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise ValueError('Restored database integrity failed')
                config = root / 'launch-restored.json'
                config.write_text(json.dumps({'protocol': 'overseas-launch-v1', 'workspace': '.', 'website_extraction': 'native'}), encoding='utf-8')
                if getattr(sys, 'frozen', False):
                    launcher = Path(sys.executable).with_name('Overseas.exe')
                    if launcher.is_file():
                        command = '@echo off\r\nchcp 65001 >nul\r\n"' + str(launcher).replace('%', '%%') + '" --auto-port --stage search --config "%~dp0launch-restored.json"\r\n'
                        (root / 'Start-Restored.cmd').write_text(command, encoding='utf-8')
                (root / 'RESTORE_OK.json').write_text(json.dumps({'ticket': identity, 'counts': manifest['counts']}), encoding='utf-8')
            except (sqlite3.Error, ValueError, OSError) as error:
                (root / 'RESTORE_FAILED.txt').write_text('恢复失败；不要打开此目录。当前工作区未改动。', encoding='utf-8')
                raise AppError('独立工作区恢复失败；当前库和保护快照均保留', 400, 'restore_failed') from error
            return {'ticket': identity, 'workspace': str(root), 'config': str(config), 'protection_backup': protection['id'],
                    'counts': manifest['counts'], 'current_workspace_unchanged': True,
                    'message': '已恢复到独立工作区。当前数据未覆盖；Key 需自行重新输入；向量索引需重建。'}

    def open_restored(self, data):
        identity = data.get('ticket')
        if not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{32}', identity):
            raise AppError('恢复工作区标识无效')
        root = self.workspace / 'restored-workspaces' / identity
        if not (root / 'RESTORE_OK.json').is_file() or (root / 'RESTORE_FAILED.txt').exists():
            raise AppError('恢复工作区未成功生成', 404, 'restore_missing')
        if not getattr(sys, 'frozen', False):
            raise AppError('请使用打包桌面版打开恢复工作区', 409, 'desktop_required')
        launcher = Path(sys.executable).with_name('Overseas.exe')
        subprocess.Popen([str(launcher), '--auto-port', '--stage', 'search', '--config', str(root / 'launch-restored.json')],
                         cwd=launcher.parent, creationflags=subprocess.CREATE_NO_WINDOW)
        return {'opened': True, 'workspace': str(root)}
