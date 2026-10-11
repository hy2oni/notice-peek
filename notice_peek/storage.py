from contextlib import closing, contextmanager
from datetime import datetime, timezone
import os
import json
from uuid import uuid4
from pathlib import Path
import sqlite3

from .models import Source, SOURCES, StorageError
from .catalog import load_catalog


def default_database() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        raise StorageError("Windows 사용자 앱 데이터 경로를 찾을 수 없습니다.")
    return Path(base) / "notice-peek" / "notices.sqlite3"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    """Short-lived connections allow a future UI and worker to share the database."""

    def __init__(self, path=None):
        self.path = Path(path) if path is not None else default_database()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError("데이터 폴더를 만들 수 없습니다.") from exc
        catalog = load_catalog()
        with self._connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise StorageError("더 새로운 버전의 데이터베이스입니다. 앱을 업데이트해 주세요.")
            if version == 1:
                backup = self.path.with_name(self.path.name + '.v1-backup-' + uuid4().hex[:8])
                with closing(sqlite3.connect(backup)) as destination:
                    db.backup(destination)
            db.execute("BEGIN IMMEDIATE")
            if version == 0:
                if db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                    raise StorageError("알 수 없는 데이터베이스입니다. 기존 파일을 유지합니다.")
                db.execute("""CREATE TABLE notices (
                    source_id TEXT NOT NULL, article_id TEXT NOT NULL,
                    title TEXT NOT NULL, published TEXT, url TEXT NOT NULL,
                    originally_pinned INTEGER NOT NULL CHECK(originally_pinned IN (0,1)),
                    is_read INTEGER NOT NULL CHECK(is_read IN (0,1)),
                    favorite INTEGER NOT NULL DEFAULT 0 CHECK(favorite IN (0,1)),
                    discovered_at TEXT NOT NULL,
                    PRIMARY KEY(source_id, article_id))""")
                db.execute("""CREATE TABLE sources (
                    source_id TEXT PRIMARY KEY, initialized INTEGER NOT NULL DEFAULT 0,
                    last_success TEXT, last_attempt TEXT, status TEXT, message TEXT)""")
                db.execute("PRAGMA user_version=1")
            if version < 2:
                db.execute("SELECT source_id, initialized, last_success, last_attempt, status, message FROM sources LIMIT 0")
                db.execute("ALTER TABLE sources RENAME TO subscriptions")
                db.execute("ALTER TABLE subscriptions ADD COLUMN generation TEXT NOT NULL DEFAULT ''")
                db.execute("CREATE TABLE board_catalog (source_id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL)")
                db.execute("PRAGMA user_version=2")
            for board in catalog:
                name = board['organization'] if board['id'] in ('cse', 'swedu') else board['organization'] + ' · ' + board['name']
                db.execute("""INSERT INTO board_catalog VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(source_id) DO UPDATE SET name=excluded.name, url=excluded.url,
                    status=excluded.status, payload=excluded.payload""",
                    (board['id'], name, board['url'], board['status'], json.dumps(board, ensure_ascii=False)))
            if version == 1:
                # The legacy UI subscribed to both boards even before a first sync.
                for source in SOURCES:
                    db.execute("INSERT OR IGNORE INTO subscriptions (source_id) VALUES (?)", (source.id,))
                db.execute("UPDATE subscriptions SET generation=?", (uuid4().hex,))
            if db.execute("SELECT 1 FROM subscriptions s LEFT JOIN board_catalog c USING(source_id) WHERE c.source_id IS NULL LIMIT 1").fetchone():
                raise StorageError("출처 정보를 찾을 수 없어 기존 DB 이관을 취소했습니다.")
            if db.execute("SELECT 1 FROM notices n LEFT JOIN subscriptions s USING(source_id) WHERE s.source_id IS NULL LIMIT 1").fetchone():
                raise StorageError("구독 없는 공지 기록이 있어 기존 DB 이관을 취소했습니다.")
            # Fail visibly on an incompatible schema instead of replacing it.
            db.execute("SELECT source_id, article_id, title, published, url, originally_pinned, "
                       "is_read, favorite, discovered_at FROM notices LIMIT 0")
            db.execute("SELECT source_id, initialized, last_success, last_attempt, status, message, generation FROM subscriptions LIMIT 0")
            db.execute("CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            complete = version == 1 or bool(db.execute("SELECT 1 FROM subscriptions LIMIT 1").fetchone())
            db.execute("INSERT OR IGNORE INTO preferences VALUES ('selection_complete', ?)", (str(int(complete)),))

    @contextmanager
    def _connect(self):
        db = None
        try:
            db = sqlite3.connect(self.path, timeout=5)
            db.row_factory = sqlite3.Row
            with db:
                yield db
        except (sqlite3.Error, OSError) as exc:
            raise StorageError("로컬 기록을 읽거나 저장하지 못했습니다. 기존 파일은 초기화하지 않습니다.") from exc
        finally:
            if db is not None:
                db.close()

    def list_catalog(self):
        with self._connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM board_catalog ORDER BY name, source_id")]

    def subscriptions(self):
        with self._connect() as db:
            return tuple(Source(r[0], r[1], r[2]) for r in db.execute(
                "SELECT c.source_id, c.name, c.url FROM subscriptions s JOIN board_catalog c USING(source_id) ORDER BY s.rowid"))

    def subscribe(self, source_id):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM board_catalog WHERE source_id=?", (source_id,)).fetchone()
            if not row or row[0] != 'supported':
                raise StorageError("수집을 확인한 게시판만 구독할 수 있습니다.")
            db.execute("INSERT OR IGNORE INTO subscriptions (source_id, generation) VALUES (?, ?)", (source_id, uuid4().hex))
            db.execute("UPDATE preferences SET value='1' WHERE key='selection_complete'")

    def selection_complete(self):
        with self._connect() as db:
            return db.execute("SELECT value FROM preferences WHERE key='selection_complete'").fetchone()[0] == '1'

    def subscription_snapshot(self):
        with self._connect() as db:
            return dict(db.execute("SELECT source_id, generation FROM subscriptions"))

    def apply_selection(self, selected, collections, expected):
        """Commit the complete selection and validated initial collections together."""
        selected = set(selected)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = dict(db.execute("SELECT source_id, generation FROM subscriptions"))
            if current != expected:
                raise StorageError("다른 작업에서 출처가 변경됐습니다. 설정을 다시 열어 주세요.")
            added, removed = selected - current.keys(), current.keys() - selected
            if set(collections) != added:
                raise StorageError("추가할 출처의 검사 결과가 일치하지 않습니다.")
            for source_id in added:
                board = db.execute("SELECT status FROM board_catalog WHERE source_id=?", (source_id,)).fetchone()
                if not board or board[0] != 'supported' or not collections[source_id]:
                    raise StorageError("수집을 확인한 게시판만 추가할 수 있습니다.")
                db.execute("INSERT INTO subscriptions (source_id, generation) VALUES (?, ?)", (source_id, uuid4().hex))
                self._save_collection(db, source_id, collections[source_id])
            for source_id in removed:
                db.execute("DELETE FROM notices WHERE source_id=?", (source_id,))
                db.execute("DELETE FROM subscriptions WHERE source_id=?", (source_id,))
            db.execute("UPDATE preferences SET value='1' WHERE key='selection_complete'")
            return added, removed

    def remove_subscription(self, source_id):
        """Call only after UI confirmation: deletes notices/flags, retains catalogue."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM notices WHERE source_id=?", (source_id,))
            db.execute("DELETE FROM subscriptions WHERE source_id=?", (source_id,))

    def state(self, source_id):
        with self._connect() as db:
            row = db.execute("SELECT * FROM subscriptions WHERE source_id=?", (source_id,)).fetchone()
            return dict(row) if row else {"source_id": source_id, "initialized": 0}

    def known_regular(self, source_id):
        with self._connect() as db:
            return {r[0] for r in db.execute(
                "SELECT article_id FROM notices WHERE source_id=? AND originally_pinned=0", (source_id,))}

    def save_collection(self, source_id, notices, *, generation=None):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._save_collection(db, source_id, notices, generation)

    def _save_collection(self, db, source_id, notices, generation=None):
        row = db.execute("SELECT initialized, generation FROM subscriptions WHERE source_id=?", (source_id,)).fetchone()
        if not row or (generation is not None and row[1] != generation):
            raise StorageError("제거되었거나 선택하지 않은 출처입니다. 저장을 취소했습니다.")
        initial, added, now = not row[0], [], _now()
        for n in notices:
            if n.source_id != source_id:
                raise StorageError("공지 출처가 일치하지 않아 저장을 취소했습니다.")
            cursor = db.execute("""INSERT INTO notices
                (source_id, article_id, title, published, url, originally_pinned, is_read, discovered_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(source_id, article_id) DO NOTHING""",
                (source_id, n.article_id, n.title, n.published, n.url, int(n.pinned), int(initial), now))
            if cursor.rowcount:
                added.append(n.article_id)
        db.execute("UPDATE subscriptions SET initialized=1, last_success=?, last_attempt=?, status='success', message='' WHERE source_id=?",
                   (now, now, source_id))
        return initial, tuple(added)

    def record_failure(self, source_id, status, message, *, generation=None):
        with self._connect() as db:
            db.execute("UPDATE subscriptions SET last_attempt=?, status=?, message=? WHERE source_id=? AND (? IS NULL OR generation=?)",
                       (_now(), status, message, source_id, generation, generation))

    def list_notices(self, source_id=None, unread_only=False, favorites_only=False):
        clauses, args = [], []
        if source_id is not None:
            clauses.append("source_id=?")
            args.append(source_id)
        if unread_only:
            clauses.append("is_read=0")
        if favorites_only:
            clauses.append("favorite=1")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM notices" + where +
                " ORDER BY published IS NULL, published DESC, source_id, article_id DESC", args)]

    def set_read(self, source_id, article_id, value):
        self._set_flag("is_read", source_id, article_id, value)

    def set_favorite(self, source_id, article_id, value):
        self._set_flag("favorite", source_id, article_id, value)

    def _set_flag(self, column, source_id, article_id, value):
        with self._connect() as db:
            cursor = db.execute(f"UPDATE notices SET {column}=? WHERE source_id=? AND article_id=?",
                                (int(bool(value)), source_id, article_id))
            if not cursor.rowcount:
                raise StorageError("저장된 공지를 찾을 수 없습니다.")
