from contextlib import ExitStack
from copy import deepcopy
import io
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests
from rich.console import Console

from vp_helper import browser_login, live, nowcoder_submit
from vp_helper.http import _RequestException
from vp_helper.models import Contest, Rules, VPError
from vp_helper.nowcoder import INFO, PROBLEMS, RANK, STATUS, ORIGIN, NowcoderLive, identity_from_html, problem_config
from vp_helper.parsing import contest_url
from vp_helper.receipts import ReceiptReader
from vp_helper.storage import read_json, write_json
from vp_helper.tui import LiveDashboard

URL = ORIGIN + "/acm/contest/123"


def page(uid=42):
    return f'''<script>window.isLogin = true;
window.globalInfo = {{ ownerId: '{uid}', ownerName: 'tester' }};
window.pageInfo = {{uid:999}};</script>
<div class="acm-header"><a href="/acm/contest/profile/{uid}">tester</a></div>
<script>window.pageInfo = {{
contestId: '123',
questionId: '1001',
problemId: '10',
tagId: '4',
subTagId: '1',
doneQuestionId: '123',
codeJudgeType: '0',
supportLang: 'java,cpp'
}};</script>'''.replace('window.pageInfo = {uid:999};', 'window.author = {uid:999};')


def details(kind=1, now=1200):
    return {"contestId": 123, "name": "Synthetic Nowcoder contest", "type": kind,
            "startTime": 1000000, "endTime": 8200000, "countDownTime": (1000-now)*1000,
            "isSignUp": True, "isTeamSignUp": False, "contestType": 0, "rankingVisible": True,
            "settingInfo": {"needRankFrozen": False}, "uid": 999}


def problems():
    return {"basicInfo": {"contestId": 123, "pageCurrent": 1, "pageCount": 1, "problemCount": 2},
            "data": [{"index": "A", "problemId": 10, "submitCount": 30, "submitPersonCount": 20, "acceptedCount": 10},
                     {"index": "B", "problemId": 11, "submitCount": 5, "submitPersonCount": 4, "acceptedCount": 2}]}


def rank():
    return {"basicInfo": {"contestId": 123, "basicUid": -1, "postContestRankSupported": True, "onlyContestRankApplied": False},
            "problemData": [{"problemId": 10, "acceptedCount": 12}],
            "rankData": [{"uid": 99, "ranking": 1, "team": False}],
            "myRankData": {"uid": 42, "userName": "tester", "team": False, "ranking": 17,
                           "acceptedCount": 1, "totalScore": 130, "penaltyTime": 1230000,
                           "scoreList": [{"problemId": 10, "accepted": True, "score": 100, "submit": True, "failedCount": 2},
                                         {"problemId": 11, "accepted": False, "score": 30, "submit": True, "failedCount": 1}]}}


def status(records, page_num=1, pages=1, count=None):
    return {"basicInfo": {"contestId": 123, "basicUid": 42, "pageCurrent": page_num, "pageCount": pages,
                          "statusCount": len(records) if count is None else count}, "data": records}


