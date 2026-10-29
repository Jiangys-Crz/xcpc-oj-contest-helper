"""Bounded, read-only detail checks when a platform's list still says pending."""
from dataclasses import asdict, is_dataclass
import time

from .models import VPError


def seed_pending(api, platform, records):
    if hasattr(api, 'seed_pending_submissions'):
        api.seed_pending_submissions(records)
    elif platform in ('cf', 'gym') and hasattr(api, 'seed_submissions'):
        api.seed_submissions(records)


class PendingResults:
    def __init__(self):
        self.saved = {}
        self.since = {}
        self.next_check = {}
        self.confirmed = {}

    def seed(self, records):
        self.saved = {str(e['id']): e for item in records
                      if (e := asdict(item) if is_dataclass(item) else dict(item))['verdict'] == 'PENDING'}

    def update(self, api, records, lookup):
        now = time.monotonic()
        current = {str(e['id']): dict(e) for e in records}
        for sid, event in current.items():
            if event['verdict'] != 'PENDING':
                self.confirmed.pop(sid, None)
        pending = {sid: e for sid, e in {**self.saved, **current}.items() if e['verdict'] == 'PENDING'}
        self.since = {sid: self.since.get(sid, now) for sid in pending}
        self.next_check = {sid: self.next_check.get(sid, 0) for sid in pending}
        due = sorted((sid for sid in pending if now-self.since[sid] >= 6 and now >= self.next_check[sid]),
                     key=lambda sid: self.next_check[sid])
        for sid in due[:2]:
            self.next_check[sid] = now + 5
            try:
                fields = lookup(pending[sid])
            except VPError as exc:
                api.submission_message += (' · ' if api.submission_message else '') + str(exc)
                continue
            if fields is None:
                continue
            if fields['verdict'] == 'PENDING':
                self.confirmed.pop(sid, None)
            else:
                self.confirmed[sid] = fields
        for sid, fields in self.confirmed.items():
            if sid in pending:
                current[sid] = {**pending[sid], **fields}
        return list(current.values())
