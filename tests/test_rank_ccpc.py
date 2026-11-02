from dataclasses import replace
from datetime import datetime, timedelta
import unittest
from unittest.mock import Mock, patch

from vp_helper.models import Contest, Rules, Submission, VPError
from vp_helper.originals import OriginalReplay
from vp_helper.rank_ccpc import load, parse
from vp_helper.scoring import score_submissions


PAGE = 'https://rank.iccpc.cn/r/e5697b3a-8326-4818-84ca-e2ce1688af23'
SOURCE = 'https://rank.iccpc.cn/upload/outrank_attach/e5697b3a-8326-4818-84ca-e2ce1688af23/rank.json'


def fixture():
    start = datetime(2026, 11, 1, 9)
    rows = []
    for ident, team, problem, result, seconds in (
            (1, 'A01', 100, 6, 20), (2, 'A01', 100, 11, 30),
            (3, 'A01', 100, 4, 59), (4, 'A01', 101, 4, 119),
            (5, 'A02', 100, 4, 10)):
        rows.append({'solution_id': ident, 'contest_id': 1032, 'team_id': team,
                     'problem_id': problem, 'result': result,
                     'in_date': str(start + timedelta(seconds=seconds))})
    return {'contest': {'contest_id': 1032, 'title': 'Fixture CCPC',
                        'start_time': str(start), 'end_time': str(start + timedelta(seconds=300)),
                        'award_ratio': 30020010, 'flg_award_qty_mode': 0, 'flg_award_base': 1},
            'team': [{'contest_id': 1032, 'team_id': 'A01', 'name': 'One', 'school': 'School', 'tkind': 0},
                     {'contest_id': 1032, 'team_id': 'A02', 'name': 'Star', 'school': 'School', 'tkind': 2}],
            'problem': [{'problem_id': 100, 'num': 0, 'submit': 4, 'accepted': 2},
                        {'problem_id': 101, 'num': 1, 'submit': 1, 'accepted': 1}],
            'solution': rows,
            'time_context': {'schema_version': 1, 'wall_clock_timezone': 'Asia/Shanghai',
                             'encoding': 'naive_app_wall'}}


