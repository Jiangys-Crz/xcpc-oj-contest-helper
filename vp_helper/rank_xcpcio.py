"""Import an explicitly selected XCPCIO original contest event archive."""
from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
import math
import re
from urllib.parse import urlsplit

import requests

from .external_rank_http import read_public_json
from .models import Contest, Rules, Submission, TeamScore, VPError
from .originals import OriginalReplay, archive_submissions, board_metadata, identity
from .parsing import timestamp
from .scoring import UNRESOLVED_VERDICTS, score_submissions


def _source(page: str) -> tuple[str, str]:
    parts = urlsplit(page)
    path = parts.path.rstrip('/')
    segments = path.split('/')[1:]
    if (parts.scheme != 'https' or parts.netloc != 'board.xcpcio.com'
            or parts.query or parts.fragment or len(segments) < 3 or segments[0] not in ('icpc', 'ccpc')
            or any(not re.fullmatch(r'[A-Za-z0-9_-]+', segment) for segment in segments)):
        raise VPError('请输入 board.xcpcio.com 的 ICPC 或 CCPC 赛事页面链接。')
    return 'https://board.xcpcio.com' + path, 'https://board.xcpcio.com/data' + path


def _time(value) -> float:
    if isinstance(value, str):
        return timestamp(value)
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('invalid original time')
    return value / 1000 if abs(value) >= 100_000_000_000 else value


def parse(config, teams, organizations, runs, contest: Contest | None, page: str) -> tuple[dict, list[Submission], list[TeamScore]]:
    """The public run list is the authoritative timeline, not a final-rank estimate."""
    page, source = _source(page)
    contest = deepcopy(contest)
    try:
        if not isinstance(config, dict) or not isinstance(teams, (list, dict)) or not teams:
            raise ValueError('missing configuration or roster')
        duration = _time(config['end_time']) - _time(config['start_time'])
        penalty = config['penalty']
        if (not math.isfinite(duration) or duration <= 0 or int(duration) != duration
                or type(penalty) not in (int, float) or not math.isfinite(penalty)
                or penalty < 0 or penalty % 60):
            raise ValueError('unsupported original rules')
        problem_list = config.get('problems')
        if problem_list:
            labels = [p['label'] for p in problem_list]
            ids = [str(p.get('id', i)) for i, p in enumerate(problem_list)]
        else:
            labels = config['problem_id']
            ids = [str(i) for i in range(len(labels))]
        if (not isinstance(labels, list) or not labels
                or any(not isinstance(label, str) or not label.strip() for label in labels)
                or len(set(labels)) != len(labels) or len(set(ids)) != len(ids)
                or any(not ident or ident == 'None' for ident in ids)):
            raise ValueError('invalid problem identities')
        if contest is None:
            title = config.get('contest_name', '')
            contest = Contest('', '', page, title, title, dict.fromkeys(labels, ''),
                              Rules(int(duration), penalty_minutes=int(penalty / 60)))
        if (labels != list(contest.problems) or duration != contest.rules.duration
                or penalty / 60 != contest.rules.penalty_minutes or contest.rules.compile_penalty):
            raise VPError('外榜题目列表、时长或罚时规则与当前 VP 不一致。')
        options = config.get('options', {})
        unit = options.get('submission_timestamp_unit', 'second')
        if unit not in ('millisecond', 'second'):
            raise ValueError('unsupported timestamp unit')
        contest.rules.penalty_rounding = {
            'in_minutes': 'per_problem',
            'accumulate_in_seconds_and_finally_to_the_minute': 'total_seconds',
        }[options.get('calculation_of_penalty', 'in_minutes')]
        roster_data = {}
        for team_key, team in teams.items() if isinstance(teams, dict) else enumerate(teams):
            if not isinstance(team, dict) or not isinstance(team.get('group', []), list):
                raise ValueError('invalid team')
            flags = []
            for key in ('official', 'unofficial'):
                if key in team:
                    if type(team[key]) not in (bool, int) or team[key] not in (0, 1):
                        raise ValueError('invalid team eligibility')
                    flags.append(bool(team[key]) if key == 'official' else not team[key])
            flags.extend(group == 'official' for group in team.get('group', [])
                         if group in ('official', 'unofficial'))
            if not flags or len(set(flags)) != 1:
                raise ValueError('ambiguous team eligibility')
            roster_data[team_key] = {**team, 'official': flags[0]}
        metadata = board_metadata(config, roster_data, contest, source, organizations)
        metadata.update(source_format='xcpcio', page=page, compile_penalty=False,
                        penalty_rounding=contest.rules.penalty_rounding)
        roster = metadata['teams']
        team_ids = [team['id'] for team in roster]
        team_id_set = set(team_ids)
        names = [identity(team['name']) for team in roster]
        if (len(set(team_ids)) != len(roster) or len(set(names)) != len(roster)
                or any(not ident.strip() or ident in ('me', 'None') for ident in team_ids)
                or not any(team['official'] for team in roster)):
            raise ValueError('ambiguous or missing official participants')
        medals = metadata.get('medals')
        if medals is not None and (not isinstance(medals, dict) or any(
                type(medals.get(kind)) is not int or medals[kind] < 0 for kind in ('gold', 'silver', 'bronze'))):
            raise ValueError('invalid medal counts')
        if not isinstance(runs, list):
            raise ValueError('missing event list')
        normalized = []
        for run in runs:
            if not isinstance(run, dict):
                raise ValueError('invalid event')
            # The roster excludes organizer/judge accounts present in some exports.
            if str(run['team_id']) not in team_id_set:
                continue
            ident = run.get('submission_id', run.get('id'))
            if type(ident) not in (str, int) or not str(ident).strip():
                raise ValueError('invalid submission identity')
            at = run['timestamp']
            if type(at) not in (int, float) or not math.isfinite(at):
                raise ValueError('invalid event time')
            if not isinstance(run['status'], str):
                raise ValueError('invalid verdict')
            normalized.append({**run, 'timestamp': at * (1000 if unit == 'second' else 1)})
        events = archive_submissions(normalized, metadata, contest)
        if any(event.verdict in UNRESOLVED_VERDICTS for event in events):
            raise ValueError('unresolved original verdict')
        final = []
        for team in roster:
            row = score_submissions(events, labels, contest.rules, duration,
                                    released=True, team_id=team['id'], name=team['name'])
            row.eligible = team['official']
            final.append(row)
        OriginalReplay(contest, roster, events, final)
        return metadata, events, final
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        raise VPError('XCPCIO 外榜缺少有效的队伍、赛制或完整提交记录。') from None


def load(page: str, contest: Contest | None, *, web=None) -> tuple[dict, list[Submission], list[TeamScore]]:
    page, source = _source(page)
    # Public reads use their own session; never send the judge account's credentials.
    with (nullcontext(web) if web is not None else requests.Session()) as public:
        config = read_public_json(public, source + '/config.json')
        teams = read_public_json(public, source + '/team.json')
        organizations = None
        if not isinstance(config, dict) or not isinstance(teams, (list, dict)):
            raise VPError('XCPCIO 外榜配置或队伍列表格式无效。')
        values = teams.values() if isinstance(teams, dict) else teams
        if any(isinstance(team, dict) and 'organization' not in team and 'organization_id' in team for team in values):
            link = config.get('organizations', {})
            if not isinstance(link, dict) or link.get('url') != 'organizations.json':
                raise VPError('XCPCIO 外榜使用了不支持的学校数据地址。')
            organizations = read_public_json(public, source + '/organizations.json')
        runs = read_public_json(public, source + '/run.json')
    return parse(config, teams, organizations, runs, contest, page)
