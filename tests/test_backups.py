import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch
import zipfile
from backend.backups import Backups
from backend.common import AppError
from backend.production_runtime import ClosedStore
from backend.knowledge import KnowledgeService
from backend.common import TYPES, STAGES, KINDS
from backend.search_ledger import SearchLedger


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = ClosedStore(self.root/'data/overseas.sqlite3', False)
        self.backups = Backups(self.store, self.root)
        self.customer = self.store.save_customer({'name': 'DEMO backup customer', 'type': TYPES[0], 'stage': STAGES[1], 'blocker': 'Unknown demand'}, False)
        self.record = self.store.record({'customer_id': self.customer['id'], 'kind': KINDS[0], 'text': 'DEMO synthetic note'})
        knowledge = KnowledgeService(self.store)
        preview = knowledge.preview({'title': 'DEMO original', 'category': 'product', 'text': 'DEMO original version one'})
        self.document = knowledge.confirm({'job_id': preview['job_id'], 'consent': True})
        preview = knowledge.preview({'document_id': self.document['document_id'], 'expected_version': 1, 'title': 'DEMO original', 'category': 'product', 'text': 'DEMO original version two'})
        knowledge.confirm({'job_id': preview['job_id'], 'consent': True})
        SearchLedger(self.store, 'synthetic-owner')
        with self.store.db() as db:
            db.execute('CREATE TABLE model_config(id INTEGER PRIMARY KEY,data TEXT)')
            db.execute('INSERT INTO model_config VALUES(1,?)', (json.dumps({'key': 'SYNTHETIC-CREDENTIAL-EXCLUDE'}),))
            db.execute('CREATE TABLE runtime_sessions(token TEXT)')
            db.execute("INSERT INTO runtime_sessions VALUES('SYNTHETIC-SESSION-EXCLUDE')")
            db.execute('INSERT INTO search_plan_ledger VALUES(?,?,?,?,?,?,?)', ('plan', 'scope', '{}', 'synthetic-owner', 1234, 'runtime-rid', 'now'))
        credentials = self.root/'data/credentials'
        credentials.mkdir()
        (credentials/'synthetic.dpapi').write_bytes(b'SYNTHETIC-VAULT-EXCLUDE')

    def tearDown(self):
        self.tmp.cleanup()

    def package(self):
        result = self.backups.create()
        return self.backups.path(result['id']).read_bytes()

    def repack(self, body, change_manifest=None, change_data=None, extra=None):
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            manifest = json.loads(z.read('manifest.json'))
            data = json.loads(z.read('business.json'))
        if change_data:
            change_data(data)
        content = json.dumps(data).encode()
        manifest['sha256'] = hashlib.sha256(content).hexdigest()
        if change_manifest:
            change_manifest(manifest)
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as z:
            z.writestr('manifest.json', json.dumps(manifest))
            z.writestr('business.json', content)
            if extra:
                z.writestr(*extra)
        return out.getvalue()

    def test_round_trip_versions_originals_stage_records_and_protection(self):
        body = self.package()
        self.store.save_customer(dict(self.customer, name='DEMO newer current data'), False)
        current = self.store.state()
        ticket = self.backups.preview(body)['ticket']
        restored = self.backups.restore({'ticket': ticket, 'confirm': True})
        self.assertEqual(self.store.state(), current)
        self.assertTrue(self.backups.path(restored['protection_backup']).is_file())
        dbpath = Path(restored['workspace'])/'data/overseas.sqlite3'
        reopened = ClosedStore(dbpath, False)
        self.assertEqual(reopened.customer(self.customer['id']), self.customer)
        self.assertEqual(reopened.records(self.customer['id'], {})['items'], [self.record])
        did = self.document['document_id']
        self.assertEqual(reopened.knowledge_original(did, 1)[0], b'DEMO original version one')
        self.assertEqual(reopened.knowledge_original(did, 2)[0], b'DEMO original version two')
        self.assertEqual(len(reopened.knowledge_document(did)['versions']), 2)
        self.assertTrue(reopened.knowledge_document(did)['review_history'])
        with reopened.db() as db:
            self.assertEqual(db.execute('SELECT owner,pid,rid FROM search_plan_ledger').fetchone(), (None, None, None))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('DELETE FROM kb_reviews')
        self.assertFalse((Path(restored['workspace'])/'data/credentials').exists())
        with self.assertRaises(AppError) as error:
            self.backups.restore({'ticket': ticket, 'confirm': True})
        self.assertEqual(error.exception.code, 'restore_exists')

    def test_excludes_credential_tables_files_runtime_and_unknown_config(self):
        with self.store.db() as db:
            db.execute("UPDATE config SET data=json_set(data,'$.key','SYNTHETIC-CONFIG-SECRET')")
        with zipfile.ZipFile(io.BytesIO(self.package())) as z:
            self.assertEqual(set(z.namelist()), {'manifest.json', 'business.json'})
            body = z.read('business.json')
            for marker in (b'SYNTHETIC-CREDENTIAL', b'SYNTHETIC-SESSION', b'SYNTHETIC-VAULT', b'SYNTHETIC-CONFIG-SECRET', b'runtime-rid', b'synthetic-owner'):
                self.assertNotIn(marker, body)

    def test_damaged_missing_path_traversal_duplicate_and_symlink_rejected(self):
        body = self.package()
        payloads = [b'', b'not a zip', body[:-20], self.repack(body, extra=('../escape.txt', 'bad')),
                    self.repack(body, extra=('business.json', '{}'))]
        info = zipfile.ZipInfo('manifest.json')
        info.create_system = 3
        info.external_attr = 0o120777 << 16
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as z:
            z.writestr(info, 'business.json')
            z.writestr('business.json', '{}')
        payloads.append(stream.getvalue())
        for payload in payloads:
            with self.subTest(length=len(payload)), self.assertRaises(AppError):
                self.backups.preview(payload)
        self.assertFalse((self.root/'escape.txt').exists())
        with self.assertRaises(AppError):
            self.backups.path('../escape')
        with self.assertRaises(AppError):
            self.backups.path('manual-20000101-000000-00000000')

    def test_hash_schema_and_version_rejected(self):
        body = self.package()
        for corrupt in (
            self.repack(body, change_manifest=lambda m: m.update(sha256='0'*64)),
            self.repack(body, change_manifest=lambda m: m.update(protocol='future-v99')),
            self.repack(body, change_data=lambda d: d['tables']['customers']['columns'].append('future_column')),
            self.repack(body, change_data=lambda d: d['tables'].update(model_config={'columns': ['id', 'data'], 'rows': []})),
        ):
            with self.assertRaises(AppError):
                self.backups.preview(corrupt)
        with patch('backend.backups.MAX_BYTES', 100):
            with self.assertRaises(AppError):
                self.backups.preview(body)

    def test_missing_confirmation_and_ticket_leave_current_data_untouched(self):
        state = self.store.state()
        for data in ({'ticket': '0'*32}, {'ticket': '0'*32, 'confirm': True}, {'ticket': '../bad', 'confirm': True}):
            with self.assertRaises(AppError):
                self.backups.restore(data)
        self.assertEqual(self.store.state(), state)

    def test_broken_references_fail_restore_without_overwriting_current(self):
        body = self.repack(self.package(), change_data=lambda d: d['tables']['records']['rows'][0].__setitem__(1, 'missing-customer'))
        ticket = self.backups.preview(body)['ticket']
        state = self.store.state()
        with self.assertRaises(AppError) as error:
            self.backups.restore({'ticket': ticket, 'confirm': True})
        self.assertEqual(error.exception.code, 'restore_failed')
        self.assertEqual(self.store.state(), state)
        self.assertTrue(list(self.backups.directory.glob('before-restore-*.zip')))
        self.assertTrue((self.root/'restored-workspaces'/ticket/'RESTORE_FAILED.txt').exists())

    def test_automatic_retains_previous_files_and_reports_failure(self):
        self.backups.automatic()
        first = set(self.backups.directory.glob('*.zip'))
        self.backups.automatic()
        self.assertEqual(set(self.backups.directory.glob('*.zip')), first)
        self.backups.automatic(changed=True)
        self.assertGreater(len(list(self.backups.directory.glob('*.zip'))), len(first))
        with patch.object(self.backups, 'create', side_effect=OSError('Synthetic disk full')):
            self.backups.automatic(changed=True)
        self.assertTrue(self.backups.status()['automatic_error'])
        self.assertTrue(all(p.exists() for p in first))

    def test_snapshot_is_consistent_during_concurrent_commit(self):
        stop = threading.Event()
        with self.store.db() as db:
            db.execute("UPDATE customers SET data=json_set(data,'$.facts','0') WHERE id=?", (self.customer['id'],))
            db.execute("UPDATE records SET data=json_set(data,'$.text','0') WHERE id=?", (self.record['id'],))
        def writer():
            number = 0
            while not stop.is_set():
                number += 1
                with self.store.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    db.execute("UPDATE customers SET data=json_set(data,'$.facts',?) WHERE id=?", (str(number), self.customer['id']))
                    db.execute("UPDATE records SET data=json_set(data,'$.text',?) WHERE id=?", (str(number), self.record['id']))
                stop.wait(.002)
        thread = threading.Thread(target=writer)
        thread.start()
        try:
            for _ in range(5):
                _, tables, _ = self.backups.inspect(self.package())
                customer = json.loads(tables['customers']['rows'][0][1])
                record = json.loads(tables['records']['rows'][0][2])
                self.assertEqual(customer['facts'], record['text'])
        finally:
            stop.set();thread.join()


