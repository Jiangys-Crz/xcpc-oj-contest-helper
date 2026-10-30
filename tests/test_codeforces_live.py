from __future__ import annotations

from contextlib import ExitStack
from concurrent.futures import Future
import copy
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from isolation import isolate_local_state

from bs4 import BeautifulSoup
from rich.console import Console

from vp_helper import cli, live, live_submit
from vp_helper.codeforces_live import CodeforcesLive, own_row, public_api, submission_records
from vp_helper.http import _RequestException
from vp_helper.receipts import ReceiptReader
from vp_helper.models import Contest, Rules, VPError
from vp_helper.storage import read_history, read_json, write_json
from vp_helper.tui import LiveDashboard

URL = "https://codeforces.com/contest/1234"


def html(body="", handle="tester"):
    return BeautifulSoup(f'<div id="header"><a href="/profile/{handle}">{handle}</a></div>{body}', "html.parser")


def standings(phase="CODING", scoring="CF", points=450, frozen=False):
    return {"contest": {"id": 1234, "name": "Codeforces Round", "type": scoring, "phase": phase,
                        "durationSeconds": 7200, "startTimeSeconds": 1000, "frozen": frozen},
            "problems": [{"index": "A", "name": "Example", "points": 500}, {"index": "B", "points": 1000}],
            "rows": [{"party": {"participantType": "CONTESTANT", "members": [{"handle": "Tester"}]},
                      "rank": 17, "points": points, "penalty": 42, "successfulHackCount": 2, "unsuccessfulHackCount": 1,
                      "problemResults": [{"points": 350, "rejectedAttemptCount": 1, "type": "PRELIMINARY", "bestSubmissionTimeSeconds": 60},
                                         {"points": 0, "rejectedAttemptCount": 2, "type": "FINAL"}]}]}


def contest():
    return Contest("cf", "1234", URL, "Codeforces Round", "CF 1234", {"A": "A", "B": "B"},
                   Rules(7200), original_start=1000, extra={"mode": "live", "scoring": "CF", "phase": "CODING"})


