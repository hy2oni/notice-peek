from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3

from .models import StorageError


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
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise StorageError("더 새로운 버전의 데이터베이스입니다. 앱을 업데이트해 주세요.")
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
            # Fail visibly on an incompatible schema instead of replacing it.
            db.execute("SELECT source_id, article_id, title, published, url, originally_pinned, "
                       "is_read, favorite, discovered_at FROM notices LIMIT 0")
            db.execute("SELECT source_id, initialized, last_success, last_attempt, status, message FROM sources LIMIT 0")

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

    def state(self, source_id):
        with self._connect() as db:
            row = db.execute("SELECT * FROM sources WHERE source_id=?", (source_id,)).fetchone()
            return dict(row) if row else {"source_id": source_id, "initialized": 0}

    def known_regular(self, source_id):
        with self._connect() as db:
            return {r[0] for r in db.execute(
                "SELECT article_id FROM notices WHERE source_id=? AND originally_pinned=0", (source_id,))}

    def save_collection(self, source_id, notices):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT initialized FROM sources WHERE source_id=?", (source_id,)).fetchone()
            initial = not (row and row[0])
            added = []
            now = _now()
            for n in notices:
                if n.source_id != source_id:
                    raise StorageError("공지 출처가 일치하지 않아 저장을 취소했습니다.")
                cursor = db.execute("""INSERT INTO notices
                    (source_id, article_id, title, published, url, originally_pinned, is_read, discovered_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(source_id, article_id) DO NOTHING""",
                    (source_id, n.article_id, n.title, n.published, n.url, int(n.pinned), int(initial), now))
                if cursor.rowcount:
                    added.append(n.article_id)
            db.execute("""INSERT INTO sources VALUES (?, 1, ?, ?, 'success', '')
                ON CONFLICT(source_id) DO UPDATE SET initialized=1, last_success=excluded.last_success,
                last_attempt=excluded.last_attempt, status='success', message=''""", (source_id, now, now))
            return initial, tuple(added)

    def record_failure(self, source_id, status, message):
        with self._connect() as db:
            db.execute("""INSERT INTO sources (source_id, last_attempt, status, message) VALUES (?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET last_attempt=excluded.last_attempt,
                status=excluded.status, message=excluded.message""", (source_id, _now(), status, message))

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