class CCPCImportTests(unittest.TestCase):
    def setUp(self):
        self.contest = Contest('gym', '123', 'https://codeforces.com/gym/123',
                               'Mirror with a different title', 'fixture', {'A': 'A', 'B': 'B'}, Rules(300))
        self.data = fixture()

    def parse(self):
        return parse(self.data, self.contest, PAGE, SOURCE)

    def test_real_wire_schema_replays_star_teams_ce_and_total_second_penalties(self):
        metadata, events, final = self.parse()
        self.assertEqual(metadata['duration'], 300)
        self.assertEqual(metadata['penalty_rounding'], 'total_seconds')
        self.assertEqual(metadata['medals'], {'gold': 1, 'silver': 0, 'bronze': 0})
        self.assertEqual([t['official'] for t in metadata['teams']], [True, False])
        self.assertEqual([s.time for s in events], [20, 30, 59, 119, 10])
        self.assertEqual(final[0].penalty, 22)  # floor((59 + 119 + 1200) / 60)
        self.assertEqual(final[0].cells['A'].tries, 2)  # CE does not add a penalty.
        self.assertEqual(final[0].cells['A'].solve_time, 0)
        self.contest.rules.penalty_rounding = metadata['penalty_rounding']
        replay = OriginalReplay(self.contest, metadata['teams'], events, final)
        early = replay.at(self.contest, 58, released=True)
        self.assertEqual(early.rows[0].solved, 0)
        self.assertEqual(early.rows[1].solved, 1)
        self.assertEqual(replay.at(self.contest, 120, released=True).rows[0].penalty, 22)

    def test_download_uses_stable_official_endpoint_without_stale_timestamp(self):
        web = Mock()
        with patch('vp_helper.rank_ccpc.read_public_json', return_value=self.data) as read:
            load(PAGE + '/', self.contest, web=web)
        read.assert_called_once_with(web, SOURCE)

    def test_no_oj_or_arbitrary_url_is_accepted(self):
        for page in (PAGE.replace('https:', 'http:'), PAGE.replace('rank.iccpc.cn', 'example.com'),
                     PAGE.replace('rank.iccpc.cn', 'user@rank.iccpc.cn'), PAGE + '?t=1', SOURCE):
            with self.subTest(page=page), patch('vp_helper.rank_ccpc.read_public_json') as read:
                with self.assertRaises(VPError):
                    load(page, self.contest)
                read.assert_not_called()

    def test_contest_prefixed_ids_are_normalized_without_losing_underscores(self):
        self.data['team'][0]['team_id'] = '#cpc1032_A_01'
        for row in self.data['solution']:
            if row['team_id'] == 'A01':
                row['team_id'] = '#cpc1032_A_01'
        metadata, events, _ = self.parse()
        self.assertEqual(metadata['teams'][0]['id'], 'A_01')
        self.assertEqual(events[0].team, 'A_01')

    def test_mismatched_rules_and_problems_are_rejected(self):
        for change in ('duration', 'penalty', 'compile', 'problems'):
            with self.subTest(change=change):
                self.contest.rules = Rules(300)
                self.contest.problems = {'A': 'A', 'B': 'B'}
                if change == 'duration': self.contest.rules.duration = 301
                if change == 'penalty': self.contest.rules.penalty_minutes = 10
                if change == 'compile': self.contest.rules.compile_penalty = True
                if change == 'problems': self.contest.problems = {'A': 'A'}
                with self.assertRaises(VPError): self.parse()

    def test_incomplete_hidden_and_ambiguous_exports_fail_closed(self):
        for change in ('missing-event', 'bad-counter', 'pending', 'hidden', 'unknown-result',
                       'unknown-team', 'duplicate-event', 'duplicate-team', 'duplicate-problem',
                       'foreign-contest', 'bad-time', 'bad-zone', 'missing-eligibility'):
            with self.subTest(change=change):
                self.data = fixture()
                if change == 'missing-event': self.data['solution'].pop()
                if change == 'bad-counter': self.data['problem'][0]['accepted'] = 99
                if change == 'pending': self.data['solution'][0]['result'] = 2
                if change == 'hidden': self.data['solution'][0]['result'] = -1
                if change == 'unknown-result': self.data['solution'][0]['result'] = 99
                if change == 'unknown-team': self.data['solution'][0]['team_id'] = 'other'
                if change == 'duplicate-event': self.data['solution'].append(self.data['solution'][0])
                if change == 'duplicate-team': self.data['team'].append(self.data['team'][0])
                if change == 'duplicate-problem': self.data['problem'][1]['problem_id'] = 100
                if change == 'foreign-contest': self.data['solution'][0]['contest_id'] = 1033
                if change == 'bad-time': self.data['solution'][0]['in_date'] = '2026-11-01 09:05:00'
                if change == 'bad-zone': self.data['time_context']['wall_clock_timezone'] = 'Missing/Zone'
                if change == 'missing-eligibility': del self.data['team'][0]['tkind']
                with self.assertRaises(VPError): self.parse()

    def test_zero_submission_teams_and_quantity_medals_do_not_invent_events(self):
        self.data['team'].append({'team_id': 'A03', 'name': 'Absent', 'school': '', 'tkind': 0})
        self.data['contest'].update(award_ratio=1001001, flg_award_qty_mode=1, flg_award_base=2)
        metadata, events, final = self.parse()
        self.assertEqual(len(events), 5)
        self.assertEqual(final[-1].penalty, 0)
        self.assertEqual(metadata['medals'], {'gold': 1, 'silver': 1, 'bronze': 0})


class PenaltyRoundingTests(unittest.TestCase):
    def test_default_remains_per_problem_and_total_seconds_rounds_only_after_sum(self):
        events = [Submission('1', 'me', 'A', 59.9, 'CORRECT'),
                  Submission('2', 'me', 'B', 119.9, 'CORRECT')]
        rules = Rules(300)
        self.assertEqual(score_submissions(events, ['A', 'B'], rules, 300).penalty, 1)
        rounded = score_submissions(events, ['A', 'B'], replace(rules, penalty_rounding='total_seconds'), 300)
        self.assertEqual(rounded.penalty, 2)
        self.assertEqual([rounded.cells[p].solve_time for p in ['A', 'B']], [0, 1])

    def test_frozen_future_post_ac_and_practice_results_do_not_affect_penalty(self):
        events = [Submission('1', 'me', 'A', 59, 'CORRECT'),
                  Submission('2', 'me', 'B', 119, 'CORRECT'),
                  Submission('3', 'me', 'A', 120, 'CORRECT'),
                  Submission('4', 'me', 'B', 130, 'WRONG-ANSWER', practice=True)]
        rules = Rules(300, freeze_at=60, penalty_rounding='total_seconds')
        frozen = score_submissions(events, ['A', 'B'], rules, 300, public=True)
        self.assertEqual(frozen.penalty, 0)
        self.assertEqual(frozen.cells['B'].pending, 1)
        self.assertEqual(score_submissions(events, ['A', 'B'], rules, 60).penalty, 0)
        self.assertEqual(score_submissions(events, ['A', 'B'], rules, 300).penalty, 2)

    def test_unknown_rounding_is_rejected(self):
        with self.assertRaises(VPError): Rules(300, penalty_rounding='nearest')
