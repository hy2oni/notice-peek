"""Validate additions without changing the database or downloading detail pages."""
from .collector import collect
from .models import Cancelled, FetchError, ParseError, Source


def validate_additions(boards, client, cancel, events):
    collections, failures = {}, {}
    try:
        for index, board in enumerate(boards, 1):
            if cancel.is_set():
                raise Cancelled()
            events.put(('progress', (index, len(boards), board['id'])))
            source = Source(board['id'], board['organization'] + ' · ' + board['name'], board['url'])
            try:
                notices, _ = collect(source, client, set(), True, cancel)
                collections[board['id']] = notices
            except (FetchError, ParseError) as exc:
                failures[board['id']] = str(exc)
        if cancel.is_set():
            raise Cancelled()
        events.put(('done', (collections, failures)))
    except Cancelled:
        events.put(('cancelled', None))
    except Exception as exc:
        events.put(('error', f'게시판 검사를 마치지 못했습니다. ({type(exc).__name__})'))