class DesktopSetupTests(unittest.TestCase):
    def test_skip_then_create_shortcut_without_replacing_existing(self):
        from backend.desktop_setup import DesktopSetup
        import base64
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root/'app';app.mkdir()
            (app/'Overseas.exe').write_bytes(b'SYNTHETIC EXE - not executed')
            work = root/'work';work.mkdir()
            destination = root/'synthetic-desktop';destination.mkdir()
            protected = destination/'Overseas 工作区.lnk';protected.write_bytes(b'PRESERVE')
            setup = DesktopSetup(work, app, {'website_extraction': 'native'}, 'search', destination)
            self.assertTrue(setup.status()['prompt'])
            self.assertFalse(setup.dismiss()['prompt'])
            def powershell(command, **kwargs):
                script = base64.b64decode(command[-1]).decode('utf-16le')
                payload = script.split("FromBase64String('")[1].split("')")[0]
                details = json.loads(base64.b64decode(payload))
                self.assertEqual(details['target'], str(app/'Overseas.exe'))
                self.assertEqual(details['working'], str(app))
                self.assertEqual(details['icon'], str(app/'Overseas.exe')+',0')
                self.assertIn(str(work/'runtime/shortcut-launch.json'), details['arguments'])
                Path(details['link']).write_bytes(b'SYNTHETIC LINK')
            with patch('backend.desktop_setup.subprocess.run', side_effect=powershell):
                first = setup.create()['shortcut']
                second = setup.create()['shortcut']
            self.assertNotEqual(first, second)
            self.assertEqual(Path(first).read_bytes(), b'SYNTHETIC LINK')
            self.assertEqual(protected.read_bytes(), b'PRESERVE')
            profile = json.loads((work/'runtime/shortcut-launch.json').read_text(encoding='utf-8'))
            self.assertEqual(profile['workspace'], str(work))


