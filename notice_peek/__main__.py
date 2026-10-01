"""Launch the app by default, with optional diagnostic sync/list commands."""

import argparse
from pathlib import Path
import sys
from threading import Event

from .models import SOURCES, StorageError
from .service import SyncService
from .storage import Store


def main():
    # Windows redirected output must also support Korean titles and emoji.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="notice-peek · 공지 모아보기")
    parser.add_argument("command", nargs="?", default="gui", choices=("gui", "sync", "list"))
    parser.add_argument("--database", type=Path, help="검증용 DB 경로. 생략하면 사용자 앱 데이터 경로")
    args = parser.parse_args()
    if args.command == "gui":
        from .ui import launch
        return launch(args.database)
    try:
        store = Store(args.database)
        print(f"저장 위치: {store.path}")
        if args.command == "list":
            for n in store.list_notices():
                print(f"{n['published'] or '날짜 없음'} [{n['source_id']}] "
                      f"{'읽음' if n['is_read'] else '안 읽음'} {n['title']}\n  {n['url']}")
            return 0
        names = {s.id: s.name for s in SOURCES}
        results = SyncService(store).sync(Event(), lambda source, pages: print(f"{names[source]}: {pages}페이지 확인"))
        for result in results:
            if result.status == "success":
                label = "초기 등록" if result.initial else "신규"
                print(f"{names[result.source_id]}: {label} {result.added}건")
            else:
                print(f"{names[result.source_id]}: {result.message}", file=sys.stderr)
        return int(any(r.status != "success" for r in results))
    except StorageError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("중단했습니다. 커밋되지 않은 수집 내용은 저장하지 않습니다.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