def record(sid=77, uid=42, when=1100, verdict="答案正确"):
    return {"submissionId": sid, "userId": uid, "isTeam": False, "problemId": 10,
            "submitTime": when*1000, "statusMessage": verdict, "score": 100}


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.api = NowcoderLive(URL, {"handle": "42", "uid": 42, "cookie_source": "manual"})
        self.api._identified = True
        self.contest = Contest("nowcoder", "123", URL, "test", "test", {}, Rules(1))
        self.info, self.ps, self.ranking = details(), problems(), rank()
        self.api.details = Mock(side_effect=lambda: deepcopy(self.info))
        self.api.get_json = Mock(side_effect=lambda path, **kw: deepcopy(self.ps if path == PROBLEMS else self.ranking))

    def test_url_requires_exact_contest_path_and_host(self):
        self.assertEqual(contest_url(URL + "/?x=1"), ("nowcoder", "123", URL))
        for url in (URL + "/A", URL.replace("ac.nowcoder.com", "ac.nowcoder.com.example.com"), URL.replace("123", "0")):
            with self.subTest(url=url), self.assertRaises(VPError):
                contest_url(url)

    def test_identity_ignores_author_and_unrelated_user_profiles(self):
        self.assertEqual(identity_from_html(page())["uid"], 42)
        with self.assertRaises(VPError):
            identity_from_html('<script>window.isLogin = false; window.pageInfo = {uid:42};</script><a href="/acm/contest/profile/42">x</a>')
        with self.assertRaises(VPError):
            identity_from_html(page().replace("ownerId: '42'", "ownerId: '99'"))

    def test_safe_page_fields_not_executed(self):
        self.assertEqual(problem_config(page())["questionId"], "1001")
        self.assertEqual(problem_config(page())["doneQuestionId"], "123")
        with self.assertRaises(VPError):
            problem_config("login required")

    def test_registration_team_and_replay_are_checked_before_problem_fetch(self):
        for key, value in (("isSignUp", False), ("isTeamSignUp", True), ("contestType", 1)):
            with self.subTest(key=key):
                self.info = {**details(), key: value}
                with self.assertRaises(VPError):
                    self.api.snapshot(self.contest)
        self.api.get_json.assert_not_called()

    def test_before_start_waits_without_fetching_problems(self):
        self.info = details(now=900)
        self.assertEqual(self.api.snapshot(self.contest)["phase"], "BEFORE")
        self.api.get_json.assert_not_called()

    def test_original_acm_rank_penalty_and_merged_public_statistics(self):
        snap = self.api.snapshot(self.contest)
        self.assertEqual((snap["own"]["rank"], snap["own"]["points"], snap["own"]["penalty"]), (17, 1, "20.5"))
        self.assertEqual([p["display"] for p in snap["own"]["problemResults"]], ["+2", "−1"])
        self.assertEqual([p["points"] for p in snap["own"]["problemResults"]], [1, 0])
        self.assertEqual(snap["stats"]["attempted"], {"A": 20, "B": 4})
        self.assertEqual(snap["stats"]["accepted"]["A"], 12)
        self.assertEqual(self.contest.problems, {"A": "10", "B": "11"})
        self.assertFalse(any("ignoreRankFrozen" in c.kwargs for c in self.api.get_json.call_args_list))

    def test_ioi_and_weekly_use_server_total_and_partial_scores(self):
        for kind in (3, 4):
            self.info = details(kind)
            snap = self.api.snapshot(self.contest)
            self.assertEqual(snap["own"]["points"], 130)
            self.assertEqual(snap["own"]["problemResults"][1]["display"], "30")

    def test_oi_hidden_scoreboard_not_requested(self):
        self.info = details(2)
        snap = self.api.snapshot(self.contest)
        self.assertIsNone(snap["own"])
        self.assertEqual([c.args[0] for c in self.api.get_json.call_args_list], [PROBLEMS])

    def test_frozen_clock_follows_site_milliseconds(self):
        self.info = details(now=8100)
        self.info["settingInfo"] = {"needRankFrozen": True, "rankFrozenMillisecond": 300000, "rankEndFrozenMillisecond": 600000}
        self.assertTrue(self.api.snapshot(self.contest)["frozen"])
        self.info["countDownTime"] = (1000-9000)*1000
        self.assertFalse(self.api.snapshot(self.contest)["frozen"])

    def test_final_rank_requires_confirmed_contest_only_filter(self):
        self.info = details(now=9000)
        self.assertIsNone(self.api.snapshot(self.contest)["own"])
        self.assertEqual(self.api.get_json.call_args.kwargs["onlyContestRank"], "true")
        self.ranking["basicInfo"]["onlyContestRankApplied"] = True
        self.assertEqual(self.api.snapshot(self.contest)["own"]["rank"], 17)

    def test_no_other_user_fallback_when_self_row_absent(self):
        self.ranking["myRankData"]["uid"] = 99
        self.assertIsNone(self.api.snapshot(self.contest)["own"])

    def test_complete_problem_pagination(self):
        first, second = problems(), problems()
        first["data"], second["data"] = first["data"][:1], second["data"][1:]
        first["basicInfo"]["pageCount"] = second["basicInfo"]["pageCount"] = 2
        second["basicInfo"]["pageCurrent"] = 2
        self.api.get_json.side_effect = [first, second]
        self.assertEqual(len(self.api.problem_list(self.contest)), 2)
        self.assertEqual(self.api.get_json.call_args.kwargs["page"], 2)

    def test_repeated_page_and_missing_problems_fail(self):
        self.ps["basicInfo"]["problemCount"] = 3
        with self.assertRaisesRegex(VPError, "complete"):
            self.api.problem_list(self.contest)
        self.ps = problems()
        self.ps["basicInfo"]["pageCount"] = 2
        with self.assertRaises(VPError):
            self.api.problem_list(self.contest)

    def prepare_submissions(self):
        self.api.apply_metadata(self.contest, self.info)
        self.contest.problems = {"A": "10", "B": "11"}

    def test_own_records_pagination_practice_and_rejudge(self):
        self.prepare_submissions()
        self.api.get_json.side_effect = [status([record()], 1, 2, 2), status([record(78, when=8300)], 2, 2, 2)]
        events = self.api.submissions(self.contest)
        self.assertEqual([e["id"] for e in events], ["77", "78"])
        self.assertEqual(events[0]["time"], 100)
        self.assertTrue(events[1]["practice"])
        self.assertEqual(self.api.get_json.call_args.kwargs["onlyMyStatusFilter"], "true")
        self.api.get_json.side_effect = [status([record(verdict="答案错误")])]
        self.assertEqual(self.api.submissions(self.contest)[0]["verdict"], "WRONG-ANSWER")

    def test_other_user_records_and_incomplete_history_rejected(self):
        self.prepare_submissions()
        self.api.get_json.side_effect = [status([record(uid=99)])]
        with self.assertRaisesRegex(VPError, "complete"):
            self.api.submissions(self.contest)

    def test_pending_hidden_unknown_and_foreign_problem(self):
        self.prepare_submissions()
        rows = [record(77, verdict="正在评测"), record(78, verdict="--"), record(79, verdict="new status"), {**record(80), "problemId": 99}]
        self.api.get_json.side_effect = [status(rows)]
        self.assertEqual([e["verdict"] for e in self.api.submissions(self.contest)], ["PENDING", "HIDDEN", "UNAVAILABLE"])

    def test_guest_empty_list_cannot_confirm_sync_or_finish_history(self):
        self.prepare_submissions()
        guest = status([])
        guest["basicInfo"]["basicUid"] = 0
        self.api.get_json.side_effect = [guest]
        with self.assertRaisesRegex(VPError, "private view"):
            self.api.submissions(self.contest)
        self.assertFalse(self.api._identified)

    def test_weekly_partial_and_real_runtime_error_vocabulary(self):
        self.info = details(4)
        self.prepare_submissions()
        self.api.get_json.side_effect = [status([{**record(verdict="答案错误"), "score": 30, "fullScore": 100},
                                                record(78, verdict="执行出错")])]
        self.assertEqual([e["verdict"] for e in self.api.submissions(self.contest)], ["PARTIAL", "RUN-ERROR"])

    def test_display_has_nowcoder_brand_and_no_hacks(self):
        snap = self.api.snapshot(self.contest)
        out = io.StringIO()
        Console(file=out, width=180).print(LiveDashboard(self.contest, 100, snap, [], "42"))
        self.assertIn("Nowcoder", out.getvalue())
        self.assertNotIn("hacks", out.getvalue())

    def test_verification_error_does_not_retry_or_print_credentials(self):
        api = NowcoderLive(URL, {"cookie_source": "manual"})
        api.web = Mock()
        api.web.get.return_value = Mock(url=ORIGIN + INFO, json=Mock(return_value={"code": 1125, "msg": "验证码不正确"}))
        with self.assertRaisesRegex(VPError, "verification"):
            api.get_json(INFO, id=123)
        api.web.get.assert_called_once()

    def test_expired_json_login_refreshes_firefox_once(self):
        api = NowcoderLive(URL, {"handle": "42"})
        api.identity = Mock(return_value={"uid": 42, "handle": "42"})
        api.web = Mock()
        api.web._refresh_browser.return_value = True
        api.web.get.side_effect = [Mock(url=ORIGIN + INFO, json=Mock(return_value={"code": 1, "msg": "请先登录"})),
                                   Mock(url=ORIGIN + INFO, json=Mock(return_value={"code": 0, "data": details()}))]
        self.assertEqual(api.get_json(INFO, id=123)["contestId"], 123)
        api.web._refresh_browser.assert_called_once_with(force=True)
        self.assertEqual(api.web.get.call_count, 2)