def submission(ident=1, kind="CONTESTANT", handle="tester", verdict="OK", at=1100, testset="PRETESTS"):
    return {"id": ident, "contestId": 1234, "creationTimeSeconds": at, "relativeTimeSeconds": 999,
            "problem": {"index": "A"}, "author": {"participantType": kind, "members": [{"handle": handle}]},
            "verdict": verdict, "testset": testset}


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.api = CodeforcesLive(URL, {"handle": "tester", "cookie_source": "manual", "cookie": "fake=local"})
        self.api.api = Mock(return_value=standings())
        self.api.web = Mock()

    def test_public_standings_exact_parameters_and_no_double_count(self):
        snapshot = self.api.snapshot(contest())
        self.api.api.assert_called_once_with("contest.standings", contestId="1234")
        self.assertEqual(snapshot["own"]["points"], 450)
        self.assertEqual(snapshot["own"]["rank"], 17)
        self.assertEqual(snapshot["stats"], {"scored": {"A": 1, "B": 0}, "attempted": {"A": 1, "B": 1}})
        self.assertEqual(self.api.public.account, {})
        self.assertFalse(self.api.public._auto_refresh)

    def test_prepare_accepts_cf_ioi_icpc_and_uses_official_clock_without_post(self):
        for scoring in ("CF", "IOI", "ICPC"):
            with self.subTest(scoring=scoring):
                data = standings(scoring=scoring)
                self.api.api.side_effect = [[data["contest"]], data]
                self.api.web.soup.return_value = html()
                prepared, snapshot = self.api.prepare()
                self.assertEqual(prepared.original_start, 1000)
                self.assertEqual(prepared.extra["scoring"], scoring)
                self.assertFalse(prepared.pdfs)
                self.assertEqual(snapshot["own"]["rank"], 17)
        self.api.web.post.assert_not_called()
        self.api.web.session.post.assert_not_called()

    def test_wrong_login_rejected_before_api(self):
        self.api.web.soup.return_value = html(handle="someone_else")
        with self.assertRaisesRegex(VPError, "another account"):
            self.api.prepare()
        self.api.api.assert_not_called()

    def test_before_start_registration_without_submission_or_scoreboard(self):
        self.api.api.return_value = [standings(phase="BEFORE")["contest"]]
        self.api.web.soup.side_effect = [html(), html('<table class="registrants"><tr><td><a href="/profile/Tester">Tester</a></td></tr></table>')]
        prepared, snapshot = self.api.prepare()
        self.assertEqual(prepared.extra["phase"], "BEFORE")
        self.assertEqual(snapshot, {})
        self.api.api.assert_called_once_with("contest.list", gym="false")
        self.api.web.post.assert_not_called()

    def test_missing_row_requires_independent_registration_proof(self):
        data = standings()
        data["rows"] = []
        self.api.api.side_effect = [[data["contest"]], data]
        self.api.web.soup.return_value = html()
        with self.assertRaisesRegex(VPError, "existing registration"):
            self.api.prepare()
        self.api.web.post.assert_not_called()

    def test_unsubmitted_registered_user_can_use_live_submit_form(self):
        data = standings()
        data["rows"] = []
        self.api.api.side_effect = [[data["contest"]], data]
        self.api.web.soup.side_effect = [html(), VPError("not exposed"), html('<form class="submit-form"><input name="csrf_token" value="fake"><select name="submittedProblemIndex"></select></form>')]
        prepared, snapshot = self.api.prepare()
        self.assertIsNone(snapshot["own"])
        self.assertEqual(prepared.original_start, 1000)

    def test_virtual_manager_and_other_handles_cannot_become_own_row(self):
        for kind in ("VIRTUAL", "MANAGER", "PRACTICE", "OUT_OF_COMPETITION"):
            rows = standings()["rows"]
            rows[0]["party"]["participantType"] = kind
            self.assertIsNone(own_row(rows, "tester"))
        self.assertIsNone(own_row(standings()["rows"], "other"))

    def test_submission_filter_clock_pretests_and_rejudge(self):
        inputs = [submission(1), submission(2, kind="VIRTUAL"), submission(3, kind="MANAGER"),
                  submission(4, handle="other"), submission(5, kind="PRACTICE"), submission(6, at=999),
                  submission(7, verdict="CHALLENGED"), submission(8, kind="OUT_OF_COMPETITION"),
                  submission(9, kind="PRACTICE", at=8300)]
        self.api.api.return_value = inputs
        events = self.api.submissions(contest())
        self.assertEqual({e["id"] for e in events}, {"1", "7", "8", "9"})
        self.assertEqual(events[0]["time"], 100)
        records = submission_records(events, "CODING")
        self.assertEqual(next(e for e in records if e["id"] == "1")["verdict"], "PRETESTS-PASSED")
        self.assertEqual(next(e for e in records if e["id"] == "7")["verdict"], "CHALLENGED")
        self.assertTrue(next(e for e in records if e["id"] == "9")["practice"])
        self.assertEqual(submission_records(events, "FINISHED")[-1]["verdict"], "CORRECT")

    def test_full_submission_history_is_paginated_and_updates_old_verdicts(self):
        page = [submission(i, verdict="TESTING") for i in range(1, 1001)]
        self.api.api.side_effect = [page, [submission(1001, verdict="CHALLENGED")]]
        events = self.api.submissions(contest())
        self.assertEqual(len(events), 1001)
        self.assertEqual(events[-1]["verdict"], "CHALLENGED")
        self.assertEqual(self.api.api.call_args_list[-1].kwargs["from"], 1001)

    def test_statement_error_does_not_discard_official_snapshot(self):
        self.api.save_statements = Mock(side_effect=VPError("download failed"))
        result, error = live.fetch(self.api, contest().to_dict(), Path("/unused"))
        self.assertEqual(error, "")
        self.assertEqual(result[1]["own"]["rank"], 17)
        self.api.save_statements.assert_not_called()
        self.assertIn("download failed", live.fetch_statements(self.api, contest().to_dict(), Path("/unused")))

    def test_partial_statements_and_local_edits_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "A.html").write_text("local edit")
            self.api.web.soup.return_value = html('<div class="problem-statement">A</div><div class="problem-statement">B</div>')
            self.api.save_statements(contest(), root)
            self.assertEqual((root / "A.html").read_text(), "local edit")
            self.assertIn("B", (root / "B.html").read_text())
            self.api.save_statements(contest(), root)
            self.api.web.soup.assert_called_once()

    def test_invalid_url_rejected(self):
        for url in ("https://codeforces.com/gym/123456", "https://qoj.ac/contest/1234"):
            with self.assertRaises(VPError):
                CodeforcesLive(url, {})

    def test_legacy_vp_pdf_discovery_failure_is_optional_on_both_platforms(self):
        from vp_helper.gym import Gym
        from vp_helper.qoj import QOJ
        title = "2024 ICPC Asia Nanjing Regional Contest"
        gym = Gym("https://codeforces.com/gym/123456", {"handle": "tester"})
        data = standings(phase="FINISHED", scoring="ICPC")
        data["contest"]["name"] = title
        gym.api = Mock(return_value=data)
        gym.web = Mock()
        gym.web.soup.side_effect = [html(), VPError("no attachments"), VPError("problem A unavailable"), VPError("problem B unavailable")]
        self.assertEqual(gym.prepare().pdfs, {})
        qoj = QOJ("https://qoj.ac/contest/1", {"handle": "tester"})
        qoj._page = Mock(side_effect=[html(f'<h1>{title}</h1>Ruleset: ICPC<table><tr><td>A</td>'
                                         '<td><a href="/contest/1/problem/101">A</a></td></tr></table>'),
                                     VPError("problem unavailable")])
        qoj.web = Mock()
        qoj.web.soup.return_value = html('<table><tr><td><a href="/contest/1">Contest</a>5 hours</td></tr></table>')
        self.assertEqual(qoj.prepare().pdfs, {})