class BackupHTTPTests(unittest.TestCase):
    def test_ui_routes_csrf_export_binary_restore_and_automatic_save(self):
        import server
        with tempfile.TemporaryDirectory() as directory:
            store = ClosedStore(Path(directory)/'data/overseas.sqlite3', False)
            backups = Backups(store, directory)
            old = server.STATIC_ROOT
            server.STATIC_ROOT = Path(__file__).resolve().parents[1]/'runtime/frontend/static'
            http = server.make_server(store, port=0, backup_service=backups)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            origin = 'http://127.0.0.1:'+str(http.server_port)
            def call(path, data=None, csrf=None, binary=False):
                headers = {'Content-Type': 'application/zip' if binary else 'application/json'}
                if csrf:
                    headers['X-CSRF-Token'] = csrf
                payload = data if binary else json.dumps(data).encode() if data is not None else None
                with urllib.request.urlopen(urllib.request.Request(origin+path, payload, headers)) as r:
                    return json.load(r)
            try:
                with urllib.request.urlopen(origin+'/backups') as r:
                    self.assertIn('本地备份'.encode(), r.read())
                csrf = call('/api/backups/status')['csrf']
                with self.assertRaises(urllib.error.HTTPError) as error:
                    call('/api/backups/export', {})
                self.assertEqual(error.exception.code, 403)
                customer = call('/api/customer', {'name': 'DEMO HTTP', 'type': TYPES[0], 'stage': STAGES[0]}, csrf)
                self.assertTrue(list(backups.directory.glob('auto-*.zip')))
                result = call('/api/backups/export', {}, csrf)
                with urllib.request.urlopen(origin+result['download']) as r:
                    body = r.read()
                    self.assertEqual(r.headers['Content-Type'], 'application/zip')
                preview = call('/api/backups/preview', body, csrf, binary=True)
                restored = call('/api/backups/restore', {'ticket': preview['ticket'], 'confirm': True}, csrf)
                self.assertEqual(ClosedStore(Path(restored['workspace'])/'data/overseas.sqlite3', False).customer(customer['id'])['name'], 'DEMO HTTP')
            finally:
                http.shutdown();http.server_close();thread.join();server.STATIC_ROOT = old


if __name__ == '__main__':
    unittest.main()
