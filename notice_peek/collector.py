"""Read public list pages only. No detail, image, or attachment requests."""

from datetime import date
from html.parser import HTMLParser
from http.client import HTTPException
import re
from threading import Event
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen

from .models import Cancelled, FetchError, Notice, Page, ParseError, Source


class _Node:
    def __init__(self, tag="", attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []

    def has_class(self, name):
        return name in (self.attrs.get("class") or "").split()

    def find_all(self, tag=None, cls=None):
        for child in self.children:
            if isinstance(child, _Node):
                if (tag is None or child.tag == tag) and (cls is None or child.has_class(cls)):
                    yield child
                yield from child.find_all(tag, cls)

    def text(self):
        return "".join(c.text() if isinstance(c, _Node) else c for c in self.children)


class _Document(HTMLParser):
    VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = _Node()
        self.stack = [self.root]
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _text(node):
    return " ".join(node.text().split())


def parse_page(html: str, source: Source, offset: int = 0) -> Page:
    root = _Document(html).root
    tables = list(root.find_all("table", "board-table"))
    totals = list(root.find_all(cls="b-total-wrap"))
    match = re.search(r"총\s*([\d,]+)\s*개", _text(totals[0])) if totals else None
    if len(tables) != 1 or not match:
        raise ParseError("공지 목록 구조를 확인할 수 없습니다.")
    total = int(match[1].replace(",", ""))
    if total == 0:
        raise ParseError("비어 있는 목록입니다. 기존 기록을 유지합니다.")
    bodies = list(tables[0].find_all("tbody"))
    if len(bodies) != 1:
        raise ParseError("공지 목록 본문이 없습니다.")
    notices = []
    regular_ids = set()
    for row in bodies[0].find_all("tr"):
        number = next(row.find_all(cls="b-num-box"), None)
        if number is None:
            raise ParseError("공지 행 구조가 변경되었습니다.")
        pinned = row.has_class("b-top-box") or _text(number) == "공지"
        if not pinned and not _text(number).isdigit():
            raise ParseError("공지 번호를 확인할 수 없습니다.")
        title_box = next(row.find_all(cls="b-title-box"), None)
        links = [] if title_box is None else [
            a for a in title_box.find_all("a")
            if parse_qs(urlsplit(a.attrs.get("href") or "").query).get("mode") == ["view"]
        ]
        if len(links) != 1:
            raise ParseError("공지 제목 또는 원문 링크가 없습니다.")
        link = links[0]
        target = urlsplit(urljoin(source.url, link.attrs["href"]))
        base = urlsplit(source.url)
        ids = parse_qs(target.query).get("articleNo", [])
        if (target.scheme, target.netloc, target.path) != (base.scheme, base.netloc, base.path):
            raise ParseError("예상하지 못한 공지 주소입니다.")
        if len(ids) != 1 or not ids[0].isdigit() or not _text(link):
            raise ParseError("공지 ID 또는 제목을 확인할 수 없습니다.")
        date_node = next(row.find_all(cls="b-date"), None)
        if date_node is None:
            raise ParseError("게시일 영역을 확인할 수 없습니다.")
        raw_date = _text(date_node)
        published = None
        if raw_date not in ("", "-"):
            try:
                published = date.fromisoformat(raw_date.replace(".", "-")).isoformat()
            except ValueError as exc:
                raise ParseError("게시일 형식이 변경되었습니다.") from exc
        if not pinned:
            if ids[0] in regular_ids:
                raise ParseError("일반 공지가 한 페이지에 중복되어 있습니다.")
            regular_ids.add(ids[0])
        notices.append(Notice(source.id, ids[0], _text(link), published,
                              source.url + "?" + urlencode({"mode": "view", "articleNo": ids[0]}), pinned))
    # A partial or challenge page must not mark a source as successfully checked.
    if len(regular_ids) != min(10, max(0, total - offset)):
        raise ParseError("예상한 공지 수와 응답이 다릅니다.")
    next_offset = offset + 10 if offset + 10 < total else None
    if next_offset is not None:
        paging = next(root.find_all(cls="b-paging-wrap"), None)
        offsets = set()
        if paging:
            for a in paging.find_all("a"):
                query = parse_qs(urlsplit(a.attrs.get("href") or "").query)
                offsets.update(int(v) for v in query.get("article.offset", []) if v.isdigit())
        if next_offset not in offsets:
            raise ParseError("다음 목록 페이지를 확인할 수 없습니다.")
    return Page(tuple(notices), total, next_offset)


class HttpClient:
    """One request at a time, with bounded retry and a cancellable delay."""

    def __init__(self, timeout=10, retries=1, interval=0.5):
        self.timeout = timeout
        self.retries = retries
        self.interval = interval
        self._last_finished = 0.0

    def get(self, url: str, cancel: Event) -> str:
        for attempt in range(self.retries + 1):
            delay = max(0, self.interval - (time.monotonic() - self._last_finished))
            if cancel.wait(delay):
                raise Cancelled()
            try:
                request = Request(url, headers={"User-Agent": "notice-peek/0.1 (personal notice reader)",
                                                "Accept": "text/html"})
                with urlopen(request, timeout=self.timeout) as response:
                    if response.headers.get_content_type() != "text/html":
                        raise ParseError("HTML 공지 목록이 아닌 응답입니다.")
                    if urlsplit(response.url).path != urlsplit(url).path:
                        raise ParseError("공지 목록 이외의 페이지로 이동했습니다.")
                    payload = response.read(2_000_001)
                    if len(payload) > 2_000_000:
                        raise ParseError("공지 목록 응답이 예상 크기를 초과했습니다.")
                    result = payload.decode(response.headers.get_content_charset() or "utf-8")
                if cancel.is_set():
                    raise Cancelled()
                return result
            except HTTPError as exc:
                code = exc.code
                exc.close()
                # Do not retry access restrictions or rate limits.
                if code not in (500, 502, 503, 504) or attempt == self.retries:
                    raise FetchError(f"서버 응답 오류(HTTP {code})") from exc
            except (URLError, TimeoutError, OSError, HTTPException) as exc:
                if attempt == self.retries:
                    raise FetchError("연결에 실패했습니다. 인터넷 연결을 확인해 주세요.") from exc
            except (UnicodeError, LookupError) as exc:
                raise ParseError("공지 목록의 문자 인코딩을 읽을 수 없습니다.") from exc
            finally:
                self._last_finished = time.monotonic()
            if cancel.wait(1.0):
                raise Cancelled()
        raise FetchError("공지 목록을 가져오지 못했습니다.")


def collect(source, client, known_regular, initial, cancel, progress=None):
    notices = {}
    regular_seen = set()
    offset = 0
    pages = 0
    while True:
        if cancel.is_set():
            raise Cancelled()
        url = source.url + "?" + urlencode({"mode": "list", "articleLimit": 10, "article.offset": offset})
        page = parse_page(client.get(url, cancel), source, offset)
        pages += 1
        regular = [n for n in page.notices if not n.pinned]
        if regular and all(n.article_id in regular_seen for n in regular):
            raise ParseError("페이지를 이동해도 같은 공지 목록이 반복됩니다.")
        # Finish this page before stopping: unseen rows can follow a known row.
        for notice in page.notices:
            if not notice.pinned:
                regular_seen.add(notice.article_id)
            notices.setdefault(notice.article_id, notice)
        if progress:
            progress(source.id, pages)
        reached_known = any(n.article_id in known_regular for n in regular)
        if (initial and len(regular_seen) >= 30) or (not initial and reached_known) or page.next_offset is None:
            break
        offset = page.next_offset
        if pages >= 1000:
            raise ParseError("예상 범위를 초과한 페이지 이동입니다. 저장하지 않았습니다.")
    return tuple(notices.values()), pages
