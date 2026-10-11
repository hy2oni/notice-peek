from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import gc
import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from notice_peek.models import Notice, StorageError
from notice_peek.service import SyncService
from notice_peek.storage import Store
from notice_peek.ui import NoticeApp
from test_core import FakeClient


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = Store(Path(self.temp.name)/'settings.sqlite3')
        self.root = tk.Tk()
        self.root.withdraw()
        self.client = FakeClient()
        self.opener = Mock(return_value=True)
        self.app = NoticeApp(self.root,self.store,SyncService(self.store,self.client),
                             auto_sync=False,opener=self.opener,registration_client=self.client)
        self.root.update()

    def tearDown(self):
        if self.app.settings is not None:
            self.app.settings.cancel.set()
            if self.app.settings.worker is not None:
                self.app.settings.worker.join(2)
        try:
            if self.root.winfo_exists():
                self.app.close()
                self.root.update()
        except tk.TclError:
            pass
        self.app = self.root = None
        gc.collect()
        self.temp.cleanup()

    def pump(self, predicate, timeout=3):
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            self.root.update()
            if predicate(): return
            time.sleep(.005)
        self.fail('설정창 작업이 제한 시간 내에 끝나지 않았습니다.')

    def open(self):
        self.root.deiconify()
        self.app.open_settings()
        self.root.update()
        return self.app.settings

    def save(self, dialog):
        dialog.save()
        self.pump(lambda: self.app.settings is None or not dialog.busy)

    def test_first_run_empty_selection_saved_without_reopening(self):
        self.assertFalse(self.store.selection_complete())
        self.app._startup()
        dialog=self.app.settings
        self.assertIsNotNone(dialog)
        self.assertEqual(dialog.selected,set())
        self.assertFalse(self.client.calls)
        self.save(dialog)
        self.assertTrue(self.store.selection_complete())
        self.assertEqual(self.store.subscriptions(),())
        self.assertIn('출처 설정',self.app.empty_label.cget('text'))
        self.app._startup()
        self.assertIsNone(self.app.settings)
        self.assertFalse(self.client.calls)
        self.assertTrue(Store(self.store.path).selection_complete())

    def test_search_category_checkboxes_and_unavailable_choices(self):
        dialog=self.open()
        dialog.search.set('컴퓨터 공지')
        self.root.update()
        self.assertIn('cse',dialog.tree.get_children())
        dialog.category.set('학과·학부')
        dialog.render()
        self.assertTrue(all(dialog.catalog[k]['category']=='학과·학부' for k in dialog.tree.get_children()))
        x,y,w,h=dialog.tree.bbox('cse','check')
        dialog.tree.event_generate('<ButtonPress-1>',x=x+w//2,y=y+h//2)
        dialog.tree.event_generate('<ButtonRelease-1>',x=x+w//2,y=y+h//2)
        self.root.update()
        self.assertEqual(dialog.selected,{'cse'})
        dialog.search.set('절대없는검색어')
        self.assertEqual(dialog.tree.get_children(),())
        self.assertEqual(dialog.selected,{'cse'})
        dialog.search.set('')
        dialog.category.set('전체 분류')
        dialog.selected_only.set(True)
        dialog.render()
        self.assertEqual(dialog.tree.get_children(),('cse',))
        unavailable=next(b for b in dialog.catalog.values() if b['status']=='unsupported')
        dialog.toggle(unavailable['id'])
        self.assertNotIn(unavailable['id'],dialog.selected)
        self.assertIn('수집할 수 없는',dialog.message.get())

    def test_registration_reuses_validated_initial_rows_and_updates_dynamic_ui(self):
        dialog=self.open()
        board=next(b for b in dialog.catalog.values() if '/sta/community/notice.do' in b['url'])
        dialog.toggle(board['id'])
        self.save(dialog)
        self.assertIsNone(self.app.settings)
        self.assertEqual(len(self.client.calls),2)  # Validation is also initial collection.
        self.assertEqual(len(self.store.list_notices()),21)
        self.assertTrue(all(r['is_read'] for r in self.store.list_notices()))
        self.assertEqual(set(self.app.source_status),{board['id']})
        self.assertIn(self.app.names[board['id']],self.app.source_combo['values'])
        self.assertEqual(len(self.app.tree.get_children()),21)
        key=self.app.tree.get_children()[0]
        self.app.tree.selection_set(key)
        self.app.open_selected()
        self.opener.assert_called_once()
        self.assertTrue(all('/sta/' in url for url in self.client.calls))
        self.assertFalse(self.app.new_keys)
        self.app.refresh()
        self.pump(lambda: not self.app.busy)
        self.assertEqual(len(self.client.calls),3)

    def test_one_failed_addition_keeps_all_original_subscriptions_and_flags(self):
        self.store.subscribe('cse')
        self.store.save_collection('cse',[Notice('cse','100','기존',None,'https://www.yu.ac.kr')])
        self.store.set_read('cse','100',False)
        self.store.set_favorite('cse','100',True)
        before=self.store.list_notices()
        dialog=self.open()
        dialog.toggle('cse')
        dialog.toggle('swedu')
        board=next(b for b in dialog.catalog.values() if '/sta/community/notice.do' in b['url'])
        dialog.toggle(board['id'])
        original_get=self.client.get
        def fail(url,cancel):
            if '/sta/' in url: return '<html>구조 변경</html>'
            return original_get(url,cancel)
        self.client.get=fail
        with patch('notice_peek.settings_ui.messagebox.askyesno') as confirm:
            self.save(dialog)
        confirm.assert_not_called()
        self.assertEqual(self.store.list_notices(),before)
        self.assertEqual([s.id for s in self.store.subscriptions()],['cse'])
        self.assertIn(board['id'],dialog.failures)
        self.assertIn('저장하지 않았습니다',dialog.message.get())
        self.assertFalse(dialog.busy)

    def test_removal_confirmation_no_preserves_then_yes_deletes_only_selected(self):
        self.store.subscribe('cse')
        self.store.subscribe('swedu')
        for source in ('cse','swedu'):
            self.store.save_collection(source,[Notice(source,'100','기존',None,'https://www.yu.ac.kr')])
            self.store.set_favorite(source,'100',True)
        self.app._settings_saved(set(),set())
        self.app.source_filter.set(self.app.names['cse'])
        dialog=self.open()
        dialog.toggle('cse')
        with patch('notice_peek.settings_ui.messagebox.askyesno',return_value=False): self.save(dialog)
        self.assertEqual(len(self.store.list_notices()),2)
        self.assertIsNotNone(self.app.settings)
        with patch('notice_peek.settings_ui.messagebox.askyesno',return_value=True) as confirm: self.save(dialog)
        self.assertIn('읽음·즐겨찾기',confirm.call_args.args[1])
        self.assertEqual([s.id for s in self.store.subscriptions()],['swedu'])
        self.assertEqual(self.store.list_notices('cse'),[])
        self.assertTrue(self.store.list_notices('swedu')[0]['favorite'])
        self.assertEqual(self.app.source_filter.get(),'전체 출처')
        self.assertEqual(set(self.app.source_labels),{'swedu'})
        self.assertEqual(len(self.store.list_catalog()),272)

    def test_cancel_during_network_is_responsive_and_does_not_save(self):
        started=Event()
        dialog=self.open()
        dialog.toggle('cse')
        def wait(source,offset):
            started.set()
            dialog.cancel.wait(2)
        self.client.hook=wait
        dialog.save()
        self.pump(started.is_set)
        self.assertTrue(dialog.busy)
        dialog.toggle('swedu')
        self.assertNotIn('swedu',dialog.selected)
        self.app.refresh()
        self.assertFalse(self.app.busy)
        dialog.close()
        self.pump(lambda: self.app.settings is None)
        self.assertEqual(self.store.subscriptions(),())
        self.assertFalse(self.store.selection_complete())
        self.assertEqual(self.store.list_notices(),[])
        self.assertEqual(str(self.app.refresh_button['state']),'normal')

    def test_atomic_selection_rolls_back_additions_deletions_and_setup_on_invalid_records(self):
        expected=self.store.subscription_snapshot()
        good=Notice('cse','100','정상',None,'https://www.yu.ac.kr')
        with self.assertRaises(StorageError):
            self.store.apply_selection({'cse','swedu'},{'cse':[good],'swedu':[good]},expected)
        self.assertEqual(self.store.subscriptions(),())
        self.assertEqual(self.store.list_notices(),[])
        self.assertFalse(self.store.selection_complete())

    def test_external_subscription_change_rejected_without_overwriting(self):
        dialog=self.open()
        self.store.subscribe('cse')
        dialog.save()
        self.assertIn('설정을 다시',dialog.message.get())
        self.assertEqual([s.id for s in self.store.subscriptions()],['cse'])

    def test_many_sources_use_scrollable_status_area_and_bounded_text(self):
        boards=[b for b in self.store.list_catalog() if b['status']=='supported'][:8]
        for board in boards: self.store.subscribe(board['id'])
        self.app._settings_saved(set(),set())
        self.root.deiconify()
        self.root.update()
        self.assertEqual(len(self.app.source_labels),8)
        self.assertEqual(self.app.status_canvas.winfo_height(),120)
        self.assertTrue(self.app.status_scroll.winfo_ismapped())
        self.assertLessEqual(self.app.row_font.measure(self.app._source_text(boards[0]['id'],80)),80)

    def test_application_close_waits_for_registration_worker(self):
        started=Event()
        dialog=self.open()
        dialog.toggle('cse')
        self.client.hook=lambda source,offset: (started.set(),dialog.cancel.wait(2))
        dialog.save()
        self.pump(started.is_set)
        worker=dialog.worker
        self.app.close()
        self.pump(lambda: not worker.is_alive() and self.app.settings is None)
        self.assertFalse(worker.is_alive())
        self.assertEqual(Store(self.store.path).subscriptions(),())

    def test_newly_selected_existing_source_loads_its_logo(self):
        dialog=self.open()
        dialog.toggle('cse')
        self.save(dialog)
        self.assertIn('cse',self.app.source_logos)
        self.root.update()
        self.assertTrue(self.app.source_canvas.find_withtag('logo'))
        self.assertTrue(all(row['is_read'] for row in self.store.list_notices()))

    def test_main_controls_remain_inside_window_after_many_subscriptions(self):
        for board in [b for b in self.store.list_catalog() if b['status']=='supported'][:8]:
            self.store.subscribe(board['id'])
        self.app._settings_saved(set(),set())
        self.root.deiconify()
        self.root.update()
        self.assertLessEqual(self.app.favorite_button.winfo_rooty()+self.app.favorite_button.winfo_height(),
                             self.root.winfo_rooty()+self.root.winfo_height())