class IsolatedState(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root / "state")}))
        self.path = self.root / "state/vp/live-nowcoder-123-42.json"
        self.api = NowcoderLive(URL, {"uid": 42, "handle": "42", "cookie_source": "manual"})
        self.api._identified = True
        self.contest = Contest("nowcoder", "123", URL, "test", "test", {"A": "10", "B": "11"}, Rules(1))
        self.api.apply_metadata(self.contest, details())
        self.state = {"mode": "live", "url": URL, "handle": "42", "start_time": 1000, "contest": self.contest.to_dict()}
        write_json(self.path, self.state)


class LocalTests(IsolatedState):
    def test_firefox_parent_cookie_scoping_and_uid_verification(self):
        db = sqlite3.connect(self.root / "cookies.sqlite")
        db.execute('CREATE TABLE moz_cookies (host TEXT, name TEXT, value TEXT, path TEXT, expiry INTEGER, originAttributes TEXT, lastAccessed INTEGER)')
        db.executemany('INSERT INTO moz_cookies VALUES (?,?,?,?,?,?,1)', [
            (".nowcoder.com", "t", "synthetic", "/", 9999999999, ""),
            ("other.com", "exclude", "other", "/", 9999999999, ""),
            (".nowcoder.com", "container", "private", "/", 9999999999, "^userContextId=1")])
        db.commit()
        db.close()
        with patch.object(browser_login, "firefox_profiles", return_value=[self.root]):
            candidates = browser_login.browser_cookies("nowcoder", {})
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].cookie, "t=synthetic")
        web = Mock()
        web.session.cookies = requests.cookies.RequestsCookieJar()
        web.session.cookies.set("t", "synthetic", domain=".nowcoder.com", path="/")
        web.session.get.return_value = Mock(url=ORIGIN + "/", text=page())
        with patch("vp_helper.http.WebSession", return_value=web):
            verified = browser_login._verify("nowcoder", candidates[0], "42")
            self.assertEqual((verified["uid"], verified["handle"]), (42, "42"))
            self.assertIsNone(browser_login._verify("nowcoder", candidates[0], "99"))

    def test_resume_preserves_source_and_selects_nowcoder_submit(self):
        from vp_helper import cli
        self.path.unlink()
        native = self.root / "venv/bin/vp-submit"
        native.parent.mkdir(parents=True)
        native.write_text("#!/bin/sh\n")
        native.chmod(0o755)
        script = self.root / "submit"
        script.symlink_to(self.path.parent / "submit")
        self.api.account.update(cookie="t=synthetic", submit_script=str(script))
        self.api.web = Mock()
        self.api.web.session.headers = {"User-Agent": "synthetic"}
        self.api.prepare = Mock(return_value=(self.contest, {}))
        with patch.object(cli, "check_terminal"), patch.object(cli, "load_account", return_value={"nowcoder": self.api.account}), \
                patch("vp_helper.nowcoder.NowcoderLive", return_value=self.api), patch("vp_helper.live.sys.prefix", str(self.root / "venv")), \
                patch("vp_helper.live.desktop", return_value=self.root / "desktop"):
            path, state = live.prepare_live(URL)
            source = Path(state["directory"]) / "A.cpp"
            source.write_text("keep code")
            _, resumed = live.prepare_live(URL)
        self.assertEqual(source.read_text(), "keep code")
        self.assertEqual(script.resolve(), native.resolve())
        self.assertTrue(resumed["history_path"].endswith("nowcoder_live_history.csv"))
        self.assertEqual(read_json(source.parent / "contest.json")["uid"], 42)

    def test_statement_failure_does_not_stop_live(self):
        self.api.save_statements = Mock(side_effect=VPError("unavailable"))
        self.assertIn("skipped", live.fetch_statements(self.api, self.contest.to_dict(), self.root))