class IsolatedState(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        isolate_local_state(self.stack, self.root)
        self.state_root = self.root / "state/vp"
        self.path = self.state_root / "live-cf-1234-tester.json"
        self.state = {"mode": "live", "url": URL, "handle": "tester", "start_time": 1000,
                      "contest": contest().to_dict(), "history_path": str(self.root / "cf_live_history.csv")}


class LocalStateTests(IsolatedState):
    def test_practice_rank_does_not_change_when_official_rank_refreshes(self):
        self.state.update(practice_view=True,snapshot={'own':{'rank':7}})
        write_json(self.path,self.state)
        api=CodeforcesLive(URL,{'handle':'tester'})
        api.api=Mock(return_value=standings(phase='FINISHED'))
        for rank in (99,100):
            snapshot=api.snapshot(contest())
            snapshot['own']['rank']=rank
            live.save_progress(self.path,self.state,contest(),snapshot,{},snapshot['at']+1)
            self.assertEqual(read_json(self.path)['practice_rank'],'7')
            rows=read_history(Path(self.state['history_path']),fields=live.HISTORY_FIELDS)
            self.assertEqual(rows[0]['rank'],'7')

    def test_api_limit_shared_between_calls_and_retries_disabled(self):
        web = Mock()
        web.get.return_value.json.return_value = {"status": "OK", "result": []}
        with patch("vp_helper.codeforces_live.time.time", return_value=2000), patch("vp_helper.codeforces_live.time.sleep") as sleep:
            public_api(web, "contest.standings", contestId=1234)
            public_api(web, "contest.status", contestId=1234)
        self.assertAlmostEqual(sleep.call_args_list[-1].args[0], 2.1)
        self.assertFalse(web.get.call_args.kwargs["retry"])

    def test_live_history_keeps_official_score_and_rejudge_and_separates_accounts(self):
        api = CodeforcesLive(URL, {"handle": "tester"})
        api.api = Mock(return_value=standings(phase="SYSTEM_TEST", points=200))
        snap = api.snapshot(contest())
        live.save_progress(self.path, self.state, contest(), snap, {}, snap["at"] + 1)
        self.assertEqual(read_json(self.path)["status"], "running")
        api.api.return_value = standings(phase="FINISHED", points=-50)
        snap = api.snapshot(contest())
        live.save_progress(self.path, self.state, contest(), snap, {}, snap["at"] - 1)
        self.assertEqual(read_json(self.path)["status"], "running")
        live.save_progress(self.path, self.state, contest(), snap, {}, snap["at"] + 1)
        self.assertEqual(read_json(self.path)["status"], "complete")
        rows = read_history(Path(self.state["history_path"]), fields=live.HISTORY_FIELDS)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["points"], "-50")
        self.assertEqual(rows[0]["rank"], "17")
        self.assertEqual(rows[0]["penalty"], "")
        self.assertFalse((self.root / "vp_history.csv").exists())

    def test_frozen_or_pending_does_not_finalize(self):
        for frozen, verdict in ((True, "CORRECT"), (False, "PENDING")):
            snap = {"phase": "FINISHED", "frozen": frozen, "at": 1, "own": standings()["rows"][0]}
            events = {"1": {"id": "1", "team": "me", "problem": "A", "time": 1, "verdict": verdict}}
            live.save_progress(self.path, self.state, contest(), snap, events, 2)
            self.assertEqual(read_json(self.path)["status"], "running")

    def test_live_entry_command_uses_url(self):
        with patch.object(cli, "start") as start, patch("sys.argv", ["live", URL]):
            live.main()
        start.assert_called_once_with(URL, live=True)

    def test_reconnect_preserves_sources_submissions_and_official_start(self):
        directory = self.root / "contest files"
        directory.mkdir()
        (directory / "A.cpp").write_text("existing code")
        self.state.update(directory=str(directory), status="running", submissions=[{"id": "1"}])
        write_json(self.path, self.state)
        write_json(self.state_root / "current.json", {"state_path": str(self.path)})
        api = Mock(handle="tester")
        api.prepare.return_value = (contest(), {})
        with patch.object(cli, "check_terminal"), patch.object(cli, "load_account", return_value={}) as account, \
                patch("vp_helper.live.CodeforcesLive", return_value=api), patch("vp_helper.live.configure_submission"):
            path, state = live.prepare_live(URL)
        self.assertEqual(path, self.path)
        self.assertEqual(state["start_time"], 1000)
        self.assertEqual(state["submissions"], [{"id": "1"}])
        self.assertEqual((Path(state["directory"]) / "A.cpp").read_text(), "existing code")
        account.assert_called_once_with("cf", live=True)
        api.start.assert_not_called()

    def test_dashboard_uses_official_points_hacks_and_preliminary_mark(self):
        data = standings()
        output = io.StringIO()
        view = LiveDashboard(contest(), 100, {"own": data["rows"][0], "phase": "SYSTEM_TEST", "frozen": True}, [])
        Console(file=output, width=150, color_system=None).print(view)
        rendered = output.getvalue()
        self.assertIn("450", rendered)
        self.assertIn("2/1", rendered)
        self.assertIn("350*", rendered)
        self.assertIn("System testing", rendered)
        self.assertIn("Scoreboard frozen", rendered)
        self.assertNotIn("Gold", rendered)

    def test_installed_submit_dispatch_selects_live_entry_and_keeps_private_config(self):
        native = self.root / "venv/bin/vp-submit"
        native.parent.mkdir(parents=True)
        native.write_text("#!/bin/sh\n")
        native.chmod(0o755)
        self.state_root.mkdir(parents=True)
        script = self.root / "submit"
        script.symlink_to(self.state_root / "submit")
        api = Mock(handle="tester", account={"submit_script": str(script), "cookie": "synthetic=local"})
        api.web.session.headers = {"User-Agent": "local test"}
        directory = self.root / "contest"
        directory.mkdir()
        with patch("vp_helper.live.sys.prefix", str(self.root / "venv")):
            live.configure_submission(api, contest(), directory)
        self.assertEqual(script.resolve(), native.resolve())
        config = read_json(directory / "contest.json")
        self.assertEqual(config["platform"], "cf")
        self.assertEqual(config["mode"], "live")
        self.assertEqual((directory / "contest.json").stat().st_mode & 0o777, 0o600)

    def test_monitor_does_not_download_statements_and_persists_system_test_score_drop(self):
        class ImmediatePool:
            def __init__(self, **kwargs):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def submit(self, fn, *args):
                future = Future()
                future.set_result(fn(*args))
                return future

        directory = self.root / "contest"
        directory.mkdir()
        (directory / "A.cpp").write_text("preserved solution")
        write_json(directory / "contest.json", {})
        self.state.update(directory=str(directory), submissions=[], snapshot={})
        write_json(self.path, self.state)
        write_json(self.root / "config/vp/config.json", {"cf": {"handle": "tester"}})
        account = {"handle": "tester"}
        submission_api = Mock()
        submission_api.submissions.return_value = [{"id": "1", "team": "me", "problem": "A", "time": 100,
                                                   "verdict": "CORRECT", "testset": "PRETESTS"}]
        reference_api = CodeforcesLive(URL, account)
        reference_api.api = Mock(side_effect=[standings(), standings(phase="FINISHED", points=100),
                                              standings(phase="FINISHED", points=100)])
        reference_api.save_statements = Mock(side_effect=VPError("optional download failed"))
        view = Mock()
        view.input.side_effect = [True] * 5 + [False]
        view.__enter__ = Mock(return_value=view)
        view.__exit__ = Mock(return_value=None)
        with patch("vp_helper.live.CodeforcesLive", side_effect=[submission_api, reference_api]), \
                patch("vp_helper.live.ThreadPoolExecutor", ImmediatePool), patch("vp_helper.live.TerminalDashboard", return_value=view), \
                patch("vp_helper.live.time.monotonic", side_effect=[100, 120, 140, 160, 180]), \
                patch("vp_helper.live.time.time", return_value=10000), patch("vp_helper.live.time.sleep"):
            live.monitor_live(self.path)
        saved = read_json(self.path)
        self.assertEqual(saved["snapshot"]["own"]["points"], 100)
        self.assertEqual(saved["status"], "complete")
        self.assertEqual((directory / "A.cpp").read_text(), "preserved solution")
        reference_api.save_statements.assert_not_called()
        rows = read_history(Path(self.state["history_path"]), fields=live.HISTORY_FIELDS)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["points"], "100")


