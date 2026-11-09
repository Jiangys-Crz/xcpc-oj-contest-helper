"""Read the public ICCPC rank.json event export, without judge credentials."""
from __future__ import annotations

from collections import Counter
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime
import json
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from .external_rank_http import read_public_json
from .models import Contest, Rules, Submission, TeamScore, VPError
from .scoring import score_submissions


# rank.iccpc.cn/static/ojweb/csgoj/contest/rank_core.js defines these results,
# excludes CE from penalties, and identifies tkind == 2 as starred teams.
RESULTS = {4: 'CORRECT', 5: 'FORMAT-ERROR', 6: 'WRONG-ANSWER', 7: 'TIMELIMIT',
           8: 'MEMORY-LIMIT', 9: 'OUTPUT-LIMIT', 10: 'RUN-ERROR', 11: 'COMPILER-ERROR'}


def _integer(value) -> int:
    if type(value) is int:
        return value
    if isinstance(value, str) and re.fullmatch(r'\d+', value):
        return int(value)
    raise ValueError('expected an integer')


def _ident(value) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if type(value) is int:
        return str(value)
    raise ValueError('missing identity')


def _team_id(value, contest_id: str) -> str:
    value = _ident(value)
    if value.startswith('#'):
        prefix = '#cpc' + contest_id + '_'
        if not value.startswith(prefix) or len(value) == len(prefix):
            raise ValueError('foreign contest team ID')
        value = value[len(prefix):]
    if value == 'me':
        raise ValueError('reserved team ID')
    return value


def _label(index: int) -> str:
    result = ''
    while index >= 0:
        result = chr(65 + index % 26) + result
        index = index // 26 - 1
    return result


