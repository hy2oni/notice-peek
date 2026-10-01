from dataclasses import replace
from contextlib import closing
from html import escape
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from notice_peek.collector import HttpClient, parse_page
from notice_peek.models import Cancelled, FetchError, Notice, ParseError, SOURCES, StorageError
from notice_peek.service import SyncService
from notice_peek.storage import Store


def listing(ids, offset=0, pinned=("900",), titles=None, dates=None, hit="1"):
    """Synthetic HTML following the inspected public board structure."""
    titles, dates = titles or {}, dates or {}
    rows = []
    for article_id, is_pin in [(v, True) for v in pinned] + [(v, False) for v in ids[offset:offset+10]]:
        title = escape(titles.get(article_id, f"공지 {article_id}"))
        day = dates.get(article_id, "2026.10.01")
        rows.append(f'''<tr class="{'b-top-box' if is_pin else ''}">
            <td class="b-num-box">{'공지' if is_pin else article_id}</td>
            <td><div class="b-title-box"><a href="?mode=view&amp;articleNo={article_id}&amp;article.offset={offset}"><span>{title}</span></a></div>
            <span class="b-date">{day}</span><span class="hit">조회수 {hit}</span></td></tr>''')
    return (f'<div class="b-total-wrap">총 <span>{len(ids)}</span> 개의 게시물이 등록되어 있습니다</div>'
            '<table class="board-table"><tbody>' + ''.join(rows) + '</tbody></table>'
            f'<div class="b-paging-wrap"><a href="?mode=list&amp;article.offset={offset+10}">다음</a></div>')


class FakeClient:
    def __init__(self, ids=None, pinned=("900",), fail=None, hook=None):
        self.ids = ids if ids is not None else [str(i) for i in range(100, 60, -1)]
        self.pinned, self.fail, self.hook = pinned, fail, hook
        self.calls = []

    def get(self, url, cancel):
        self.calls.append(url)
        source_id = "cse" if "/cse/" in url else "swedu"
        offset = int(parse_qs(urlsplit(url).query)["article.offset"][0])
        if self.hook:
            self.hook(source_id, offset)
        if self.fail and self.fail(source_id, offset):
            raise FetchError("모의 네트워크 단절")
        return listing(self.ids, offset, self.pinned)


