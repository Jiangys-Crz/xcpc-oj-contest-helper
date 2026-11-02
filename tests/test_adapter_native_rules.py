"""Native rule metadata and historical records, with no registration writes."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup

from vp_helper.codeforces_live import CodeforcesLive, apply_metadata as cf_metadata, freeze_metadata as cf_freeze
from vp_helper.gym import Gym
from vp_helper.hydro import HydroLive
from vp_helper.models import Contest, Rules, VPError
from vp_helper.nowcoder import NowcoderLive, freeze_metadata as nk_freeze
from vp_helper.qoj import QOJ


def contest(platform="cf", ident="123"):
    host = {"cf": "codeforces.com", "gym": "codeforces.com", "cwnuoj": "oj.cwnupaa.com", "nowcoder": "ac.nowcoder.com", "qoj": "qoj.ac"}[platform]
    path = "/acm/contest/" if platform == "nowcoder" else "/gym/" if platform == "gym" else "/contest/"
    url = "https://" + host + path + ident
    return Contest(platform, ident, url, "Native fixture", "Native fixture", {"A": "10"}, Rules(7200))


def cf_data(scoring="CF"):
    return {"contest": {"id": 123, "name": "Educational fixture", "type": scoring, "phase": "FINISHED",
                        "durationSeconds": 7200, "startTimeSeconds": 1000, "frozen": False},
            "problems": [{"index": "A", "points": 500}],
            "rows": [{"party": {"participantType": "CONTESTANT", "members": [{"handle": "tester"}]},
                      "rank": 1, "points": 450, "penalty": 0,
                      "problemResults": [{"points": 450, "rejectedAttemptCount": 1, "bestSubmissionTimeSeconds": 120, "type": "FINAL"}]}]}


class NativeFreezeTests(unittest.TestCase):
    def test_cf_absent_current_state_is_unknown_but_explicit_zero_disables(self):
        self.assertEqual(cf_freeze({"frozen": False}, 7200), (False, None))
        self.assertEqual(cf_freeze({"freezeDurationSeconds": 0}, 7200), (True, None))
        self.assertEqual(cf_freeze({"freezeDurationSeconds": 3600}, 7200), (True, 3600))
        self.assertEqual(cf_freeze({"freezeDurationSeconds": 7200}, 7200), (True, 0))
        for value in (-1, 7201, True, "3600", float("nan")):
            with self.subTest(value=value), self.assertRaisesRegex(VPError, "freeze duration"):
                cf_freeze({"freezeDurationSeconds": value}, 7200)

    def test_cf_live_exposes_native_rule_maxima_and_preserves_manual_source(self):
        c = contest()
        raw = cf_data("IOI")
        raw["contest"]["freezeDurationSeconds"] = 3600
        cf_metadata(c, raw["contest"], raw["problems"])
        self.assertEqual((c.extra["scoring"], c.extra["score_policy"]), ("IOI", "best"))
        self.assertEqual(c.extra["problem_max_scores"], {"A": 500})
        self.assertEqual(c.rules.freeze_at, 3600)
        self.assertEqual(c.extra["freeze_source"], "platform")
        c.extra["freeze_source"] = "room"
        cf_metadata(c, cf_data()["contest"])
        self.assertEqual(c.extra["freeze_source"], "room")

    def test_nowcoder_public_native_fields_and_explicit_disabled(self):
        # These settingInfo names are published by contest-info?id=132462.
        setting = {"needRankFrozen": True, "rankFrozenMillisecond": 3600000, "rankEndFrozenMillisecond": 0}
        self.assertEqual(nk_freeze(setting, 18000), (True, 14400, 18000))
        self.assertEqual(nk_freeze({"needRankFrozen": False}, 7200), (True, None, None))
        self.assertEqual(nk_freeze({}, 7200), (False, None, None))
        self.assertEqual(nk_freeze({"needRankFrozen": True}, 7200), (False, None, None))
        self.assertEqual(nk_freeze({"needRankFrozen": True, "rankFrozenMinute": 60, "rankEndFrozenMinute": 10}, 7200), (True, 3600, 7800))

    def test_qoj_future_rule_notice_is_distinct_from_current_frozen_state(self):
        for notice, expected, known in (("Scoreboard will be frozen after 01:00:00", 3600, True),
                                         ("Scoreboard will not be frozen", None, True),
                                         ("Scoreboard is not frozen", None, False)):
            with self.subTest(notice=notice):
                api = QOJ("https://qoj.ac/contest/123", {"handle": "tester"})
                api._page = Mock(return_value=BeautifulSoup('<h1>QOJ fixture</h1>Ruleset: ICPC ' + notice +
                    '<table><tr><td>A</td><td><a href="/contest/123/problem/10">A</a></td></tr></table>' +
                    '<a href="download.php?id=1">Statements</a>', "html.parser"))
                api.web.soup = Mock(return_value=BeautifulSoup('<table><tr><td><a href="/contest/123">QOJ fixture</a>2 hours</td></tr></table>', "html.parser"))
                prepared = api.prepare()
                self.assertEqual(prepared.rules.freeze_at, expected)
                self.assertEqual(prepared.extra["freeze_known"], known)


class HydroRuleTests(unittest.TestCase):
    def setUp(self):
        self.c = contest("cwnuoj", "a" * 24)
        self.api = HydroLive(self.c.url, {"handle": "tester", "uid": 42})
        self.api._identified = True
        self.data = {"tdoc": {"docId": self.c.contest_id, "domainId": "system", "title": "CWNUOJ fixture",
                             "rule": "oi", "pids": [10], "score": {"10": 200},
                             "beginAt": "1970-01-01T00:16:40Z", "endAt": "1970-01-01T02:16:40Z"},
                     "tsdoc": {"attend": 1}}

    def test_xcpc_oi_ioi_and_cf_aliases_follow_native_metadata(self):
        for native, scoring, policy in (("acm", "ICPC", "best"), ("xcpc", "ICPC", "best"),
                                        ("oi", "OI", "latest"), ("ioi", "IOI", "best"),
                                        ("strictioi", "IOI", "subtasks"), ("cf", "CF", "best")):
            with self.subTest(rule=native):
                self.data["tdoc"]["rule"] = native
                self.api.apply_metadata(self.c, self.data)
                self.assertEqual((self.c.extra["scoring"], self.c.extra["score_policy"]), (scoring, policy))
                self.assertEqual(self.c.extra["problem_max_scores"], {"A": 200})

    def test_lock_at_is_relative_and_unknown_is_not_silently_disabled(self):
        self.api.apply_metadata(self.c, self.data)
        self.assertFalse(self.c.extra["freeze_known"])
        self.data["tdoc"]["lockAt"] = "1970-01-01T01:16:40Z"
        self.api.apply_metadata(self.c, self.data)
        self.assertEqual(self.c.rules.freeze_at, 3600)
        self.assertTrue(self.c.extra["freeze_known"])
        self.data["tdoc"]["lockAt"] = None
        self.api.apply_metadata(self.c, self.data)
        self.assertIsNone(self.c.rules.freeze_at)
        self.assertTrue(self.c.extra["freeze_known"])

    def test_weighted_scores_apply_to_original_and_preexisting_practice_records(self):
        self.api.apply_metadata(self.c, self.data)
        self.c.extra["practice"] = True
        def record(at, status, score, **extra):
            return {"_id": f"{at:08x}" + "1" * 16, "uid": 42, "pid": 10, "status": status, "score": score, **extra}
        self.api.get_json = Mock(side_effect=[
            {"rdocs": [record(1100, 1, 100, contest=self.c.contest_id)]}, {"rdocs": []},
            {"rdocs": [record(900, 2, 50)]}, {"rdocs": []}])
        events = self.api.submissions(self.c)
        self.assertEqual([(e["time"], e["points"], e["verdict"]) for e in events], [(100, 200, "CORRECT"), (-100, 100, "WRONG-ANSWER")])
        self.assertTrue(self.api.submissions_complete)
        self.assertEqual(self.api.get_json.call_args_list[0].kwargs["tid"], self.c.contest_id)
        self.assertNotIn("tid", self.api.get_json.call_args_list[2].kwargs)

    def test_hidden_contest_history_does_not_block_available_practice_history(self):
        self.api.apply_metadata(self.c, self.data)
        self.c.extra["practice"] = True
        self.api.get_json = Mock(side_effect=[VPError("HTTP 403"),
            {"rdocs": [{"_id": f"{900:08x}" + "1" * 16, "uid": 42, "pid": 10, "status": 1, "score": 100}]},
            {"rdocs": []}])
        events = self.api.submissions(self.c)
        self.assertEqual(events[0]["verdict"], "CORRECT")
        self.assertFalse(self.api.submissions_complete)
        self.assertIn("available results synced", self.api.submission_message)


class ExistingNativeHistoryTests(unittest.TestCase):
    def test_cf_bt_imports_web_virtual_and_practice_before_workspace_anchor(self):
        c = contest()
        c.original_start = 2000
        c.extra["practice"] = True
        api = CodeforcesLive(c.url, {"handle": "tester"})
        api.api = Mock(return_value=[
            {"id": i, "contestId": 123, "creationTimeSeconds": 1000 + i, "problem": {"index": "A"},
             "author": {"participantType": kind, "members": [{"handle": "tester"}]}, "verdict": verdict}
            for i, kind, verdict in ((1, "CONTESTANT", "WRONG_ANSWER"), (2, "VIRTUAL", "OK"), (3, "PRACTICE", "OK"))])
        events = api.submissions(c)
        self.assertEqual([e["verdict"] for e in events], ["WRONG-ANSWER", "CORRECT", "CORRECT"])
        self.assertTrue(all(e["time"] < 0 for e in events))

    def test_nowcoder_oi_ioi_metadata_and_existing_native_history(self):
        c = contest("nowcoder")
        api = NowcoderLive(c.url, {"handle": "42", "uid": 42})
        api._identified = True
        data = {"contestId": 123, "name": "Nowcoder fixture", "type": 2, "startTime": 1000000, "endTime": 8200000,
                "contestType": 0, "isSignUp": True, "settingInfo": {"needRankFrozen": False}}
        api.apply_metadata(c, data)
        self.assertEqual((c.extra["scoring"], c.extra["score_policy"]), ("OI", "latest"))
        data["type"] = 3
        api.apply_metadata(c, data)
        self.assertEqual((c.extra["scoring"], c.extra["score_policy"]), ("IOI", "best"))
        c.extra["practice"] = True
        api.get_json = Mock(return_value={"basicInfo": {"basicUid": 42, "contestId": 123, "pageCurrent": 1, "pageCount": 1, "statusCount": 1},
                                        "data": [{"submissionId": 77, "userId": 42, "problemId": 10, "submitTime": 900000,
                                                  "statusMessage": "答案正确", "score": 50, "fullScore": 50}]})
        event = api.submissions(c)[0]
        self.assertEqual((event["time"], event["verdict"]), (-100, "CORRECT"))
        self.assertEqual(c.extra["problem_max_scores"], {"A": 50})

    def test_qoj_bt_imports_earlier_web_records_without_stopping_pagination(self):
        c = contest("qoj")
        c.extra["practice"] = True
        api = QOJ(c.url, {"handle": "tester"})
        api.web.get = Mock(side_effect=VPError('No detail metadata in this fixture'))
        api.start_time = 2000
        def page(sid, at, pages=""):
            return BeautifulSoup('<table><thead><tr><th>ID</th><th>User</th><th>Problem</th><th>Result</th><th>Time</th></tr></thead><tbody>'
                f'<tr><td><a href="/submission/{sid}">{sid}</a></td><td><a class="uoj-username" href="/user/profile/tester">tester</a></td>'
                f'<td><a href="/problem/10">A</a></td><td>Accepted</td><td><time datetime="{at}">{at}</time></td></tr>'
                '</tbody></table>' + pages, "html.parser")
        api._page = Mock(side_effect=[page(2, "1970-01-01T00:20:00Z", '<a href="?page=2">2</a>'), page(1, "1970-01-01T00:18:20Z")])
        events = api.submissions(c)
        self.assertEqual({e.id for e in events}, {"1", "2"})
        self.assertTrue(all(e.time < 0 for e in events))
        self.assertEqual(api._page.call_count, 2)


class CodeforcesNativeVPTests(unittest.TestCase):
    def test_div_educational_and_ioi_originals_register_native_icpc_virtuals(self):
        for native in ("CF", "ICPC", "IOI"):
            with self.subTest(native=native):
                api = Gym("https://codeforces.com/contest/123", {"handle": "tester"})
                api.api = Mock(return_value=cf_data(native))
                api.web = Mock()
                api.web.soup.return_value = BeautifulSoup('<a href="/attachments/statements.pdf">Statement</a>', "html.parser")
                prepared = api.prepare()
                self.assertEqual(prepared.extra["scoring"], "ICPC")
                self.assertEqual(prepared.extra["original_scoring"], native)
                self.assertFalse(prepared.extra["freeze_known"])
                api.web.post.assert_not_called()

    def test_cf_original_comparison_replays_icpc_events_instead_of_cf_points(self):
        c = contest()
        c.problems = {"A": "A"}
        api = Gym(c.url, {"handle": "tester"})
        data = cf_data()
        api._original = data
        def response(method, **params):
            if method == "contest.standings":
                return deepcopy(data)
            return [{"id": 2, "relativeTimeSeconds": 240, "problem": {"index": "A"},
                     "author": data["rows"][0]["party"], "verdict": "OK"},
                    {"id": 1, "relativeTimeSeconds": 100, "problem": {"index": "A"},
                     "author": data["rows"][0]["party"], "verdict": "WRONG_ANSWER"}]
        api.api = Mock(side_effect=response)
        with tempfile.TemporaryDirectory() as tmp, patch("vp_helper.originals.cache_path", return_value=Path(tmp) / "events.json"):
            reference = api.reference(c, 7200)
        self.assertTrue(reference.complete)
        self.assertEqual(reference.rows[0].solved, 1)
        self.assertEqual(reference.rows[0].penalty, 24)
        self.assertNotEqual(reference.rows[0].penalty, data["rows"][0]["penalty"])

    def test_gymlive_keeps_its_platform_and_fetches_native_standings(self):
        api = CodeforcesLive("https://codeforces.com/gym/123", {"handle": "tester"}, allow_gym=True)
        data = cf_data("ICPC")
        api.web = Mock()
        api.web.soup.return_value = BeautifulSoup('<div id="header"><a href="/profile/tester">tester</a></div>', "html.parser")
        api.api = Mock(return_value=data)
        prepared, snapshot = api.prepare()
        self.assertEqual(prepared.platform, "gym")
        self.assertEqual(snapshot["own"]["rank"], 1)
        self.assertEqual(api.api.call_args_list[0].args, ("contest.standings",))
        api.web.post.assert_not_called()