def _medals(raw: dict, data: dict, teams: list[dict], events: list[Submission], final: list[TeamScore]):
    """Use the official cumulative-ceil/count rules for a single team award pool."""
    # Multi-pool and custom school awards cannot be represented by three team counts.
    if data.get('is_multi_group') and len(data.get('contest_group', [])) > 1:
        return None
    try:
        addition = raw.get('addition') or {}
        if isinstance(addition, str):
            addition = json.loads(addition)
        cfg = (addition or {}).get('award_cfg', {}).get('medal', {})
        if (cfg.get('scope', 'team') != 'team' or cfg.get('star_eligible', False)
                or cfg.get('rounding', 'cumulative_ceil') != 'cumulative_ceil'):
            return None
        packed = _integer(raw['award_ratio'])
        if not 0 <= packed < 1_000_000_000:
            return None
        mode = _integer(raw.get('flg_award_qty_mode', 0))
        base_mode = _integer(raw.get('flg_award_base', 0))
        if mode not in (0, 1) or base_mode not in (0, 1, 2):
            return None
        submitted = {event.team for event in events}
        solved = {row.id for row in final if row.solved}
        base = sum(t['official'] and (base_mode == 2 or
                   t['id'] in (submitted if base_mode == 1 else solved)) for t in teams)
        bands = (packed % 1000, packed // 1000 % 1000, packed // 1_000_000)
        counts, line, accumulated = [], 0, 0
        for value in bands:
            previous = line
            if mode == 1 or value >= 100:
                line = min(base, line + (value if mode == 1 else value - 100))
                accumulated = line * 100
            else:
                accumulated += base * value
                line = min(base, (accumulated + 99) // 100)
            counts.append(line - previous)
        return dict(zip(('gold', 'silver', 'bronze'), counts))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def parse(data: dict, contest: Contest | None, page: str, source: str):
    try:
        raw = data['contest']
        contest_id = _ident(raw['contest_id'])
        context = data.get('time_context') or {}
        if context.get('encoding', 'naive_app_wall') != 'naive_app_wall':
            raise ValueError('unsupported timestamp encoding')
        zone = ZoneInfo(context.get('wall_clock_timezone', 'Asia/Shanghai'))

        def at(value):
            dt = datetime.fromisoformat(value)
            return (dt.replace(tzinfo=zone) if dt.tzinfo is None else dt).timestamp()

        start, end = at(raw['start_time']), at(raw['end_time'])
        duration = end - start
        if duration <= 0 or int(duration) != duration:
            raise ValueError('contest rules do not match')
        if not all(isinstance(data.get(key), list) for key in ('team', 'problem', 'solution')):
            raise ValueError('missing roster, problems or timeline')
        problems = sorted(data['problem'], key=lambda p: _integer(p['num']))
        if [_integer(p['num']) for p in problems] != list(range(len(problems))):
            raise ValueError('ambiguous problem order')
        labels = [_label(i) for i in range(len(problems))]
        if contest is None:
            title = raw.get('title', '')
            contest = Contest('', '', page, title, title, dict.fromkeys(labels, ''), Rules(int(duration)))
        if (duration != contest.rules.duration or contest.rules.penalty_minutes != 20
                or contest.rules.compile_penalty):
            raise ValueError('contest rules do not match')
        if not labels or labels != list(contest.problems):
            raise ValueError('problem labels do not match')
        mapping = {_ident(p['problem_id']): label for p, label in zip(problems, labels)}
        if len(mapping) != len(labels):
            raise ValueError('duplicate problem identity')
        teams, known = [], set()
        for team in data['team']:
            ident = _team_id(team['team_id'], contest_id)
            kind = _integer(team['tkind'])
            if (ident in known or kind not in (0, 1, 2)
                    or _ident(team.get('contest_id', contest_id)) != contest_id
                    or str(team.get('privilege', '')).strip().lower() not in ('', 'default')):
                raise ValueError('ambiguous team eligibility')
            name, school = team['name'], team['school']
            if not isinstance(name, str) or not name.strip() or not isinstance(school, str):
                raise ValueError('missing team name')
            known.add(ident)
            teams.append({'id': ident, 'name': f'{school} - {name} ({ident})', 'official': kind != 2,
                          'organization': school, 'team_name': name})
        events, seen = [], set()
        submitted, accepted = Counter(), Counter()
        for row in data['solution']:
            ident = _ident(row['solution_id'])
            team = _team_id(row['team_id'], contest_id)
            problem = _ident(row['problem_id'])
            result = _integer(row['result'])
            elapsed = at(row['in_date']) - start
            if (ident in seen or team not in known or problem not in mapping or result not in RESULTS
                    or _ident(row.get('contest_id', contest_id)) != contest_id
                    or not 0 <= elapsed < duration):
                raise ValueError('unsupported, hidden or incomplete submission event')
            seen.add(ident)
            submitted[problem] += 1
            accepted[problem] += result == 4
            events.append(Submission(ident, team, mapping[problem], elapsed, RESULTS[result]))
        # These independent export counters detect truncated/partially hidden timelines.
        for problem in problems:
            ident = _ident(problem['problem_id'])
            if (_integer(problem['submit']) != submitted[ident]
                    or _integer(problem['accepted']) != accepted[ident]):
                raise ValueError('submission timeline does not match the export counters')
        if not teams or not any(team['official'] for team in teams):
            raise ValueError('no official teams')
        rules = replace(contest.rules, penalty_rounding='total_seconds')
        final = []
        for team in teams:
            row = score_submissions(events, labels, rules, duration, released=True,
                                    team_id=team['id'], name=team['name'])
            row.eligible = team['official']
            final.append(row)
        metadata = {'version': 1, 'contest_name': contest.name, 'source': source,
                    'source_page': page, 'source_format': 'ccpc', 'duration': int(duration),
                    'penalty_minutes': 20, 'penalty_rounding': 'total_seconds',
                    'original_start': start, 'problems': labels, 'problem_ids': mapping,
                    'teams': teams, 'medals': _medals(raw, data, teams, events, final)}
        return metadata, events, final
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, ZoneInfoNotFoundError):
        raise VPError('CCPC 外榜的题目、赛制、队伍或提交时间轴不完整，或与当前比赛不匹配。') from None


def load(page: str, contest: Contest | None, *, web=None) -> tuple[dict, list[Submission], list[TeamScore]]:
    parts = urlsplit(page)
    match = re.fullmatch(r'/r/([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})/?', parts.path)
    if (parts.scheme != 'https' or parts.netloc != 'rank.iccpc.cn'
            or parts.query or parts.fragment or not match):
        raise VPError('CCPC 外榜链接格式应为 https://rank.iccpc.cn/r/赛事ID。')
    page = 'https://rank.iccpc.cn/r/' + match[1].lower()
    source = 'https://rank.iccpc.cn/upload/outrank_attach/' + match[1].lower() + '/rank.json'
    with (nullcontext(web) if web is not None else requests.Session()) as public:
        data = read_public_json(public, source)
    return parse(data, contest, page, source)
