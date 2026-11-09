from copy import deepcopy
from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

from vp_helper.models import Contest, Rules, VPError
from vp_helper.originals import OriginalReplay
from vp_helper.rank_xcpcio import load, parse


PAGE = 'https://board.xcpcio.com/icpc/51st/wuhan'
CCPC_PAGE = 'https://board.xcpcio.com/ccpc/9th/qinhuangdao'


def fixture():
    # Minimal version of the current XCPCIO config/team/organization/run schema.
    config = {
        'start_time': 1793498400000, 'end_time': 1793516400000, 'penalty': 1200,
        'problems': [{'id': '0', 'label': 'A'}, {'id': '1', 'label': 'B'}],
        'options': {'submission_timestamp_unit': 'millisecond'},
        'organizations': {'url': 'organizations.json'},
        'medal': {'official': {'gold': 1, 'silver': 0, 'bronze': 0}},
    }
    localized = {'fallback_lang': 'zh-CN', 'texts': {'zh-CN': '选手'}}
    teams = [
        {'id': 'E70', 'name': '正式队', 'organization_id': 'school',
         'group': ['official'], 'members': [{'name': localized}]},
        {'id': 'E71', 'name': '打星队', 'organization_id': 'school',
         'group': ['unofficial'], 'members': [{'name': localized}]},
        {'id': 'E72', 'name': '零提交队', 'organization_id': 'school',
         'group': ['official'], 'members': [{'name': localized}]},
    ]
    organizations = [{'id': 'school', 'name': '示例大学'}]
    runs = [
        {'id': '1', 'team_id': 'E70', 'problem_id': '0', 'timestamp': 60000, 'status': 'COMPILATION_ERROR'},
        {'id': '2', 'team_id': 'E70', 'problem_id': '0', 'timestamp': 61000, 'status': 'WRONG_ANSWER'},
        {'id': '3', 'team_id': 'E70', 'problem_id': '0', 'timestamp': 180999, 'status': 'ACCEPTED'},
        {'id': '4', 'team_id': 'E71', 'problem_id': '0', 'timestamp': 120000, 'status': 'ACCEPTED'},
        {'id': '5', 'team_id': 'E70', 'problem_id': '1', 'timestamp': 14500000, 'status': 'ACCEPTED'},
    ]
    return config, teams, organizations, runs


