from threading import Event, Lock

from .collector import HttpClient, collect
from .models import Cancelled, FetchError, ParseError, SOURCES, StorageError, SyncResult


class SyncService:
    """Call from one background worker; progress/results can be sent to a UI queue."""

    def __init__(self, store, client=None, sources=SOURCES):
        self.store = store
        self.client = client if client is not None else HttpClient()
        self.sources = sources
        self._lock = Lock()

    def sync(self, cancel=None, progress=None):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("이미 공지를 확인하고 있습니다.")
        cancel = cancel if cancel is not None else Event()
        results = []
        try:
            for source in self.sources:
                if cancel.is_set():
                    results.append(SyncResult(source.id, "cancelled", message="수집을 취소했습니다."))
                    continue
                try:
                    initial = not self.store.state(source.id)["initialized"]
                    known = self.store.known_regular(source.id)
                    notices, pages = collect(source, self.client, known, initial, cancel, progress)
                    if cancel.is_set():
                        raise Cancelled()
                    initial, added = self.store.save_collection(source.id, notices)
                    results.append(SyncResult(source.id, "success", len(added), initial, pages,
                                              new_ids=() if initial else added))
                except Cancelled:
                    results.append(SyncResult(source.id, "cancelled", message="수집을 취소했습니다."))
                except (FetchError, ParseError, StorageError) as exc:
                    status = ("network_error" if isinstance(exc, FetchError) else
                              "parse_error" if isinstance(exc, ParseError) else "storage_error")
                    message = str(exc)
                    try:
                        self.store.record_failure(source.id, status, message)
                    except StorageError:
                        status = "storage_error"
                        message += " 오류 상태도 저장하지 못했습니다."
                    results.append(SyncResult(source.id, status, message=message))
            return tuple(results)
        finally:
            self._lock.release()
