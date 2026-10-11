"""Bundled board catalogue; independent of the user's subscriptions."""
import json
from pathlib import Path
from urllib.parse import urlsplit

from .models import StorageError


def load_catalog():
    try:
        data = json.loads((Path(__file__).parent / 'assets' / 'board_catalog.json').read_text(encoding='utf-8'))
        if data['schema_version'] != 1:
            raise ValueError('schema')
        boards = data['boards']
        ids, urls = set(), set()
        for board in boards:
            p = urlsplit(board['url'])
            if (not board['id'] or board['id'] in ids or board['url'] in urls
                    or p.scheme not in ('http', 'https') or p.username or p.password
                    or p.port not in (None, 80, 443)
                    or not (p.hostname == 'yu.ac.kr' or (p.hostname or '').endswith('.yu.ac.kr'))
                    or board['status'] not in ('supported', 'unsupported', 'unverified')):
                raise ValueError('board')
            for key in ('organization', 'name', 'category'):
                if not isinstance(board[key], str) or not board[key]:
                    raise ValueError(key)
            ids.add(board['id'])
            urls.add(board['url'])
        return boards
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise StorageError('게시판 카탈로그를 읽을 수 없습니다. 앱 파일을 확인해 주세요.') from exc