class XCPCIOImportTests(unittest.TestCase):
    def setUp(self):
        self.data = fixture()
        self.contest = Contest('gym', '123', 'https://codeforces.com/gym/123',
                               'Mirror title', 'Mirror title', {'A': 'A', 'B': 'B'},
                               Rules(18000, freeze_at=14400))

    def parse(self, data=None, contest=None):
        return parse(*(data or self.data), contest or self.contest, PAGE)

    def test_current_schema_replays_real_time_preserves_starred_and_ignores_ce_penalty(self):
        self.data[1][0]['group'].append('girl')
        metadata, events, final = self.parse()
        self.assertEqual(metadata['duration'], 18000)
        self.assertEqual(metadata['penalty_minutes'], 20)
        self.assertFalse(metadata['compile_penalty'])
        self.assertEqual(metadata['medals'], {'gold': 1, 'silver': 0, 'bronze': 0})
        self.assertEqual(metadata['teams'][0]['name'], '示例大学 - 正式队 - 选手')
        self.assertIs(metadata['teams'][0]['female'], True)
        self.assertFalse(metadata['teams'][1]['official'])
        self.assertEqual(events[2].time, 180.999)
        self.assertEqual(events[0].verdict, 'COMPILER-ERROR')
        self.assertEqual(final[0].cells['A'].tries, 2)
        self.assertEqual(final[0].cells['A'].solve_time, 3)
        self.assertEqual(final[0].penalty, 3 + 20 + 241)
        self.assertFalse(final[1].eligible)
        self.assertEqual(final[2].solved, 0)
        replay = OriginalReplay(self.contest, metadata['teams'], events, final)
        before = replay.at(self.contest, 180).rows[0]
        self.assertFalse(before.cells['A'].solved)
        self.contest.rules.problem_reveal_count = 50
        frozen = replay.at(self.contest, 15000).rows[0]
        self.assertFalse(frozen.cells['B'].solved)
        self.assertEqual(frozen.cells['B'].pending, 1)
        self.assertEqual(replay.statistics_at(self.contest, 15000)['submitted']['B'], 0)
        self.assertTrue(replay.at(self.contest, 18000, released=True).rows[0].cells['B'].solved)

    def test_load_uses_all_four_public_files_and_a_separate_session(self):
        config, teams, organizations, runs = self.data
        with patch('vp_helper.rank_xcpcio.requests.Session') as factory, \
             patch('vp_helper.rank_xcpcio.read_public_json', side_effect=[config, teams, organizations, runs]) as read:
            load(PAGE + '/', self.contest)
        factory.assert_called_once_with()
        expected = ['config.json', 'team.json', 'organizations.json', 'run.json']
        for call, filename in zip(read.call_args_list, expected):
            self.assertIs(call.args[0], factory.return_value.__enter__.return_value)
            self.assertEqual(call.args[1], 'https://board.xcpcio.com/data/icpc/51st/wuhan/' + filename)

    def test_legacy_roster_and_problem_labels_without_options(self):
        config, _, _, runs = deepcopy(self.data)
        config.pop('problems')
        config.pop('options')
        config['problem_id'] = ['A', 'B']
        for run in runs:
            run['timestamp'] /= 1000
        teams = {ident: {'name': name, 'organization': 'School', 'members': ['member'],
                         'official': official}
                 for ident, name, official in [('E70', 'One', True), ('E71', 'Two', False)]}
        metadata, events, final = self.parse((config, teams, None, runs))
        self.assertEqual(metadata['problem_ids'], {'0': 'A', '1': 'B'})
        self.assertEqual(len(final), 2)
        self.assertEqual(events[2].time, 180.999)

    def test_qinhuangdao_archive_seconds_eligibility_and_total_penalty(self):
        config, _, _, runs = deepcopy(self.data)
        config['options'] = {'calculation_of_penalty': 'accumulate_in_seconds_and_finally_to_the_minute'}
        teams = [
            {'team_id': 'E70', 'name': 'One', 'organization': 'School', 'members': ['Member'],
             'official': 1, 'unofficial': False},
            {'team_id': 'E71', 'name': 'Two', 'organization': 'School', 'members': ['Member'],
             'official': False, 'unofficial': 1},
        ]
        for run, at in zip(runs, (4, 9, 179, 120, 14519)):
            run['timestamp'] = at
        with patch('vp_helper.rank_xcpcio.read_public_json', side_effect=[config, teams, runs]) as read:
            metadata, events, final = load(CCPC_PAGE + '/', self.contest, web=Mock())
        self.assertEqual(read.call_args_list[0].args[1],
                         'https://board.xcpcio.com/data/ccpc/9th/qinhuangdao/config.json')
        self.assertEqual(metadata['penalty_rounding'], 'total_seconds')
        self.assertEqual(events[2].time, 179)
        self.assertEqual(final[0].penalty, (179 + 1200 + 14519) // 60)
        self.assertFalse(final[1].eligible)
        self.assertEqual(self.contest.rules.penalty_rounding, 'per_problem')

    def test_declared_second_timestamps_are_converted_without_rounding(self):
        config, teams, organizations, runs = deepcopy(self.data)
        config['options']['submission_timestamp_unit'] = 'second'
        for run in runs:
            run['timestamp'] /= 1000
        self.assertEqual(self.parse((config, teams, organizations, runs))[1][2].time, 180.999)

    def test_switching_from_ccpc_uses_icpc_rounding_without_mutating_contest(self):
        contest = replace(self.contest, rules=Rules(18000, penalty_rounding='total_seconds'))
        metadata, _, final = self.parse(contest=contest)
        self.assertEqual(metadata['penalty_rounding'], 'per_problem')
        self.assertEqual(final[0].penalty, 3 + 20 + 241)
        self.assertEqual(contest.rules.penalty_rounding, 'total_seconds')

    def test_different_rules_or_problem_set_are_rejected(self):
        for contest in (
                replace(self.contest, rules=Rules(14400)),
                replace(self.contest, rules=Rules(18000, penalty_minutes=10)),
                replace(self.contest, rules=Rules(18000, compile_penalty=True)),
                replace(self.contest, problems={'A': 'A', 'C': 'C'})):
            with self.subTest(contest=contest), self.assertRaises(VPError):
                self.parse(contest=contest)

    def test_ambiguous_rosters_and_problem_ids_are_rejected(self):
        changes = [
            lambda d: d[1][1].update(id='E70'),
            lambda d: d[1][0].update(official=False),
            lambda d: d[1][0].update(group=['official', 'unofficial']),
            lambda d: d[1][0].update(group=[]),
            lambda d: d[1][0].update(id='me'),
            lambda d: d[0]['problems'][1].update(id='0'),
            lambda d: d[0]['problems'][1].update(label='A'),
        ]
        for change in changes:
            data = deepcopy(self.data)
            change(data)
            with self.subTest(change=change), self.assertRaises(VPError):
                self.parse(data)

    def test_pending_unknown_or_invalid_events_are_rejected(self):
        changes = [
            {'status': 'PENDING'}, {'status': 'UNAVAILABLE'}, {'status': 'UNKNOWN'},
            {'timestamp': float('nan')}, {'timestamp': -1}, {'timestamp': 18000000},
            {'timestamp': True}, {'id': ''}, {'id': None}, {'problem_id': 'unknown'},
        ]
        for change in changes:
            data = deepcopy(self.data)
            data[3][0].update(change)
            with self.subTest(change=change), self.assertRaises(VPError):
                self.parse(data)
        data = deepcopy(self.data)
        data[3].append(data[3][0])
        with self.assertRaises(VPError):
            self.parse(data)

    def test_unknown_organizer_runs_do_not_add_competing_teams(self):
        data = deepcopy(self.data)
        data[3].append({'team_id': 'judge-account'})
        _, events, final = self.parse(data)
        self.assertEqual(len(events), 5)
        self.assertEqual(len(final), 3)

    def test_invalid_rules_and_units_are_rejected(self):
        for change in ({'penalty': 1200.5}, {'penalty': True}, {'end_time': float('inf')},
                       {'options': {'submission_timestamp_unit': 'minute'}}):
            data = deepcopy(self.data)
            data[0].update(change)
            with self.subTest(change=change), self.assertRaises(VPError):
                self.parse(data)

    def test_bad_urls_never_reach_the_network(self):
        for url in ('http://board.xcpcio.com/icpc/51st/wuhan', PAGE + '?url=other', PAGE + '#x',
                    'https://board.xcpcio.com.evil.test/icpc/51st/wuhan',
                    'https://user@board.xcpcio.com/icpc/51st/wuhan',
                    'https://board.xcpcio.com/icpc/../wuhan',
                    'https://board.xcpcio.com/icpc/%2e%2e/wuhan'):
            with self.subTest(url=url), patch('vp_helper.rank_xcpcio.read_public_json') as read:
                with self.assertRaises(VPError):
                    load(url, self.contest)
                read.assert_not_called()

    def test_external_organization_redirect_is_not_followed(self):
        config, teams, _, _ = deepcopy(self.data)
        config['organizations']['url'] = 'https://other.test/organizations.json'
        with patch('vp_helper.rank_xcpcio.read_public_json', side_effect=[config, teams]) as read:
            with self.assertRaises(VPError):
                load(PAGE, self.contest, web=Mock())
        self.assertEqual(read.call_count, 2)
