from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    url: str


SOURCES = (
    Source("cse", "컴퓨터공학과", "https://www.yu.ac.kr/cse/community/notice.do"),
    Source("swedu", "SW중심대학사업단", "https://www.yu.ac.kr/swedu/notice/notice.do"),
)


@dataclass(frozen=True)
class Notice:
    source_id: str
    article_id: str
    title: str
    published: str | None
    url: str
    pinned: bool = False


@dataclass(frozen=True)
class Page:
    notices: tuple[Notice, ...]
    total: int
    next_offset: int | None


@dataclass(frozen=True)
class SyncResult:
    source_id: str
    status: str
    added: int = 0
    initial: bool = False
    pages: int = 0
    message: str = ""
    new_ids: tuple[str, ...] = ()


class FetchError(Exception):
    """A network or HTTP failure, not an empty board."""


class ParseError(Exception):
    """The response cannot be trusted as a complete notice list."""


class StorageError(Exception):
    """The database could not be read or saved; never reset it automatically."""


class Cancelled(Exception):
    pass