class SubmitTests(IsolatedState):
    def setUp(self):
        super().setUp()
        self.source = self.root / "A.cpp"
        self.source.write_text('int main() { /* 测试 & += */ }\n', encoding="utf8")
        self.config = {"platform": "nowcoder", "mode": "live", "base_url": URL, "contest_id": "123",
                       "handle": "42", "uid": 42, "problem_ids": {"A": "10", "B": "11"},
                       "vp_context": {"state_path": str(self.path), "url": URL, "handle": "42", "start_time": 1000}}
        self.api.web = Mock()
        self.api.identity = Mock(return_value={"uid": 42, "handle": "42"})
        self.api.details = Mock(return_value=details())
        self.api.problem_list = Mock(return_value=problems()["data"])
        self.api.web.get.return_value = Mock(url=URL + "/A", text=page())
        self.api.web.session.post.return_value = Mock(status_code=200, json=Mock(return_value={"code": 0, "data": 77}))
        self.stack.enter_context(patch("vp_helper.nowcoder_submit.NowcoderLive", return_value=self.api))
        self.refresh = self.stack.enter_context(patch("vp_helper.nowcoder_submit.refresh_submission_login"))
        self.stack.enter_context(patch("vp_helper.nowcoder_submit.submission_configuration", return_value=(self.root / "contest.json", self.config)))
        self.stack.enter_context(patch("vp_helper.nowcoder_submit.time.time", return_value=1200))

    def test_form_protocol_and_numeric_receipt_match_reference(self):
        self.assertEqual(nowcoder_submit.submit(self.source), "77")
        call = self.api.web.session.post.call_args
        self.api.web.session.post.assert_called_once()
        self.assertEqual(call.args[0], ORIGIN + "/nccommon/submit_cd")
        self.assertEqual(call.kwargs["data"], {"questionId": "1001", "tagId": "4", "subTagId": "1", "doneQuestionId": "123",
                                             "content": self.source.read_text(encoding="utf8"), "language": "2", "languageName": "C++（clang++18）"})
        self.assertIn("application/x-www-form-urlencoded", call.kwargs["headers"]["Content-Type"])
        self.assertFalse(call.kwargs["allow_redirects"])
        self.assertEqual(ReceiptReader(self.path, self.state).read()[0].id, "77")
        self.refresh.assert_called_once_with(self.root / "contest.json", self.config, "nowcoder")

    def test_explicit_gcc_and_python_language(self):
        self.assertEqual(nowcoder_submit.language_id(Path("A.py"), None), "11")
        self.assertEqual(nowcoder_submit.language_id(Path("A.cpp"), "g++"), "38")
        self.assertEqual(nowcoder_submit.submit(self.source, language="38"), "77")
        self.assertEqual(self.api.web.session.post.call_args.kwargs["data"]["languageName"], "C++(g++ 13)")

    def test_timeout_is_not_retried(self):
        self.api.web.session.post.side_effect = _RequestException("timeout")
        with self.assertRaisesRegex(VPError, "outcome unknown"):
            nowcoder_submit.submit(self.source)
        self.api.web.session.post.assert_called_once()

    def test_finished_contest_accepts_practice(self):
        self.api.details.return_value = details(now=9000)
        with patch("vp_helper.nowcoder_submit.time.time", return_value=9000):
            self.assertEqual(nowcoder_submit.submit(self.source), "77")
        self.assertTrue(ReceiptReader(self.path, self.state).read()[0].practice)

    def test_bt_without_registration_preserves_original_submit_fields(self):
        self.config["mode"] = self.state["mode"] = "practice"
        self.state["start_time"] = self.config["vp_context"]["start_time"] = 8500
        write_json(self.path, self.state)
        self.api.details.return_value = {**details(now=9000), "isSignUp": False}
        with patch("vp_helper.nowcoder_submit.time.time", return_value=9000):
            self.assertEqual(nowcoder_submit.submit(self.source), "77")
        self.assertEqual(self.api.web.session.post.call_args.kwargs["data"]["doneQuestionId"], "123")

    def test_captcha_login_and_missing_id_do_not_create_receipt(self):
        for response in ({"code": 1125, "msg": "验证码不正确"}, {"code": 1, "msg": "请先登录"}, {"code": 0, "data": None}):
            self.api.web.session.post.return_value.json.return_value = response
            with self.assertRaises(VPError):
                nowcoder_submit.submit(self.source)
        self.assertEqual(self.api.web.session.post.call_count, 3)
        self.assertEqual(ReceiptReader(self.path, self.state).read(), [])

    def test_changed_page_identity_or_contest_or_special_judge_blocks_post(self):
        for html in (page(99), page().replace("doneQuestionId: '123'", "doneQuestionId: '456'"),
                     page().replace("problemId: '10'", "problemId: '11'"), page().replace("codeJudgeType: '0'", "codeJudgeType: '1'")):
            self.api.web.get.return_value.text = html
            with self.assertRaises(VPError):
                nowcoder_submit.submit(self.source)
        self.api.web.session.post.assert_not_called()

    def test_changed_context_problem_and_before_contest_block_post(self):
        self.config["problem_ids"]["A"] = "99"
        with self.assertRaisesRegex(VPError, "problem list"):
            nowcoder_submit.submit(self.source)
        self.config["problem_ids"]["A"] = "10"
        self.config["vp_context"]["handle"] = "99"
        with self.assertRaisesRegex(VPError, "context changed"):
            nowcoder_submit.submit(self.source)
        self.config["vp_context"]["handle"] = "42"
        self.api.details.return_value = details(now=900)
        with self.assertRaisesRegex(VPError, "not started"):
            nowcoder_submit.submit(self.source)
        self.api.web.session.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
