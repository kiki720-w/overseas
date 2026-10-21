import sqlite3,tempfile,unittest
from pathlib import Path
from backend.store import Store

class StoreLifecycleTests(unittest.TestCase):
    def test_context_commit_closes_before_windows_file_operation(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'synthetic.sqlite3';store=Store(path,False);connection=store.db()
            with connection as db:db.execute('CREATE TABLE synthetic(value TEXT)');db.execute("INSERT INTO synthetic VALUES('fixture')")
            with self.assertRaises(sqlite3.ProgrammingError):connection.execute('SELECT 1')
            moved=path.with_name('moved.sqlite3');path.rename(moved)
            reopened=sqlite3.connect(moved)
            try:self.assertEqual(reopened.execute('SELECT value FROM synthetic').fetchone()[0],'fixture')
            finally:reopened.close()
    def test_rollback_closes_and_explicit_raw_access_remains_supported(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'synthetic.sqlite3',False)
            raw=store.db();raw.execute('CREATE TABLE synthetic(value TEXT)');raw.commit();raw.close()
            connection=store.db()
            with self.assertRaises(RuntimeError):
                with connection as db:db.execute("INSERT INTO synthetic VALUES('rollback')");raise RuntimeError('synthetic failure')
            with self.assertRaises(sqlite3.ProgrammingError):connection.execute('SELECT 1')
            with store.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM synthetic').fetchone()[0],0)