class ParserTests(unittest.TestCase):
    def test_title_date_url_pin_and_no_false_change_from_views(self):
        source = SOURCES[0]
        page = parse_page(listing(["5"], titles={"5": "안내 & 신청"}), source)
        changed_views = parse_page(listing(["5"], titles={"5": "안내 & 신청"}, hit="999"), source)
        self.assertEqual(page, changed_views)
        self.assertTrue(page.notices[0].pinned)
        self.assertEqual(page.notices[1].title, "안내 & 신청")
        self.assertEqual(page.notices[1].published, "2026-10-01")
        self.assertEqual(page.notices[1].url, source.url + "?mode=view&articleNo=5")
        self.assertIsNone(page.next_offset)

    def test_date_missing_is_explicit_and_bad_date_is_error(self):
        self.assertIsNone(parse_page(listing(["5"], dates={"5": "-"}), SOURCES[0]).notices[-1].published)
        with self.assertRaises(ParseError):
            parse_page(listing(["5"], dates={"5": "broken"}), SOURCES[0])

    def test_bad_pages_are_not_empty_success(self):
        for html in ("<html>login</html>", listing([]), listing(["5"]).replace('class="b-date"', 'class="renamed"'),
                     listing(["5"]).replace("articleNo=5", "articleNo=x"),
                     listing(["5"]).replace("?mode=view", "https://example.com/?mode=view"),
                     listing(["5"]).replace("<span>1</span>", "<span>50</span>")):
            with self.subTest(html=html), self.assertRaises(ParseError):
                parse_page(html, SOURCES[0])

    def test_missing_next_page_is_failure(self):
        html = listing([str(i) for i in range(20)]).replace('class="b-paging-wrap"', 'class="changed"')
        with self.assertRaises(ParseError):
            parse_page(html, SOURCES[0])

    def test_real_minimized_samples(self):
        for source, pinned, total in [(SOURCES[0], 4, 1849), (SOURCES[1], 2, 198)]:
            html = (Path(__file__).parent / "fixtures" / f"{source.id}-list.html").read_text(encoding="utf-8")
            page = parse_page(html, source)
            self.assertEqual(len(page.notices), 10 + pinned)
            self.assertEqual(page.total, total)
            self.assertEqual(page.next_offset, 10)


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "notices.sqlite3"
        self.store = Store(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def sync(self, client=None, sources=SOURCES, cancel=None, progress=None):
        return SyncService(self.store, client or FakeClient(), sources).sync(cancel, progress)

    def test_initial_30_plus_pins_all_read_and_per_source_identity(self):
        client = FakeClient()
        results = self.sync(client)
        self.assertEqual(len(client.calls), 6)
        self.assertTrue(all(r.initial and r.added == 31 and not r.new_ids for r in results))
        rows = self.store.list_notices()
        self.assertEqual(len(rows), 62)
        self.assertTrue(all(r["is_read"] and not r["favorite"] for r in rows))
        self.assertEqual(len(Store(self.path).list_notices()), 62)

    def test_repeat_stops_at_first_page_and_keeps_manual_flags(self):
        self.sync()
        self.store.set_read("cse", "100", False)
        self.store.set_favorite("cse", "100", True)
        client = FakeClient()
        results = self.sync(client)
        self.assertEqual(len(client.calls), 2)
        self.assertTrue(all(r.added == 0 and not r.initial for r in results))
        rows = Store(self.path).list_notices("cse", unread_only=True, favorites_only=True)
        self.assertEqual([r["article_id"] for r in rows], ["100"])

    def test_new_backlog_crosses_pages_despite_known_pins(self):
        self.sync()
        client = FakeClient(ids=[str(i) for i in range(125, 60, -1)])
        results = self.sync(client)
        self.assertEqual(len(client.calls), 6)
        self.assertTrue(all(r.added == 25 and len(r.new_ids) == 25 for r in results))
        self.assertEqual(len(self.store.list_notices(unread_only=True)), 50)

    def test_old_pinned_becoming_regular_does_not_stop_early(self):
        self.sync()
        client = FakeClient(ids=["900"] + [str(i) for i in range(125, 60, -1)], pinned=())
        self.assertTrue(all(r.added == 25 for r in self.sync(client)))
        self.assertEqual(len(client.calls), 6)

    def test_existing_notice_never_overwritten(self):
        self.sync()
        self.store.set_favorite("cse", "100", True)
        self.store.set_read("cse", "100", False)
        changed = Notice("cse", "100", "수정된 제목", "2027-01-01", "https://example.com")
        self.store.save_collection("cse", [changed])
        row = self.store.list_notices("cse", favorites_only=True)[0]
        self.assertEqual(row["title"], "공지 100")
        self.assertFalse(row["is_read"])
        self.assertTrue(row["favorite"])

    def test_source_failure_isolated_and_first_success_remains_initial(self):
        results = self.sync(FakeClient(fail=lambda source, offset: source == "cse"))
        self.assertEqual([r.status for r in results], ["network_error", "success"])
        self.assertFalse(self.store.state("cse")["initialized"])
        self.assertEqual(len(self.store.list_notices()), 31)
        self.assertTrue(self.sync()[0].initial)

    def test_later_page_failure_does_not_partially_save(self):
        self.sync()
        before = self.store.state("cse")["last_success"]
        results = self.sync(FakeClient(ids=[str(i) for i in range(125, 60, -1)],
                                      fail=lambda source, offset: source == "cse" and offset == 10))
        self.assertEqual(results[0].status, "network_error")
        self.assertEqual(len(self.store.list_notices("cse")), 31)
        self.assertEqual(self.store.state("cse")["last_success"], before)
        self.assertEqual(results[1].added, 25)

    def test_initial_partial_failure_does_not_set_baseline(self):
        results = self.sync(FakeClient(fail=lambda source, offset: offset == 10))
        self.assertTrue(all(r.status == "network_error" for r in results))
        self.assertEqual(self.store.list_notices(), [])
        self.assertFalse(self.store.state("cse")["initialized"])

    def test_cancel_during_collection_preserves_database(self):
        cancel = Event()
        results = self.sync(cancel=cancel, progress=lambda source, pages: cancel.set())
        self.assertTrue(all(r.status == "cancelled" for r in results))
        self.assertEqual(self.store.list_notices(), [])

    def test_user_changes_during_fetch_survive(self):
        self.sync()
        def edit(source, offset):
            self.store.set_read(source, "100", False)
            self.store.set_favorite(source, "100", True)
        self.sync(FakeClient(hook=edit))
        self.assertEqual(len(self.store.list_notices(unread_only=True, favorites_only=True)), 2)

    def test_parse_failure_does_not_delete_records(self):
        self.sync()
        client = FakeClient()
        client.get = lambda url, cancel: "<html>접근 제한</html>"
        self.assertTrue(all(r.status == "parse_error" for r in self.sync(client)))
        self.assertEqual(len(self.store.list_notices()), 62)

    def test_storage_failure_is_not_success_and_other_source_continues(self):
        save = self.store.save_collection
        def fail_cse(source, notices):
            if source == "cse":
                raise StorageError("모의 디스크 오류")
            return save(source, notices)
        with patch.object(self.store, "save_collection", side_effect=fail_cse):
            results = self.sync()
        self.assertEqual([r.status for r in results], ["storage_error", "success"])

    def test_transaction_rollback(self):
        good = Notice("cse", "1", "공지", None, "https://www.yu.ac.kr")
        with self.assertRaises(StorageError):
            self.store.save_collection("cse", [good, replace(good, source_id="swedu")])
        self.assertEqual(self.store.list_notices(), [])
        self.assertFalse(self.store.state("cse")["initialized"])

    def test_corrupt_or_newer_database_not_reset(self):
        corrupt = Path(self.temp.name) / "corrupt.db"
        corrupt.write_bytes(b"not a sqlite database")
        with self.assertRaises(StorageError):
            Store(corrupt)
        self.assertEqual(corrupt.read_bytes(), b"not a sqlite database")
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("PRAGMA user_version=99")
        with self.assertRaises(StorageError):
            Store(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 99)

    def test_overlapping_sync_is_rejected(self):
        service = SyncService(self.store, FakeClient())
        def overlap(source, pages):
            with self.assertRaises(RuntimeError):
                service.sync()
        service.sync(progress=overlap)


class NetworkTests(unittest.TestCase):
    def test_network_retries_are_bounded(self):
        cancel = Event()
        with patch("notice_peek.collector.urlopen", side_effect=URLError("offline")) as open_url, \
             patch.object(cancel, "wait", return_value=False):
            with self.assertRaises(FetchError):
                HttpClient().get(SOURCES[0].url, cancel)
        self.assertEqual(open_url.call_count, 2)

    def test_access_denial_is_not_retried(self):
        for code in (403, 429):
            with patch("notice_peek.collector.urlopen", side_effect=HTTPError(SOURCES[0].url, code, "denied", {}, None)) as open_url:
                with self.assertRaises(FetchError):
                    HttpClient().get(SOURCES[0].url, Event())
            self.assertEqual(open_url.call_count, 1)

    def test_cancel_before_request(self):
        cancel = Event()
        cancel.set()
        with patch("notice_peek.collector.urlopen") as open_url:
            with self.assertRaises(Cancelled):
                HttpClient().get(SOURCES[0].url, cancel)
        open_url.assert_not_called()


if __name__ == "__main__":
    unittest.main()
