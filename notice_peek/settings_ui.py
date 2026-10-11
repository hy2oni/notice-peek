"""Shared first-run and later subscription selection window."""
from queue import Empty, Queue
from pathlib import Path
from threading import Event, Thread
import tkinter as tk
from tkinter import messagebox, ttk

from .collector import HttpClient
from .models import StorageError
from .subscriptions import validate_additions


STATUS = {'supported': '사용 가능', 'unsupported': '미지원', 'unverified': '확인 필요'}
HELP = {'supported': '추가할 때 실제 게시판을 다시 검사합니다.',
        'unsupported': '현재 버전에서는 수집할 수 없는 게시판 형식입니다.',
        'unverified': '접속 실패 또는 빈 게시판으로 수집 여부를 확인하지 못했습니다.'}


class SourceSettings:
    def __init__(self, parent, store, on_saved, on_closed, *, initial=False, client=None):
        self.store, self.on_saved, self.on_closed = store, on_saved, on_closed
        self.catalog = {b['id']: b for b in store.list_catalog()}
        self.snapshot = store.subscription_snapshot()
        self.selected = set(self.snapshot)
        self.client = client if client is not None else HttpClient(timeout=8, retries=0)
        self.events, self.cancel = Queue(), Event()
        self.worker = None
        self.poll_id = None
        self.busy = self.closing = self.closed = False
        self.failures = {}
        self.outcome = None
        self.window = tk.Toplevel(parent)
        self.window.title('notice-peek · 받을 공지 선택' if initial else 'notice-peek · 출처 설정')
        self.window.transient(parent)
        self.window.iconbitmap(str(Path(__file__).parent / 'assets' / 'notice-peek.ico'))
        self.window.configure(background='#F5F7FA')
        self.window.geometry('900x680')
        self.window.minsize(800, 600)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.window.bind('<Escape>', lambda event: self.close())
        self.search = tk.StringVar(self.window)
        self.category = tk.StringVar(self.window, value='전체 분류')
        self.selected_only = tk.BooleanVar(self.window, value=False)
        self.counts = tk.StringVar(self.window)
        self.detail = tk.StringVar(self.window, value='사용 가능한 게시판을 체크해 주세요. 선택 개수에는 제한이 없습니다.')
        self.message = tk.StringVar(self.window, value='아무것도 선택하지 않고 저장해도 됩니다.')
        self._build(initial)
        self.search.trace_add('write', lambda *args: self.render())
        self.render()
        self.window.update_idletasks()
        x = max(0, parent.winfo_rootx() + (parent.winfo_width() - self.window.winfo_width()) // 2)
        y = max(0, min(parent.winfo_rooty() + 30, self.window.winfo_screenheight() - self.window.winfo_height() - 60))
        self.window.geometry(f'+{x}+{y}')
        self.window.grab_set()
        self.search_entry.focus_set()

    def _build(self, initial):
        outer = ttk.Frame(self.window, padding=20)
        outer.pack(fill='both', expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)
        ttk.Label(outer, text='받을 공지를 선택하세요' if initial else '출처 설정',
                  font=('맑은 고딕', 17, 'bold')).grid(row=0, column=0, sticky='w')
        ttk.Label(outer, text='기관명이나 게시판명으로 찾고, 원하는 출처를 체크하세요.',
                  style='Muted.TLabel').grid(row=1, column=0, sticky='w', pady=(6, 16))
        filters = ttk.Frame(outer)
        filters.grid(row=2, column=0, sticky='ew', pady=(0, 12))
        filters.columnconfigure(1, weight=1)
        ttk.Label(filters, text='검색').grid(row=0, column=0, padx=(0, 8))
        self.search_entry = ttk.Entry(filters, textvariable=self.search)
        self.search_entry.grid(row=0, column=1, sticky='ew', padx=(0, 12))
        self.category_combo = ttk.Combobox(filters, state='readonly', textvariable=self.category,
            width=19, values=('전체 분류', *sorted({b['category'] for b in self.catalog.values()})))
        self.category_combo.grid(row=0, column=2)
        self.category_combo.bind('<<ComboboxSelected>>', lambda event: self.render())
        self.selected_check = ttk.Checkbutton(filters, text='선택한 출처만', variable=self.selected_only, command=self.render)
        self.selected_check.grid(row=1, column=0, columnspan=2, sticky='w', pady=(6, 0))
        ttk.Label(filters, textvariable=self.counts, style='Muted.TLabel').grid(row=1, column=2, sticky='e')
        table = ttk.Frame(outer)
        table.grid(row=3, column=0, sticky='nsew')
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=('check','organization','name','status'), show='headings', selectmode='browse')
        for column, label, width, stretch in [('check','선택',52,False), ('organization','기관',240,True),
                ('name','게시판',340,True), ('status','지원 상태',110,False)]:
            self.tree.heading(column, text=label, anchor='center' if column in ('check','status') else 'w')
            self.tree.column(column, width=width, minwidth=width if not stretch else 150,
                             stretch=stretch, anchor='center' if column in ('check','status') else 'w')
        self.tree.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.tag_configure('unavailable', foreground='#707C8D')
        self.tree.tag_configure('checked', background='#EAF2FF')
        self.tree.tag_configure('failed', foreground='#A33126')
        self.tree.bind('<ButtonRelease-1>', self._click)
        self.tree.bind('<space>', self._space)
        self.tree.bind('<<TreeviewSelect>>', lambda event: self._show_detail())
        self.empty = ttk.Label(table, text='검색 결과가 없습니다.', background='#FFFFFF', foreground='#526176')
        detail = ttk.Label(outer, textvariable=self.detail, style='Muted.TLabel', wraplength=840, justify='left')
        detail.grid(row=4, column=0, sticky='ew', pady=(12, 8))
        # Keep the table stable while details wrap at narrower widths.
        outer.bind('<Configure>', lambda event: detail.configure(wraplength=max(300,event.width-40)))
        self.progress = ttk.Progressbar(outer, mode='determinate')
        self.progress.grid(row=5, column=0, sticky='ew')
        self.message_label = ttk.Label(outer, textvariable=self.message, wraplength=840, justify='left')
        self.message_label.grid(row=6, column=0, sticky='ew', pady=(8, 12))
        buttons = ttk.Frame(outer)
        buttons.grid(row=7, column=0, sticky='ew')
        self.save_button = ttk.Button(buttons, text='선택 저장', style='Primary.TButton', command=self.save)
        self.save_button.pack(side='right')
        self.cancel_button = ttk.Button(buttons, text='취소', command=self.close)
        self.cancel_button.pack(side='right', padx=(0, 8))

    def render(self):
        if self.closed:
            return
        previous = self.tree.selection()
        scroll = self.tree.yview()[0]
        self.tree.delete(*self.tree.get_children())
        terms = self.search.get().casefold().split()
        for ident, board in self.catalog.items():
            text = (board['organization'] + ' ' + board['name'] + ' ' + board['category']).casefold()
            if (any(term not in text for term in terms)
                    or (self.category.get() != '전체 분류' and board['category'] != self.category.get())
                    or (self.selected_only.get() and ident not in self.selected)):
                continue
            tags = []
            if board['status'] != 'supported': tags.append('unavailable')
            if ident in self.selected: tags.append('checked')
            if ident in self.failures: tags.append('failed')
            self.tree.insert('', 'end', iid=ident, values=('☑' if ident in self.selected else '☐',
                board['organization'], board['name'], '검사 실패' if ident in self.failures else STATUS[board['status']]), tags=tags)
        if previous and self.tree.exists(previous[0]):
            self.tree.selection_set(previous[0])
            self.tree.focus(previous[0])
        self.tree.yview_moveto(scroll)
        self.counts.set(f'선택 {len(self.selected)}개 · 표시 {len(self.tree.get_children())}개')
        if self.tree.get_children(): self.empty.place_forget()
        else: self.empty.place(relx=.5, rely=.5, anchor='center')

    def _show_detail(self):
        keys = self.tree.selection()
        if not keys:
            self.detail.set('사용 가능한 게시판을 체크해 주세요. 선택 개수에는 제한이 없습니다.')
            return
        board = self.catalog[keys[0]]
        explanation = self.failures.get(board['id'], HELP[board['status']])
        self.detail.set(f"{board['organization']} · {board['name']}\n{board['category']} · {explanation}\n{board['url']}")

    def _click(self, event):
        if self.tree.identify_region(event.x,event.y) != 'cell': return
        ident = self.tree.identify_row(event.y)
        if ident and self.tree.identify_column(event.x) == '#1': self.toggle(ident)

    def _space(self, event):
        keys = self.tree.selection()
        if keys: self.toggle(keys[0])
        return 'break'

    def toggle(self, ident):
        if self.busy or self.closing: return
        board = self.catalog[ident]
        if ident in self.selected:
            self.selected.remove(ident)
        elif board['status'] == 'supported' or ident in self.snapshot:
            self.selected.add(ident)
        else:
            self.message.set(HELP[board['status']])
            return
        self.render()

    def save(self):
        if self.busy or self.closing: return
        additions = [b for ident,b in self.catalog.items() if ident in self.selected and ident not in self.snapshot]
        if not additions:
            self._commit({})
            return
        self.busy = True
        self.failures = {}
        self.cancel.clear()
        self.outcome = None
        self._controls(False)
        self.progress.configure(maximum=len(additions),value=0)
        self.message_label.configure(foreground='#245DB5')
        self.message.set('추가할 게시판을 확인하고 있습니다. 기존 구독과 기록은 아직 변경하지 않습니다.')
        self.worker = Thread(target=validate_additions, args=(additions,self.client,self.cancel,self.events),
                             name='notice-peek-board-check',daemon=False)
        self.worker.start()
        self.poll_id = self.window.after(60,self._poll)

    def _controls(self, enabled):
        self.search_entry.configure(state='normal' if enabled else 'disabled')
        self.category_combo.configure(state='readonly' if enabled else 'disabled')
        self.selected_check.configure(state='normal' if enabled else 'disabled')
        self.save_button.configure(state='normal' if enabled else 'disabled',text='선택 저장' if enabled else '검사 중…')

    def _poll(self):
        self.poll_id = None
        while True:
            try: kind,value = self.events.get_nowait()
            except Empty: break
            if kind == 'progress' and not self.closing:
                index,total,ident = value
                self.progress.configure(value=index-1)
                self.message.set(f"{index}/{total} · {self.catalog[ident]['organization']} 게시판 확인 중")
            elif kind != 'progress': self.outcome = (kind,value)
        if self.worker.is_alive():
            self.poll_id = self.window.after(60,self._poll)
            return
        self.worker = None
        self.busy = False
        if self.closing:
            self._destroy()
            return
        self._controls(True)
        self.progress.configure(value=0)
        kind,value = self.outcome or ('error','게시판 검사를 마치지 못했습니다.')
        if kind == 'done':
            collections,self.failures = value
            if self.failures:
                self.render()
                first = next(iter(self.failures))
                if self.tree.exists(first):
                    self.tree.selection_set(first)
                    self.tree.see(first)
                    self._show_detail()
                self.message_label.configure(foreground='#A33126')
                self.message.set(f'{len(self.failures)}개 출처 검사 실패 · 저장하지 않았습니다. 실패 항목을 해제하거나 다시 시도해 주세요.')
            else: self._commit(collections)
        else:
            self.message_label.configure(foreground='#A33126')
            self.message.set(value or '검사를 취소했습니다. 기존 구독은 유지합니다.')

    def _commit(self, collections):
        removed = set(self.snapshot)-self.selected
        if removed:
            names = [self.catalog[k]['organization']+' · '+self.catalog[k]['name'] for k in sorted(removed)]
            preview = '\n'.join('• '+name for name in names[:6])
            if len(names)>6: preview += f'\n외 {len(names)-6}개 출처'
            if not messagebox.askyesno('출처 제거 확인',
                f'{len(removed)}개 출처를 제거할까요?\n\n{preview}\n\n이 출처의 공지와 읽음·즐겨찾기 기록이 모두 삭제됩니다.',
                parent=self.window,default='no'):
                self.message.set('저장을 취소했습니다. 선택을 수정하거나 창을 닫을 수 있습니다.')
                return
        try:
            added, removed = self.store.apply_selection(self.selected,collections,self.snapshot)
        except StorageError as exc:
            self.message_label.configure(foreground='#A33126')
            self.message.set(str(exc))
            return
        try:
            self.on_saved(added,removed)
        finally:
            self._destroy()

    def close(self):
        if self.closed or self.closing: return
        self.closing = True
        self.cancel.set()
        if self.worker is not None and self.worker.is_alive():
            self._controls(False)
            self.cancel_button.configure(state='disabled')
            self.message.set('진행 중인 검사를 마무리하고 닫습니다. 잠시만 기다려 주세요.')
        else: self._destroy()

    def _destroy(self):
        if self.closed: return
        self.closed = True
        if self.poll_id is not None: self.window.after_cancel(self.poll_id)
        self.window.grab_release()
        self.window.destroy()
        self.on_closed()
