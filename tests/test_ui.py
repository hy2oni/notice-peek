from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import gc
import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from notice_peek.models import Notice, SOURCES, StorageError
from notice_peek.service import SyncService
from notice_peek.storage import Store
from notice_peek.ui import NoticeApp
from test_core import FakeClient


class UiTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "ui.sqlite3")
        for source in SOURCES:
            self.store.save_collection(source.id, [
                Notice(source.id, "100", "학교 공지 · 원문을 열어도 읽음 상태는 유지", "2026-10-01",
                       source.url + "?mode=view&articleNo=100"),
                Notice(source.id, "99", "두 번째 공지", "2026-09-30", source.url + "?mode=view&articleNo=99"),
            ])
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.temp.cleanup()
            self.skipTest(f"Tk 화면을 사용할 수 없는 환경: {exc}")
        self.root.withdraw()
        self.client = FakeClient(ids=["100", "99"], pinned=())
        self.opener = Mock(return_value=True)
        self.app = NoticeApp(self.root, self.store, SyncService(self.store, self.client),
                             auto_sync=False, opener=self.opener)
        self.root.update()

    def tearDown(self):
        self.app.cancel.set()
        if self.app.worker is not None:
            self.app.worker.join(timeout=2)
            self.assertFalse(self.app.worker.is_alive())
        try:
            if self.root.winfo_exists():
                self.app.close()
        except tk.TclError:
            pass
        self.app = None
        self.root = None
        gc.collect()  # Dispose old Tcl objects on the main thread before the next worker starts.
        self.temp.cleanup()

    def pump_until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            self.root.update()
            time.sleep(0.005)
        self.fail("UI 작업이 제한 시간 안에 끝나지 않았습니다.")

    def select(self, key="cse:100"):
        self.app.tree.selection_set(key)
        self.app.tree.focus(key)
        self.root.update()

    def test_cached_rows_visible_before_network_and_light_theme(self):
        self.assertEqual(len(self.app.tree.get_children()), 4)
        self.assertFalse(self.client.calls)
        self.assertEqual(self.root.cget("background"), "#F5F7FA")
        self.assertFalse(self.app.new_keys)

    def test_buttons_persist_flags_and_filters_do_not_fetch(self):
        self.select()
        self.app.read_button.invoke()
        self.app.favorite_button.invoke()
        self.app.source_filter.set("컴퓨터공학과")
        self.app.unread_check.invoke()
        self.app.favorite_check.invoke()
        self.assertEqual(self.app.tree.get_children(), ("cse:100",))
        row = Store(self.store.path).list_notices("cse", unread_only=True, favorites_only=True)[0]
        self.assertEqual(row["article_id"], "100")
        self.assertFalse(self.client.calls)
        self.app.read_button.invoke()
        self.assertEqual(self.app.tree.get_children(), ())
        self.assertIn("조건에 맞는", self.app.empty_label.cget("text"))

    def test_open_selected_uses_original_url_without_marking_read(self):
        self.store.set_read("cse", "100", False)
        self.app.reload()
        self.select()
        self.app.open_button.invoke()
        self.opener.assert_called_once_with(SOURCES[0].url + "?mode=view&articleNo=100")
        self.assertEqual(len(self.store.list_notices(unread_only=True)), 1)
        self.app.open_button.invoke()
        self.assertEqual(self.opener.call_count, 1)  # Accidental double-click is deduplicated.

    def test_title_click_and_star_click_have_separate_actions(self):
        self.root.deiconify()
        self.root.update()
        x, y, width, height = self.app.tree.bbox("cse:100", "title")
        self.app.tree.event_generate("<ButtonPress-1>", x=x+20, y=y+height//2)
        self.app.tree.event_generate("<ButtonRelease-1>", x=x+20, y=y+height//2)
        self.root.update()
        self.opener.assert_called_once()
        self.assertTrue(self.store.list_notices("cse")[0]["is_read"])
        x, y, width, height = self.app.tree.bbox("cse:100", "favorite")
        self.app.tree.event_generate("<ButtonPress-1>", x=x+width//2, y=y+height//2)
        self.app.tree.event_generate("<ButtonRelease-1>", x=x+width//2, y=y+height//2)
        self.root.update()
        self.assertEqual(len(self.store.list_notices(favorites_only=True)), 1)
        self.assertEqual(self.opener.call_count, 1)

    def test_refresh_keeps_ui_responsive_and_prevents_duplicate_work(self):
        started, release = Event(), Event()
        def wait(source, offset):
            started.set()
            release.wait(2)
        self.client.hook = wait
        self.app.refresh_button.invoke()
        self.pump_until(started.is_set)
        try:
            self.assertEqual(str(self.app.refresh_button.cget("state")), "disabled")
            self.app.refresh()
            self.assertEqual(len(self.client.calls), 1)
            self.select()
            self.app.read_button.invoke()
            self.app.favorite_button.invoke()
            self.app.favorite_check.invoke()
            self.assertEqual(self.app.tree.get_children(), ("cse:100",))
        finally:
            release.set()
        self.pump_until(lambda: not self.app.busy)
        self.assertEqual(len(self.client.calls), 2)
        self.assertEqual(str(self.app.refresh_button.cget("state")), "normal")
        self.assertEqual(len(self.store.list_notices(unread_only=True, favorites_only=True)), 1)
        self.assertIn("신규 0건", self.app.summary.get())

    def test_new_badges_independent_of_read_and_persist_until_exit(self):
        self.client.ids = ["101", "100", "99"]
        self.app.refresh()
        self.pump_until(lambda: not self.app.busy)
        self.assertEqual(self.app.new_keys, {"cse:101", "swedu:101"})
        self.select("cse:101")
        self.app.toggle_read()
        self.assertEqual(self.app.tree.set("cse:101", "new"), "신규")
        self.app.refresh()
        self.pump_until(lambda: not self.app.busy)
        self.assertEqual(self.app.tree.set("cse:101", "new"), "신규")
        self.assertIn("신규 0건", self.app.summary.get())

    def test_partial_failure_keeps_rows_and_explains_source(self):
        self.client.fail = lambda source, offset: source == "cse"
        self.client.ids = ["101", "100", "99"]
        self.app.refresh()
        self.pump_until(lambda: not self.app.busy)
        self.assertEqual(len(self.app.tree.get_children()), 5)
        self.assertIn("연결 실패", self.app.source_status["cse"].get())
        self.assertIn("마지막 확인", self.app.source_status["cse"].get())
        self.assertIn("신규 1건", self.app.source_status["swedu"].get())

    def test_failed_state_save_does_not_change_display(self):
        self.select()
        with patch.object(self.store, "set_read", side_effect=StorageError("저장 실패")):
            self.app.toggle_read()
        self.assertEqual(self.app.tree.set("cse:100", "read"), "읽음")
        self.assertIn("저장 실패", self.app.summary.get())

    def test_unexpected_worker_failure_restores_refresh(self):
        with patch.object(self.app.service, "sync", side_effect=RuntimeError("test failure")):
            self.app.refresh()
            self.pump_until(lambda: not self.app.busy)
        self.assertEqual(str(self.app.refresh_button.cget("state")), "normal")
        self.assertIn("마치지 못했습니다", self.app.summary.get())
        self.assertEqual(len(self.app.tree.get_children()), 4)

    def test_startup_runs_once_and_manual_refresh_cancels_pending_startup(self):
        self.app._startup_id = self.root.after(150, self.app._auto_refresh)
        self.app.refresh()
        self.pump_until(lambda: not self.app.busy)
        until = time.monotonic() + 0.25
        while time.monotonic() < until:
            self.root.update()
            time.sleep(0.005)
        self.assertEqual(len(self.client.calls), 2)
        self.assertIsNone(self.app._poll_id)

    def test_close_waits_for_cancelled_worker_then_destroys_window(self):
        started = Event()
        def wait(source, offset):
            started.set()
            self.app.cancel.wait(2)
        self.client.hook = wait
        self.app.refresh()
        self.pump_until(started.is_set)
        worker = self.app.worker
        self.app.close()
        self.pump_until(lambda: not worker.is_alive())
        self.pump_until(lambda: self.app.worker is None)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(len(Store(self.store.path).list_notices()), 4)
        with self.assertRaises(tk.TclError):
            self.root.winfo_exists()


if __name__ == "__main__":
    unittest.main()
