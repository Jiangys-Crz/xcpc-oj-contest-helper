"""Verify lingering CF pending records against the same account's own web table."""
from dataclasses import asdict, is_dataclass
import time
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .models import VPError
from .scoring import codeforces_verdict


def web_verdicts(html: str, url: str, handle: str, expected: dict[str, dict]) -> dict[str, dict]:
    soup = BeautifulSoup(html, 'html.parser')
    if not any(urlsplit(a.get('href', '')).path.rstrip('/').casefold() == '/profile/' + handle.casefold()
               for a in soup.select('#header a[href]')):
        raise VPError('Codeforces web login could not be verified for this account.')
    base = urlsplit(url).path.rstrip('/')
    result = {}
    for row in soup.select('tr[data-submission-id]'):
        sid = row['data-submission-id']
        event = expected.get(sid)
        if event is None:
            continue
        paths = [urlsplit(a['href']).path.rstrip('/') for a in row.select('a[href]')]
        if (base + '/problem/' + event['problem'] not in paths
                or not any(p.casefold() == '/profile/' + handle.casefold() for p in paths)):
            continue
        cell = row.select_one('.status-verdict-cell')
        wrapper = row.select_one('span[submissionid]')
        if (cell is None or cell.get('submissionid', sid) != sid
                or wrapper is not None and wrapper.get('submissionid') != sid):
            continue
        if (cell.get('waiting', '').casefold() == 'true'
                or wrapper is not None and wrapper.get('waiting', '').casefold() == 'true'):
            result[sid] = {'verdict': 'PENDING'}
            continue
        text = cell.get_text(' ', strip=True)
        raw = (wrapper.get('submissionverdict') if wrapper else None) or cell.get('verdict') or (wrapper.get('verdict') if wrapper else None) or text
        pretests = 'pretests passed' in text.casefold()
        parsed = 'CORRECT' if pretests else codeforces_verdict(raw)
        if parsed == 'UNAVAILABLE' and raw not in ('FAILED', 'CRASHED', 'INPUT_PREPARATION_CRASHED', 'SKIPPED', 'REJECTED'):
            continue
        result[sid] = {'verdict': parsed}
        if pretests and 'testset' in event:
            result[sid]['testset'] = 'PRETESTS'
    return result


class PendingJudgements:
    def __init__(self):
        self.saved = {}
        self.since = {}
        self.next_check = 0.0
        self.confirmed = {}

    def seed(self, records):
        self.saved = {str(event['id']): event for item in records
                      if (event := asdict(item) if is_dataclass(item) else dict(item))['verdict'] == 'PENDING'}

    def update(self, api, records: list[dict]) -> list[dict]:
        now = time.monotonic()
        current = {str(e['id']): dict(e) for e in records}
        for sid, event in current.items():
            if event['verdict'] != 'PENDING':
                self.confirmed.pop(sid, None)
        expected = {**self.saved, **current}
        pending = {sid: e for sid, e in expected.items() if e['verdict'] == 'PENDING'}
        self.since = {sid: self.since.get(sid, now) for sid in pending}
        due = {sid: e for sid, e in pending.items() if now - self.since[sid] >= 8}
        def resolved():
            for sid, fields in self.confirmed.items():
                if sid in pending:
                    current[sid] = {**pending[sid], **fields}
            return list(current.values())
        if not due or now < self.next_check:
            return resolved()
        self.next_check = now + 5
        try:
            response = api.web.get(api.url + '/my', params={'locale': 'en'},
                                   headers={'Cache-Control': 'no-cache'}, timeout=(5, 8), retry=False)
            parts = urlsplit(response.url)
            if parts.scheme != 'https' or parts.netloc != 'codeforces.com' or parts.path.rstrip('/') != urlsplit(api.url).path + '/my':
                raise VPError('Codeforces returned an unexpected My submissions page.')
            confirmed = web_verdicts(response.text, api.url, api.handle, due)
        except VPError as exc:
            api.submission_message = (api.submission_message + ' · ' if api.submission_message else '') + str(exc)
            return resolved()
        for sid, fields in confirmed.items():
            if fields['verdict'] == 'PENDING':
                self.confirmed.pop(sid, None)
            else:
                self.confirmed[sid] = fields
        return resolved()
