"""Light-mode Tk interface. Only the main thread touches Tk widgets."""

from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread
import time
import tkinter as tk
from tkinter import font, messagebox, ttk
from urllib.parse import parse_qs, urlsplit
import webbrowser

from .models import StorageError
from .settings_ui import SourceSettings
from .service import SyncService
from .storage import Store


BG = "#F5F7FA"
TEXT = "#202B3C"
MUTED = "#526176"
BLUE = "#245DB5"
ERROR = "#A33126"


def _last_checked(value):
    if not value:
        return "아직 확인한 기록이 없습니다"
    try:
        return "마지막 확인 " + datetime.fromisoformat(value).astimezone().strftime("%m.%d %H:%M")
    except ValueError:
        return "마지막 확인 시각을 읽을 수 없습니다"


class NoticeApp:
    def __init__(self, root, store, service=None, *, auto_sync=True, opener=None, registration_client=None):
        self.sources = store.subscriptions()
        self.names = {s.id: s.name for s in self.sources}
        self.settings = None
        self.registration_client = registration_client
        self._close_after_settings = False
        self.root = root
        self.store = store
        self.service = service if service is not None else SyncService(store)
        self.opener = opener if opener is not None else webbrowser.open
        self.events = Queue()
        self.cancel = Event()
        self.worker = None
        self.busy = False
        self.closing = False
        self._poll_id = None
        self._startup_id = None
        self._logo_draw_id = None
        self._outcome = None
        self._last_open = (None, 0.0)
        self._skip_title_release = False
        self.records = {}
        self.new_keys = set()
        self.source_filter = tk.StringVar(root, value="전체 출처")
        self.unread_filter = tk.BooleanVar(root, value=False)
        self.favorite_filter = tk.BooleanVar(root, value=False)
        self.summary = tk.StringVar(root, value="저장된 공지를 표시합니다.")
        self.counts = tk.StringVar(root)
        self.detail_title = tk.StringVar(root, value="공지를 선택해 주세요.")
        self.detail_meta = tk.StringVar(root, value="제목을 더블클릭하면 원문을 열고 읽음으로 표시합니다.")
        self.source_status = {s.id: tk.StringVar(root) for s in self.sources}
        self._configure_style()
        self.source_logos = {}
        self._load_source_logos()
        self._build()
        self.reload()
        self._restore_status()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<F5>", lambda event: self.refresh())
        if auto_sync:
            # Give the cached list a chance to paint before starting network work.
            self._startup_id = self.root.after(150, self._startup)

    def _configure_style(self):
        self.root.title("notice-peek · 공지 모아보기")
        self.root.iconbitmap(str(Path(__file__).parent / "assets" / "notice-peek.ico"))
        width = max(920, min(1120, self.root.winfo_screenwidth() - 60))
        height = max(640, min(760, self.root.winfo_screenheight() - 100))
        self.root.geometry(f"{width}x{height}+20+20")
        self.root.minsize(920, 640)
        self.root.configure(background=BG)
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            font.nametofont(name).configure(family="맑은 고딕", size=10)
        self.row_font = font.Font(family="맑은 고딕", size=10)
        self.unread_font = font.Font(family="맑은 고딕", size=10, weight="bold")
        style = ttk.Style(self.root)
        # A fixed theme keeps the content light even if Windows uses dark mode.
        style.theme_use("clam")
        style.configure(".", font=("맑은 고딕", 10), background=BG, foreground=TEXT)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Title.TLabel", font=("Segoe UI", 24, "bold"))
        style.configure("TButton", padding=(14, 8), background="#FFFFFF", bordercolor="#CCD5E0",
                        lightcolor="#FFFFFF", darkcolor="#CCD5E0", focuscolor=BLUE)
        style.map("TButton", background=[("active", "#EAF1FA"), ("disabled", "#EDF0F4")],
                  foreground=[("disabled", "#707C8D")])
        style.configure("Primary.TButton", background=BLUE, foreground="#FFFFFF",
                        bordercolor=BLUE, lightcolor=BLUE, darkcolor=BLUE)
        style.map("Primary.TButton", background=[("disabled", "#DFE6F0"), ("active", "#194B95")],
                  foreground=[("disabled", "#627188"), ("!disabled", "#FFFFFF")])
        style.configure("TCheckbutton", padding=(8, 5), background=BG)
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("TCombobox", padding=(8, 6), fieldbackground="#FFFFFF", foreground=TEXT)
        style.map("TCombobox", fieldbackground=[("readonly", "#FFFFFF")],
                  foreground=[("readonly", TEXT)], selectbackground=[("readonly", "#FFFFFF")],
                  selectforeground=[("readonly", TEXT)])
        self.root.option_add("*TCombobox*Listbox.background", "#FFFFFF")
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        style.configure("Treeview", background="#FFFFFF", fieldbackground="#FFFFFF", foreground=TEXT,
                        rowheight=40, borderwidth=0, font=self.row_font)
        style.configure("Treeview.Heading", background="#EAF0F6", foreground=TEXT,
                        padding=(8, 10), font=("맑은 고딕", 10, "bold"), relief="flat")
        style.map("Treeview", background=[("selected", "#DCEBFF")], foreground=[("selected", "#153E77")])
        style.map("Treeview.Heading", background=[("active", "#EAF0F6")])
        style.configure("Horizontal.TProgressbar", background=BLUE, troughcolor="#E5EAF0",
                        borderwidth=0, lightcolor=BLUE, darkcolor=BLUE)

    def _build(self):
        outer = ttk.Frame(self.root, padding=(24, 20, 24, 16))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(4, weight=1)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="notice-peek", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="영남대학교 공지 모아보기", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.refresh_button = ttk.Button(header, text="새로고침  ·  F5", style="Primary.TButton", command=self.refresh)
        self.settings_button = ttk.Button(header, text="출처 설정", command=self.open_settings)
        self.settings_button.grid(row=0, column=1, rowspan=2, sticky="e", padx=(0, 10))
        self.refresh_button.grid(row=0, column=2, rowspan=2, sticky="e")

        status = ttk.Frame(outer)
        status.grid(row=1, column=0, sticky="ew", pady=(20, 12))
        self.status_canvas = tk.Canvas(status, background=BG, highlightthickness=0, height=64)
        self.status_canvas.grid(row=0, column=0, sticky="ew")
        self.status_scroll = ttk.Scrollbar(status, orient="vertical", command=self.status_canvas.yview)
        self.status_scroll.grid(row=0, column=1, sticky="ns")
        self.status_frame = ttk.Frame(self.status_canvas)
        self._status_window = self.status_canvas.create_window(0, 0, window=self.status_frame, anchor="nw")
        self.status_frame.bind("<Configure>", self._update_status_scroll)
        def status_width(event):
            self.status_canvas.itemconfigure(self._status_window, width=event.width)
            for label in self.source_labels.values():
                label.configure(wraplength=max(200,event.width//2-20))
        self.status_canvas.bind("<Configure>",status_width)
        self.status_canvas.bind("<MouseWheel>",lambda event: self.status_canvas.yview_scroll(-int(event.delta/120),"units"))
        status.columnconfigure(0,weight=1)
        status.columnconfigure(1,weight=0)
        self._build_source_status()
        self.summary_label = ttk.Label(status, textvariable=self.summary, foreground=BLUE)
        self.summary_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(12, 6))
        def resize_status(event):
            for label in self.source_labels.values():
                label.configure(wraplength=max(200, event.width // 2 - 32))
            self.summary_label.configure(wraplength=max(200, event.width - 8))
        status.bind("<Configure>", resize_status)
        self.progress = ttk.Progressbar(status, mode="determinate", length=200)
        self.progress.grid(row=2, column=0, columnspan=2, sticky="ew")

        filters = ttk.Frame(outer)
        filters.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(filters, text="출처").pack(side="left", padx=(0, 8))
        self.source_combo = ttk.Combobox(filters, textvariable=self.source_filter, state="readonly", width=20,
                                        values=("전체 출처", *self.names.values()))
        self.source_combo.pack(side="left", padx=(0, 14))
        self.source_combo.bind("<<ComboboxSelected>>", lambda event: self.render())
        self.unread_check = ttk.Checkbutton(filters, text="안 읽음만", variable=self.unread_filter, command=self.render)
        self.unread_check.pack(side="left")
        self.favorite_check = ttk.Checkbutton(filters, text="즐겨찾기만", variable=self.favorite_filter, command=self.render)
        self.favorite_check.pack(side="left", padx=(8, 0))
        ttk.Label(outer, textvariable=self.counts, style="Muted.TLabel").grid(row=3, column=0, sticky="w", pady=(0, 8))

        table = ttk.Frame(outer)
        table.grid(row=4, column=0, sticky="nsew")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        columns = ("favorite", "read", "title", "source", "date")
        self.tree = ttk.Treeview(table, columns=columns, show="headings", selectmode="browse")
        for name, text, width, anchor, stretch in (
            ("favorite", "별", 44, "center", False), ("read", "읽음 상태", 80, "center", False),
            ("title", "제목", 558, "w", True),
            ("source", "출처", 250, "center", False), ("date", "게시일", 105, "center", False),
        ):
            self.tree.heading(name, text=text, anchor=anchor)
            self.tree.column(name, width=width, minwidth=260 if name == "title" else width, anchor=anchor, stretch=stretch)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        def scrolled(first, last):
            scroll.set(first, last)
            self._schedule_source_logos()
        self.tree.configure(yscrollcommand=scrolled, xscrollcommand=lambda *args: self._schedule_source_logos())
        self.tree.tag_configure("even", background="#FFFFFF")
        self.tree.tag_configure("odd", background="#F6F8FB")
        self.tree.tag_configure("unread", font=self.unread_font)
        self.tree.bind("<<TreeviewSelect>>", lambda event: self._selection_changed())
        self.tree.bind("<ButtonRelease-1>", self._table_click)
        self.tree.bind("<Double-Button-1>", self._title_double_click)
        self.tree.bind("<Return>", lambda event: self.open_selected())
        self.tree.bind("<space>", self._keyboard_read)
        self.tree.bind("<Motion>", self._table_cursor)
        # Treeview only supports native images in its first (tree) column.
        # A clipped canvas paints the source cells at their real bbox, without
        # changing column order or adding one widget per stored notice.
        self.source_canvas = tk.Canvas(self.tree, highlightthickness=0, borderwidth=0,
                                       background="#FFFFFF", takefocus=False)
        self.source_canvas.bind("<Button-1>", self._source_click)
        self.source_canvas.bind("<B1-Motion>", self._source_click)
        self.source_canvas.bind("<MouseWheel>", self._source_wheel)
        for sequence in ("<Configure>", "<<TreeviewSelect>>", "<B1-Motion>", "<ButtonRelease-1>"):
            self.tree.bind(sequence, lambda event: self._schedule_source_logos(), add="+")
        self.empty_label = ttk.Label(table, text="", foreground=MUTED, background="#FFFFFF", justify="center")

        detail = ttk.Frame(outer)
        detail.grid(row=5, column=0, sticky="ew", pady=(14, 0))
        detail.columnconfigure(0, weight=1)
        self.title_label = ttk.Label(detail, textvariable=self.detail_title, font=("맑은 고딕", 11, "bold"),
                                     wraplength=1000, justify="left")
        self.title_label.grid(row=0, column=0, sticky="ew")
        detail.bind("<Configure>", lambda event: self.title_label.configure(wraplength=max(200, event.width - 8)))
        ttk.Label(detail, textvariable=self.detail_meta, style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(5, 10))
        actions = ttk.Frame(detail)
        actions.grid(row=2, column=0, sticky="ew")
        self.open_button = ttk.Button(actions, text="원문 열기", command=self.open_selected)
        self.open_button.pack(side="left")
        self.read_button = ttk.Button(actions, text="읽음으로 표시", command=self.toggle_read)
        self.read_button.pack(side="left", padx=8)
        self.favorite_button = ttk.Button(actions, text="즐겨찾기 추가", command=self.toggle_favorite)
        self.favorite_button.pack(side="left")
        ttk.Label(actions, text="원문을 열면 읽음으로 표시합니다.", style="Muted.TLabel").pack(side="right")

    @staticmethod
    def _key(row):
        return f"{row['source_id']}:{row['article_id']}"

    def _load_source_logos(self):
        for source in self.sources:
            if source.id in self.source_logos:
                continue
            try:
                self.source_logos[source.id] = tk.PhotoImage(master=self.root, file=str(
                    Path(__file__).parent / 'assets' / 'logos' / f'{source.id}-row.png'))
            except tk.TclError:
                pass

    def _update_status_scroll(self, event=None):
        self.status_canvas.configure(scrollregion=self.status_canvas.bbox('all'))
        requested = self.status_frame.winfo_reqheight()
        self.status_canvas.configure(height=min(120,max(42,requested)))
        if requested > 120:
            self.status_scroll.grid()
        else:
            self.status_scroll.grid_remove()

    def _source_text(self, source_id, width):
        text = self.names.get(source_id, source_id)
        if self.row_font.measure(text) <= width:
            return text
        while text and self.row_font.measure(text + '…') > width:
            text = text[:-1]
        return text + '…'

    def _build_source_status(self):
        for widget in self.status_frame.winfo_children():
            widget.destroy()
        self.source_labels = {}
        self.status_frame.columnconfigure((0, 1), weight=1, uniform='source')
        self.status_canvas.yview_moveto(0)
        self.status_canvas.configure(height=64 if len(self.sources) <= 2 else 120)
        if len(self.sources) > 4:
            self.status_scroll.grid()
        else:
            self.status_scroll.grid_remove()
        if not self.sources:
            ttk.Label(self.status_frame, text='선택한 출처가 없습니다. 출처 설정에서 추가할 수 있습니다.',
                      style='Muted.TLabel').grid(row=0,column=0,columnspan=2,sticky='w',pady=10)
        for index,source in enumerate(self.sources):
            label = ttk.Label(self.status_frame, textvariable=self.source_status[source.id],
                              justify='left',style='Muted.TLabel',wraplength=450)
            label.grid(row=index//2,column=index%2,sticky='nw',padx=(0,16),pady=(0,8))
            label.bind('<MouseWheel>',lambda event: self.status_canvas.yview_scroll(-int(event.delta/120),'units'))
            self.source_labels[source.id] = label

    def _startup(self):
        self._startup_id = None
        try:
            if not self.store.selection_complete():
                self.open_settings(initial=True)
            else:
                self.refresh()
        except StorageError as exc:
            self._error(str(exc))

    def open_settings(self, initial=False):
        if self.busy or self.closing:
            return
        if self.settings is not None:
            self.settings.window.lift()
            return
        if self._startup_id is not None:
            self.root.after_cancel(self._startup_id)
            self._startup_id = None
        try:
            self.settings = SourceSettings(self.root,self.store,self._settings_saved,self._settings_closed,
                                           initial=initial,client=self.registration_client)
        except StorageError as exc:
            self._error(str(exc))
            return
        self.refresh_button.configure(state='disabled')

    def _settings_saved(self, added, removed):
        self.sources = self.store.subscriptions()
        self.names = {s.id:s.name for s in self.sources}
        self._load_source_logos()
        self.source_status = {s.id:tk.StringVar(self.root) for s in self.sources}
        self._build_source_status()
        self.source_combo.configure(values=('전체 출처',*self.names.values()))
        if self.source_filter.get() not in ('전체 출처',*self.names.values()):
            self.source_filter.set('전체 출처')
        self.new_keys = {key for key in self.new_keys if key.split(':',1)[0] in self.names}
        self._restore_status()
        self.reload()
        text = f'출처 {len(self.sources)}개를 구독합니다.' if self.sources else '출처를 선택하지 않았습니다. 출처 설정에서 언제든 추가할 수 있습니다.'
        if added: text += f' 추가한 {len(added)}개 출처의 첫 공지는 모두 읽음으로 등록했습니다.'
        if removed: text += f' 제거한 {len(removed)}개 출처의 기록을 삭제했습니다.'
        self.summary.set(text)
        self.summary_label.configure(foreground=BLUE)

    def _settings_closed(self):
        self.settings = None
        self.refresh_button.configure(state='normal')
        if self._close_after_settings:
            self.close()

    def _schedule_source_logos(self):
        if not self.closing and self._logo_draw_id is None:
            self._logo_draw_id = self.root.after_idle(self._draw_source_logos)

    def _draw_source_logos(self):
        self._logo_draw_id = None
        canvas = self.source_canvas
        canvas.delete("all")
        if self.closing or not self.tree.winfo_ismapped():
            canvas.place_forget()
            return
        # Locate the heading's lower edge rather than assume a font/DPI height.
        height = self.tree.winfo_height()
        body_top = None
        seen_heading = False
        for y in range(height):
            region = self.tree.identify_region(2, y)
            if region == "heading":
                seen_heading = True
            elif seen_heading:
                body_top = y
                break
        if body_top is None:
            canvas.place_forget()
            return
        rowheight = int(ttk.Style(self.root).lookup("Treeview", "rowheight"))
        # Only paint visible rows. The extra probe includes a partly visible row.
        keys = dict.fromkeys(self.tree.identify_row(y) for y in
                             range(body_top, height + rowheight, rowheight))
        boxes = [(key, self.tree.bbox(key, "source")) for key in keys if key]
        # Tk paints complete rows only; do not show a stray logo in the blank
        # remainder below the last row at the bottom of a small window.
        boxes = [(key, box) for key, box in boxes if box and box[1] + box[3] <= height - 2]
        if not boxes:
            canvas.place_forget()
            return
        x, _, width, _ = boxes[0][1]
        canvas.place(x=x, y=body_top, width=width, height=max(0, height - body_top - 2))
        selected = self.tree.selection()
        for key, (_, y, _, row_height) in boxes:
            row = self.records[key]
            tags = self.tree.item(key, "tags")
            bg = "#DCEBFF" if key in selected else "#F6F8FB" if "odd" in tags else "#FFFFFF"
            top = y - body_top
            canvas.create_rectangle(0, top, width, top + row_height, fill=bg, outline="")
            logo = self.source_logos.get(row["source_id"])
            if logo:
                canvas.create_image(width // 2, top + row_height // 2, image=logo, tags=(key, "logo"))
            else:
                canvas.create_text(width // 2, top + row_height // 2, text=self._source_text(row["source_id"], width - 20),
                                   fill="#153E77" if key in selected else TEXT,
                                   font=self.unread_font if not row["is_read"] else self.row_font,
                                   tags=(key, "source-text"))

    def _source_click(self, event):
        key = self.tree.identify_row(event.y + self.source_canvas.winfo_y())
        if key and not self.closing:
            self.tree.selection_set(key)
            self.tree.focus(key)
            self.tree.focus_set()
        return "break"

    def _source_wheel(self, event):
        self.tree.event_generate("<MouseWheel>", delta=event.delta,
                                 x=event.x + self.source_canvas.winfo_x(),
                                 y=event.y + self.source_canvas.winfo_y())
        return "break"

    def reload(self):
        try:
            rows = self.store.list_notices()
        except StorageError as exc:
            self._error(str(exc))
            return
        self.records = {self._key(row): row for row in rows}
        self.render()

    def render(self):
        selection = self.tree.selection()
        old_key = selection[0] if selection else None
        scroll = self.tree.yview()[0]
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        visible = []
        for key, row in self.records.items():
            if self.source_filter.get() != "전체 출처" and self.names[row["source_id"]] != self.source_filter.get():
                continue
            if self.unread_filter.get() and row["is_read"]:
                continue
            if self.favorite_filter.get() and not row["favorite"]:
                continue
            visible.append((key, row))
        for index, (key, row) in enumerate(visible):
            tags = ["odd" if index % 2 else "even"]
            if not row["is_read"]:
                tags.append("unread")
            self.tree.insert("", "end", iid=key, values=(
                "★" if row["favorite"] else "☆", "읽음" if row["is_read"] else "안 읽음",
                ("NEW  " if key in self.new_keys else "") + row["title"], self.names[row["source_id"]],
                row["published"] or "날짜 없음"), tags=tags)
        if old_key and self.tree.exists(old_key):
            self.tree.selection_set(old_key)
            self.tree.focus(old_key)
        self.tree.yview_moveto(scroll)
        unread = sum(not row["is_read"] for row in self.records.values())
        self.counts.set(f"전체 {len(self.records):,}건   ·   안 읽음 {unread:,}건   ·   현재 표시 {len(visible):,}건")
        if visible:
            self.empty_label.place_forget()
        else:
            text = ("조건에 맞는 공지가 없습니다.\n출처 또는 필터를 변경해 보세요." if self.records else
                    "아직 저장된 공지가 없습니다.\n새로고침으로 공지를 가져올 수 있습니다." if self.sources else
                    "구독 중인 출처가 없습니다.\n출처 설정에서 받을 공지를 선택해 주세요.")
            self.empty_label.configure(text=text)
            self.empty_label.place(relx=0.5, rely=0.5, anchor="center")
        self._selection_changed()
        self._schedule_source_logos()

    def _selected(self):
        selected = self.tree.selection()
        return self.records.get(selected[0]) if selected else None

    def _selection_changed(self):
        row = self._selected()
        for button in (self.open_button, self.read_button, self.favorite_button):
            button.configure(state="normal" if row and not self.closing else "disabled")
        if row is None:
            self.detail_title.set("공지를 선택해 주세요.")
            self.detail_meta.set("제목을 더블클릭하면 원문을 열고 읽음으로 표시합니다.")
            return
        self.detail_title.set(row["title"])
        self.detail_meta.set(f"{self.names[row['source_id']]}  ·  {row['published'] or '날짜 없음'}" +
                             ("  ·  이번 실행에서 발견한 신규 공지" if self._key(row) in self.new_keys else ""))
        self.read_button.configure(text="안 읽음으로 표시" if row["is_read"] else "읽음으로 표시")
        self.favorite_button.configure(text="즐겨찾기 해제" if row["favorite"] else "즐겨찾기 추가")

    def _table_cursor(self, event):
        clickable = self.tree.identify_region(event.x, event.y) == "cell" and self.tree.identify_column(event.x) in ("#1", "#2", "#3")
        self.tree.configure(cursor="hand2" if clickable else "")

    def _table_click(self, event):
        if self._skip_title_release:
            self._skip_title_release = False
            return "break"
        if self.closing or self.tree.identify_region(event.x, event.y) != "cell":
            return
        key = self.tree.identify_row(event.y)
        if not key:
            return
        self.tree.selection_set(key)
        self.tree.focus(key)
        column = self.tree.identify_column(event.x)
        if column == "#1":
            self.toggle_favorite()
        elif column == "#2":
            self.toggle_read()

    def _title_double_click(self, event):
        if (self.closing or self.tree.identify_region(event.x, event.y) != "cell"
                or self.tree.identify_column(event.x) != "#3"):
            return
        key = self.tree.identify_row(event.y)
        if not key:
            return
        self.tree.selection_set(key)
        self.tree.focus(key)
        # Auto-read can remove this row under the unread filter. Do not let
        # the remaining release/class binding act on the row now underneath.
        self._skip_title_release = True
        self.open_selected()
        return "break"

    def _keyboard_read(self, event):
        self.toggle_read()
        return "break"

    def open_selected(self):
        row = self._selected()
        if row is None or self.closing:
            return
        target = urlsplit(row["url"])
        source = next((s for s in self.sources if s.id == row["source_id"]), None)
        if source is None:
            self._error("제거된 출처의 공지입니다. 목록을 다시 불러와 주세요.")
            return
        base = urlsplit(source.url)
        query = parse_qs(target.query)
        if ((target.scheme, target.netloc, target.path) != (base.scheme, base.netloc, base.path)
                or query.get("mode") != ["view"] or query.get("articleNo") != [row["article_id"]]):
            self._error("원문 주소를 확인할 수 없어 열지 않았습니다.")
            return
        key, now = self._key(row), time.monotonic()
        if self._last_open[0] == key and now - self._last_open[1] < 0.8:
            return
        try:
            if not self.opener(row["url"]):
                self._error("기본 브라우저를 열지 못했습니다. Windows 기본 앱 설정을 확인해 주세요.")
                return
        except (OSError, webbrowser.Error):
            self._error("기본 브라우저를 열지 못했습니다.")
            return
        self._last_open = (key, now)
        if not row["is_read"]:
            try:
                self.store.set_read(row["source_id"], row["article_id"], True)
            except StorageError as exc:
                self._error(f"원문은 열었지만 읽음 상태를 저장하지 못했습니다. {exc}")
                return
            row["is_read"] = 1
            self.render()

    def toggle_read(self):
        self._toggle("is_read", self.store.set_read)

    def toggle_favorite(self):
        self._toggle("favorite", self.store.set_favorite)

    def _toggle(self, field, save):
        row = self._selected()
        if row is None or self.closing:
            return
        value = not row[field]
        try:
            save(row["source_id"], row["article_id"], value)
        except StorageError as exc:
            self._error(str(exc))
            return
        row[field] = int(value)
        self.render()

    def _restore_status(self):
        for source in self.sources:
            try:
                state = self.store.state(source.id)
                label = "이전 확인 실패" if state.get("status", "success") != "success" else "저장된 기록"
                if not state.get("initialized"):
                    label = "첫 확인 대기"
                self.source_status[source.id].set(f"{source.name} · {label}\n{_last_checked(state.get('last_success'))}")
            except StorageError as exc:
                self.source_status[source.id].set(f"{source.name} · 기록을 읽지 못했습니다")
                self._error(str(exc))

    def _auto_refresh(self):
        self._startup_id = None
        self.refresh()

    def refresh(self):
        if self.busy or self.closing or self.settings is not None:
            return
        if not self.sources:
            self.summary.set("구독 중인 출처가 없습니다. 출처 설정에서 선택해 주세요.")
            return
        if self._startup_id is not None:
            self.root.after_cancel(self._startup_id)
            self._startup_id = None
        self.busy = True
        self.settings_button.configure(state="disabled")
        self.cancel.clear()
        self._outcome = None
        self.refresh_button.configure(state="disabled", text="확인 중…")
        self.summary.set("새 공지를 확인하고 있습니다. 목록과 읽음·즐겨찾기는 계속 사용할 수 있습니다.")
        self.summary_label.configure(foreground=BLUE)
        self.progress.configure(mode="indeterminate")
        self.progress.start(20)
        self.worker = Thread(target=self._collect, name="notice-peek-collector", daemon=False)
        self.worker.start()
        self._poll_id = self.root.after(60, self._poll)

    def _collect(self):
        try:
            results = self.service.sync(self.cancel, lambda source, page: self.events.put(("progress", (source, page))))
            self.events.put(("done", results))
        except Exception as exc:
            # Always return control to the UI, including unexpected worker errors.
            self.events.put(("error", f"공지 확인을 마치지 못했습니다. 기존 기록을 유지합니다. ({type(exc).__name__})"))

    def _poll(self):
        self._poll_id = None
        while True:
            try:
                kind, value = self.events.get_nowait()
            except Empty:
                break
            if kind == "progress" and not self.closing:
                source, page = value
                self.summary.set(f"{self.names[source]} · {page}페이지 확인 중")
            elif kind in ("done", "error"):
                self._outcome = (kind, value)
        if self.worker is not None and self.worker.is_alive():
            self._poll_id = self.root.after(60, self._poll)
            return
        self.busy = False
        self.worker = None
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        if self.closing:
            self.root.destroy()
            return
        self.refresh_button.configure(state="normal", text="새로고침  ·  F5")
        self.settings_button.configure(state="normal")
        if self._outcome is not None:
            kind, value = self._outcome
            if kind == "done":
                self._apply_results(value)
            else:
                self.reload()
                self._restore_status()
                self._error(value)

    def _apply_results(self, results):
        new_count, initial_count, failed = 0, 0, 0
        for result in results:
            self.source_labels[result.source_id].configure(foreground=MUTED)
            try:
                state = self.store.state(result.source_id)
            except StorageError:
                state = {}
            if result.status == "success":
                label = f"초기 등록 {result.added}건" if result.initial else f"신규 {result.added}건"
                initial_count += result.added if result.initial else 0
                new_count += 0 if result.initial else result.added
                self.new_keys.update(f"{result.source_id}:{key}" for key in result.new_ids)
            else:
                failed += 1
                category = {"network_error": "연결 실패", "parse_error": "목록 확인 실패",
                            "storage_error": "저장 실패", "cancelled": "취소됨"}.get(result.status, "확인 실패")
                label = f"{category} · {result.message}"
                self.source_labels[result.source_id].configure(foreground=ERROR)
            self.source_status[result.source_id].set(f"{self.names[result.source_id]} · {label}\n{_last_checked(state.get('last_success'))}")
        parts = []
        if initial_count:
            parts.append(f"초기 공지 {initial_count}건을 읽음으로 등록했습니다")
        parts.append(f"이번 확인 신규 {new_count}건")
        if failed:
            parts.append(f"{failed}개 출처 확인 실패 · 저장된 공지는 유지됩니다")
        self.summary.set("  ·  ".join(parts))
        self.summary_label.configure(foreground=ERROR if failed else BLUE)
        self.reload()

    def _error(self, message):
        self.summary.set(message)
        self.summary_label.configure(foreground=ERROR)

    def close(self):
        if self.closing:
            return
        if self.settings is not None:
            self._close_after_settings = True
            self.settings.close()
            return
        self.closing = True
        self.settings_button.configure(state="disabled")
        if self._logo_draw_id is not None:
            self.root.after_cancel(self._logo_draw_id)
            self._logo_draw_id = None
        if self._startup_id is not None:
            self.root.after_cancel(self._startup_id)
            self._startup_id = None
        self.cancel.set()
        self.refresh_button.configure(state="disabled")
        self._selection_changed()
        if self.worker is not None and self.worker.is_alive():
            self.summary.set("진행 중인 요청을 마무리하고 종료합니다. 잠시만 기다려 주세요.")
            if self._poll_id is None:
                self._poll_id = self.root.after(60, self._poll)
        else:
            if self._poll_id is not None:
                self.root.after_cancel(self._poll_id)
                self._poll_id = None
            self.progress.stop()
            self.worker = None
            self.busy = False
            self.root.destroy()


def launch(database=None):
    root = tk.Tk()
    root.withdraw()
    try:
        store = Store(database)
        NoticeApp(root, store)
    except StorageError as exc:
        messagebox.showerror("notice-peek · 저장 오류", str(exc), parent=root)
        root.destroy()
        return 1
    root.deiconify()
    root.mainloop()
    return 0
