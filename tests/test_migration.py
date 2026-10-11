from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from notice_peek.models import Notice, StorageError
from notice_peek.service import SyncService
from notice_peek.storage import Store
from test_core import FakeClient


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'old.sqlite3'

    def legacy(self, source='cse'):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript('''
                CREATE TABLE notices (source_id TEXT NOT NULL, article_id TEXT NOT NULL,
                    title TEXT NOT NULL, published TEXT, url TEXT NOT NULL,
                    originally_pinned INTEGER NOT NULL, is_read INTEGER NOT NULL,
                    favorite INTEGER NOT NULL DEFAULT 0, discovered_at TEXT NOT NULL,
                    PRIMARY KEY(source_id,article_id));
                CREATE TABLE sources (source_id TEXT PRIMARY KEY, initialized INTEGER NOT NULL DEFAULT 0,
                    last_success TEXT,last_attempt TEXT,status TEXT,message TEXT);
                PRAGMA user_version=1;
            ''')
            db.execute('INSERT INTO notices VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                       (source, '100', '이관 전 제목', None, 'https://www.yu.ac.kr', 1, 0, 1, 'old-time'))
            db.execute("INSERT INTO sources VALUES (?,1,'success-time','attempt-time','network_error','보존할 메시지')", (source,))

    def test_legacy_records_flags_status_and_backup_preserved(self):
        self.legacy()
        with closing(sqlite3.connect(self.path)) as db:
            before = db.execute('SELECT * FROM notices').fetchall()
            state = db.execute('SELECT * FROM sources').fetchone()
        store = Store(self.path)
        self.assertEqual([s.id for s in store.subscriptions()], ['cse', 'swedu'])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT * FROM notices').fetchall(), before)
            self.assertEqual(db.execute('SELECT source_id, initialized,last_success,last_attempt,status,message FROM subscriptions WHERE source_id="cse"').fetchone(), state)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 2)
        backups = list(self.path.parent.glob('*.v1-backup-*'))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT * FROM notices').fetchall(), before)
        Store(self.path)
        self.assertEqual(len(list(self.path.parent.glob('*.v1-backup-*'))), 1)

    def test_failed_migration_rolls_back_schema_and_records(self):
        self.legacy('unknown-board')
        with self.assertRaises(StorageError):
            Store(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT source_id FROM sources').fetchone()[0], 'unknown-board')
            self.assertEqual(db.execute('SELECT favorite FROM notices').fetchone()[0], 1)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='subscriptions'").fetchone())

    def test_migration_selection_collection_removal_readd_and_restart(self):
        self.legacy()
        store = Store(self.path)
        original = store.list_notices('cse')
        board = next(b for b in store.list_catalog() if '/sta/community/notice.do' in b['url'])
        source_id = board['id']
        initial = Notice(source_id, '101', '첫 수집', None, board['url'])
        selected = {'cse', 'swedu', source_id}
        store.apply_selection(selected, {source_id: [initial]}, store.subscription_snapshot())
        self.assertEqual(store.list_notices('cse'), original)
        self.assertTrue(store.list_notices(source_id)[0]['is_read'])
        SyncService(store, FakeClient()).sync()
        self.assertEqual(store.list_notices('cse', favorites_only=True), original)
        self.assertTrue(any(not r['is_read'] for r in store.list_notices(source_id)))
        stale_generation = store.state(source_id)['generation']
        store.apply_selection({'cse', 'swedu'}, {}, store.subscription_snapshot())
        self.assertEqual(store.list_notices(source_id), [])
        store.apply_selection(selected, {source_id: [initial]}, store.subscription_snapshot())
        self.assertNotEqual(store.state(source_id)['generation'], stale_generation)
        self.assertTrue(store.list_notices(source_id)[0]['is_read'])
        reopened = Store(self.path)
        self.assertEqual({s.id for s in reopened.subscriptions()}, selected)
        self.assertTrue(reopened.selection_complete())
        self.assertEqual(reopened.list_notices('cse', favorites_only=True), original)
        self.assertEqual(len(list(self.path.parent.glob('*.v1-backup-*'))), 1)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_empty_legacy_still_preserves_two_implicit_subscriptions(self):
        self.legacy()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DELETE FROM notices')
            db.execute('DELETE FROM sources')
        self.assertEqual([s.id for s in Store(self.path).subscriptions()], ['cse', 'swedu'])

    def test_fresh_install_has_no_subscription_or_network_requests(self):
        store = Store(self.path)
        client = FakeClient()
        self.assertEqual(store.subscriptions(), ())
        self.assertEqual(SyncService(store, client).sync(), ())
        self.assertEqual(client.calls, [])
        self.assertEqual(len(store.list_catalog()), 272)
        with self.assertRaises(StorageError):
            store.subscribe('does-not-exist')
        unsupported = next(b for b in store.list_catalog() if b['status'] != 'supported')
        with self.assertRaises(StorageError):
            store.subscribe(unsupported['id'])

    def test_removal_deletes_only_that_board_and_readd_starts_read(self):
        store = Store(self.path)
        for source in ('cse','swedu'):
            store.subscribe(source)
            store.save_collection(source,[Notice(source,'1','공지',None,'https://www.yu.ac.kr')])
            store.set_read(source,'1',False)
            store.set_favorite(source,'1',True)
        store.remove_subscription('cse')
        self.assertEqual(store.list_notices('cse'), [])
        self.assertEqual(len(store.list_notices('swedu', favorites_only=True)),1)
        self.assertEqual(len(store.list_catalog()),272)
        store.subscribe('cse')
        initial, _ = store.save_collection('cse',[Notice('cse','1','재등록',None,'https://www.yu.ac.kr')])
        self.assertTrue(initial)
        self.assertTrue(store.list_notices('cse')[0]['is_read'])
        self.assertFalse(store.list_notices('cse')[0]['favorite'])

    def test_deleted_and_readded_during_fetch_rejects_stale_collection(self):
        store = Store(self.path)
        store.subscribe('cse')
        def remove(source, offset):
            if offset == 0:
                store.remove_subscription(source)
                store.subscribe(source)
        result = SyncService(store,FakeClient(hook=remove)).sync()[0]
        self.assertEqual(result.status, 'storage_error')
        self.assertEqual(store.list_notices(), [])
        self.assertFalse(store.state('cse')['initialized'])
        self.assertIsNone(store.state('cse')['status'])

    def test_removed_during_fetch_cannot_be_resurrected_by_failure(self):
        store = Store(self.path)
        store.subscribe('cse')
        client = FakeClient(hook=lambda source, offset: store.remove_subscription(source))
        self.assertEqual(SyncService(store,client).sync()[0].status,'storage_error')
        self.assertEqual(store.subscriptions(),())
        self.assertEqual(store.list_notices(),[])
        self.assertEqual(Store(self.path).subscriptions(),())

    def test_sync_uses_selected_catalogue_board_instead_of_fixed_sources(self):
        store = Store(self.path)
        board = next(b for b in store.list_catalog() if '/sta/community/notice.do' in b['url'])
        store.subscribe(board['id'])
        store.subscribe(board['id'])
        client = FakeClient()
        result = SyncService(store,client).sync()
        self.assertEqual(len(result),1)
        self.assertEqual(result[0].source_id,board['id'])
        self.assertEqual(result[0].added,21)
        self.assertEqual(len(client.calls),2)
        self.assertTrue(all('/sta/' in url for url in client.calls))
        self.assertTrue(all(row['is_read'] for row in store.list_notices()))