class SubmitTests(IsolatedState):
    def setUp(self):
        super().setUp()
        self.stack.enter_context(patch('builtins.input', return_value='y'))
        self.directory = self.root / "contest"
        self.directory.mkdir()
        self.source = self.directory / "A.cpp"
        self.source.write_text("int main() {}\n")
        self.config = {"platform": "cf", "mode": "live", "base_url": URL, "contest_id": 1234,
                       "handle": "tester", "cookie": "fake=local", "cookie_source": "manual",
                       "problems": ["A", "B"], "vp_context": {
                           "state_path": str(self.path), "start_time": 1000, "url": URL, "handle": "tester"}}
        write_json(self.path, self.state)
        self.config_path = self.directory / "contest.json"
        write_json(self.config_path, self.config)
        self.api = Mock(id="1234", url=URL, handle="tester")
        self.api.api.return_value = standings()
        self.form = '<form class="submit-form" action="/contest/1234/submit"><input name="csrf_token" value="synthetic">' \
                    '<input name="action" value="submitSolutionFormSubmitted"><select name="programTypeId">' \
                    '<option value="54">GNU G++17</option><option value="73">GNU G++20</option></select>' \
                    '<select name="submittedProblemIndex"><option value="A">A</option></select><textarea name="source"></textarea></form>'
        self.api.web.soup.side_effect = [html(), html(self.form)]
        self.api.web.session.post.return_value = Mock(status_code=200, url=URL + "/my", text=str(html(
            '<table><tr data-submission-id="77"><td><a href="/profile/tester">tester</a>'
            '<a href="/contest/1234/problem/A">A</a></td></tr></table>')))
        self.stack.enter_context(patch("vp_helper.live_submit.CodeforcesLive", return_value=self.api))
        self.stack.enter_context(patch("vp_helper.live_submit.submission_configuration", return_value=(self.config_path, self.config)))
        self.stack.enter_context(patch("vp_helper.live_submit.refresh_submission_login"))
        self.stack.enter_context(patch("vp_helper.live_submit.time.time", return_value=1200))

    def test_success_sends_once_uses_source_text_dynamic_compiler_and_receipt(self):
        with patch('sys.stdout', io.StringIO()) as output:
            self.assertEqual(live_submit.submit(self.source), "77")
        self.assertIn('Check judging results here: ' + URL + '/submission/77', output.getvalue())
        self.api.web.session.post.assert_called_once()
        self.assertEqual(self.api.web.session.post.call_args.kwargs["data"]["source"], "int main() {}\n")
        self.assertEqual(self.api.web.session.post.call_args.kwargs["data"]["programTypeId"], "73")
        receipt = read_json(self.path.with_suffix(".receipts") / "77.json")
        self.assertEqual(receipt["verdict"], "PENDING")
        self.assertEqual(receipt["time"], 200)

    def test_declined_preview_never_posts_or_creates_a_receipt(self):
        with patch('builtins.input',return_value='n'), patch('sys.stdout',io.StringIO()) as output:
            self.assertIsNone(live_submit.submit(self.source))
        self.api.web.session.post.assert_not_called()
        self.assertIn('GNU G++20',output.getvalue())
        self.assertIn(URL+'/submit',output.getvalue())
        self.assertFalse(self.path.with_suffix('.receipts').exists())
        self.assertNotIn('Check judging results here:', output.getvalue())

    def test_finished_before_and_system_tests_never_submit_practice(self):
        for phase in ("BEFORE", "PENDING_SYSTEM_TEST", "SYSTEM_TEST", "FINISHED"):
            self.api.api.return_value = standings(phase=phase)
            with self.assertRaisesRegex(VPError, "coding phase"):
                live_submit.submit(self.source)
        self.api.web.session.post.assert_not_called()

    def test_after_end_can_submit_practice_and_receipt_is_blue_classified(self):
        self.api.api.return_value = standings(phase="FINISHED")
        with patch("vp_helper.live_submit.time.time", return_value=9000):
            self.assertEqual(live_submit.submit(self.source), "77")
        self.assertTrue(ReceiptReader(self.path, self.state).read()[0].practice)
        self.api.web.session.post.assert_called_once()

    def test_bt_context_can_have_its_own_start_time(self):
        self.config["mode"] = self.state["mode"] = "practice"
        self.state["start_time"] = self.config["vp_context"]["start_time"] = 8500
        write_json(self.path, self.state)
        self.api.api.return_value = standings(phase="FINISHED")
        with patch("vp_helper.live_submit.time.time", return_value=9000):
            self.assertEqual(live_submit.submit(self.source), "77")
        self.assertTrue(ReceiptReader(self.path, self.state).read()[0].practice)

    def test_context_mismatch_or_outside_state_directory_blocks_post(self):
        for key, value in (("handle", "other"), ("start_time", 1001), ("state_path", str(self.root / "elsewhere.json"))):
            with self.subTest(key=key):
                before = copy.deepcopy(self.config)
                self.config["vp_context"][key] = value
                with self.assertRaises(VPError):
                    live_submit.submit(self.source)
                self.config.clear()
                self.config.update(before)
        self.api.web.session.post.assert_not_called()

    def test_timeout_is_not_retried(self):
        self.api.web.session.post.side_effect = _RequestException("synthetic timeout")
        with patch('sys.stdout', io.StringIO()) as output, self.assertRaisesRegex(VPError, "outcome unknown"):
            live_submit.submit(self.source)
        self.assertIn('Check judging results here: ' + URL + '/my', output.getvalue())
        self.api.web.session.post.assert_called_once()

    def test_wrong_login_and_insecure_form_target_block_post(self):
        for form in (str(html(self.form, handle="other")), str(html(self.form.replace('action="/contest', 'action="http://codeforces.com/contest')))):
            self.api.web.soup.side_effect = [html(), BeautifulSoup(form, "html.parser")]
            with self.assertRaises(VPError):
                live_submit.submit(self.source)
        self.api.web.session.post.assert_not_called()

    def test_unknown_problem_and_compiler_block_post(self):
        with self.assertRaises(VPError):
            live_submit.submit(self.source, "C")
        with self.assertRaises(VPError):
            live_submit.submit(self.source, language="unknown compiler")
        self.api.web.session.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
