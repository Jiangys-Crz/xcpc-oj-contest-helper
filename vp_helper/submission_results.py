"""English result-location messages shared by every submit command."""
from urllib.parse import urlsplit

from .parsing import contest_url
from .receipts import valid_submission_id


def judging_url(base_url: str, submission_id: str | None = None) -> str:
    platform, _, url = contest_url(base_url)
    origin = 'https://' + urlsplit(url).netloc
    sid = str(submission_id) if submission_id is not None else ''
    known = valid_submission_id(sid, platform)
    if platform in ('cf', 'gym'):
        return url + ('/submission/' + sid if known else '/my')
    if platform == 'qoj':
        return origin + '/submission/' + sid if known else url + '/submissions'
    if platform == 'cwnuoj':
        prefix = urlsplit(url).path.rsplit('/contest/', 1)[0]
        return origin + prefix + '/record' + ('/' + sid if known else '')
    return origin + '/acm/contest/view-submission?submissionId=' + sid if known else url + '#submit/'


def print_judging_link(base_url: str, submission_id: str | None = None) -> None:
    print('Check judging results here: ' + judging_url(base_url, submission_id), flush=True)
