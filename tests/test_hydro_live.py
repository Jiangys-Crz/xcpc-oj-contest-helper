from __future__ import annotations

from contextlib import ExitStack, closing
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup
from rich.console import Console

from vp_helper import browser_login, hydro_submit, live
from vp_helper.hydro import HydroLive, ORIGIN, identity_from_html
from vp_helper.http import _RequestException
from vp_helper.models import Contest, Rules, VPError
from vp_helper.parsing import contest_url
from vp_helper.receipts import ReceiptReader, record_receipt
from vp_helper.storage import read_json, write_json
from vp_helper.tui import LiveDashboard

TID = "a" * 24
URL = ORIGIN + "/contest/" + TID
RID = f"{1100:08x}" + "1" * 16


def page(body="", uid=42, handle="tester"):
    return '<script>window.UserContext = ' + json.dumps(json.dumps({"_id": uid, "uname": handle})) + ';</script>' + body


def details(rule="acm", attend=1):
    return {"tdoc": {"docId": TID, "domainId": "system", "title": "Synthetic CWNUOJ contest", "rule": rule,
                     "pids": [10, 11], "beginAt": "1970-01-01T00:16:40Z", "endAt": "1970-01-01T02:16:40Z"},
            "tsdoc": {"attend": attend}}


def board(rule="acm"):
    return {"rows": [
        [{"type": "rank", "value": "#"}, {"type": "user", "value": "User"},
         {"type": "solved" if rule == "acm" else "total_score", "value": "Total"},
         {"type": "problem", "value": "A", "raw": 10}, {"type": "problem", "value": "B", "raw": 11}],
        [{"type": "rank", "value": "7"}, {"type": "user", "value": "tester", "raw": 42},
         {"type": "time" if rule == "acm" else "total_score", "value": "1\n0:25" if rule == "acm" else "75"},
         {"type": "record", "score": 100 if rule == "acm" else 75, "value": '<span class="icon icon-check"></span>\n0:05' if rule == "acm" else "75"},
         {"type": "record", "score": 0, "value": "-1"}]],
        "pdict": {"10": {"nSubmit": 10, "nAccept": 3}, "11": {"nSubmit": 2, "nAccept": 0}},
        "ratingBoard": {"problems": [{"pid": 10, "submitted": 10, "attempted": 4, "accepted": 3},
                                     {"pid": 11, "submitted": 2, "attempted": 1, "accepted": 0}]}}


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.api = HydroLive(URL, {"handle": "tester", "uid": 42, "cookie_source": "manual"})
        self.api.web = Mock()
        self.api._identified = True
        self.contest = Contest("cwnuoj", TID, URL, "test", "test", {}, Rules(1))
        self.clock = patch("vp_helper.hydro.time.time", return_value=1200)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.api.apply_metadata(self.contest, details())

    def test_links_preserve_domain_and_reject_other_hosts(self):
        self.assertEqual(contest_url(URL), ("cwnuoj", TID, URL))
        self.assertEqual(contest_url(ORIGIN + "/d/system/contest/" + TID)[2], URL)
        custom = ORIGIN + "/d/training/contest/" + TID
        self.assertEqual(HydroLive(custom, {}).domain, "training")
        for invalid in (URL.replace("oj.cwnupaa.com", "oj.cwnupaa.com.evil.example"),
                        ORIGIN + "/d/../contest/" + TID, ORIGIN + "/contest/123", URL.replace("https://", "https://user@")):
            with self.assertRaises(VPError):
                contest_url(invalid)

    def test_identity_uses_own_context_and_never_executes_scripts(self):
        self.assertEqual(identity_from_html(page(uid=42, handle="O'Example")), {"uid": 42, "handle": "O'Example"})
        for value in (page(uid=0), '<a href="/user/42">tester</a>', '<script>window.UserContext = alert(1)</script>'):
            with self.assertRaises(VPError):
                identity_from_html(value)

    def test_prepare_uses_official_start_existing_registration_and_no_post(self):
        self.api.web.get.return_value = Mock(url=ORIGIN, text=page())
        self.api.get_json = Mock(side_effect=[details(), board()])
        contest, snapshot = self.api.prepare()
        self.assertEqual(contest.original_start, 1000)
        self.assertEqual(contest.rules.duration, 7200)
        self.assertEqual(snapshot["own"]["rank"], "7")
        self.assertEqual(snapshot["own"]["penalty"], "0:25")
        self.assertEqual(snapshot["stats"]["attempted"], {"A": 4, "B": 1})
        self.api.web.session.post.assert_not_called()

    def test_wrong_user_and_unregistered_are_rejected(self):
        self.api.web.get.return_value = Mock(url=ORIGIN, text=page(uid=43))
        with self.assertRaisesRegex(VPError, "another account"):
            self.api.identity()
        with self.assertRaisesRegex(VPError, "existing registration"):
            self.api.apply_metadata(self.contest, details(attend=0))
        self.api.web.session.post.assert_not_called()

    def test_acm_oi_ioi_and_cf_keep_server_scores(self):
        for rule, scoring in (("acm", "ICPC"), ("oi", "OI"), ("ioi", "IOI"), ("codeforces", "CF")):
            self.api.get_json = Mock(return_value=board(rule))
            snap = self.api.snapshot(self.contest, details=details(rule))
            self.assertEqual(self.contest.extra["scoring"], scoring)
            self.assertEqual(snap["own"]["points"], 1 if rule == "acm" else 75)

    def test_before_start_hidden_board_and_freeze_are_preserved(self):
        with patch("vp_helper.hydro.time.time", return_value=900):
            self.api.get_json = Mock()
            snap = self.api.snapshot(self.contest, details=details())
            self.api.get_json.assert_not_called()
            self.assertEqual(snap["phase"], "BEFORE")
        data = details()
        data["tdoc"]["lockAt"] = "1970-01-01T00:18:20Z"
        self.api.get_json = Mock(side_effect=VPError("hidden scoreboard"))
        snap = self.api.snapshot(self.contest, details=data)
        self.assertTrue(snap["frozen"])
        self.assertIsNone(snap["own"])
        self.assertIn("unavailable", snap["message"])
        data["tdoc"]["unlocked"] = True
        self.api.apply_metadata(self.contest, data)
        self.assertFalse(self.contest.extra["frozen"])

    def test_unstarted_timed_participation_is_not_started_by_client(self):
        data = details()
        data["tdoc"]["duration"] = 1
        with self.assertRaisesRegex(VPError, "has not started"):
            self.api.apply_metadata(self.contest, data)
        data["tsdoc"]["startAt"] = "1970-01-01T00:20:00Z"
        self.api.apply_metadata(self.contest, data)
        self.assertEqual(self.contest.original_start, 1200)
        self.assertEqual(self.contest.rules.duration, 3600)
        self.api.web.session.post.assert_not_called()

    def test_dynamic_pages_are_filtered_by_exact_uid_and_object_id_clock(self):
        mine = {"_id": RID, "uid": 42, "pid": 10, "domainId": "system", "status": 20, "score": 0}
        other = {**mine, "uid": 99, "_id": f"{1101:08x}" + "2" * 16}
        final = {**mine, "_id": f"{1102:08x}" + "3" * 16, "status": 1}
        self.api.get_json = Mock(side_effect=[
            {"ratingDynamicRecords": True, "rdocs": []},
            {"ratingDynamicRecords": True, "rdocs": [mine, other], "ratingRecordsHasNext": True,
             "ratingRecordsNextUrl": "/record?tid=" + TID + "&page=2"},
            {"ratingDynamicRecords": True, "rdocs": [final], "ratingRecordsHasNext": False}])
        events = self.api.submissions(self.contest)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["time"], 100)
        self.assertEqual(events[0]["verdict"], "PENDING")
        self.assertEqual(events[1]["verdict"], "CORRECT")
        self.assertNotIn("uidOrName", self.api.get_json.call_args_list[1].kwargs)

    def test_repeated_pages_or_cross_contest_pagination_do_not_claim_complete(self):
        record = {"_id": RID, "uid": 42, "pid": 10, "status": 1}
        self.api.get_json = Mock(return_value={"rdocs": [record]})
        with self.assertRaisesRegex(VPError, "repeated"):
            self.api.submissions(self.contest)
        self.api.get_json = Mock(return_value={"ratingDynamicRecords": True, "rdocs": [record],
                                              "ratingRecordsHasNext": True, "ratingRecordsNextUrl": "https://evil.example/record?page=2"})
        with self.assertRaisesRegex(VPError, "invalid next"):
            self.api.submissions(self.contest)

    def test_projection_hides_verdict_instead_of_inventing_failure(self):
        self.api.get_json = Mock(side_effect=[{"rdocs": [{"_id": RID, "uid": 42, "pid": 10}]}, {"rdocs": []}])
        self.assertEqual(self.api.submissions(self.contest)[0]["verdict"], "HIDDEN")

    def test_display_uses_hydro_values_without_hack_columns(self):
        self.api.get_json = Mock(return_value=board("oi"))
        snap = self.api.snapshot(self.contest, details=details("oi"))
        output = io.StringIO()
        Console(file=output, width=140, color_system=None).print(LiveDashboard(self.contest, 100, snap, []))
        self.assertIn("75", output.getvalue())
        self.assertIn("CWNUOJ", output.getvalue())
        self.assertNotIn("hacks", output.getvalue())

    def test_json_login_redirect_is_refreshed_once_without_post(self):
        response = Mock(url=URL)
        response.json.side_effect = [{"url": "/login"}, details()]
        self.api.web.get.return_value = response
        self.api.web._refresh_browser.return_value = True
        self.assertEqual(self.api.get_json(URL)["tdoc"]["docId"], TID)
        self.api.web._refresh_browser.assert_called_once_with(force=True)
        self.api.web.session.post.assert_not_called()
        with self.assertRaises(VPError):
            self.api.get_json("https://other.example/contest/" + TID)


class IsolatedState(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root / "state")}))
        self.path = self.root / "state/vp/live-cwnuoj-system-test-42.json"
        self.api = HydroLive(URL, {"uid": 42, "handle": "tester", "cookie_source": "manual"})
        self.api._identified = True
        self.contest = Contest("cwnuoj", TID, URL, "test", "test", {}, Rules(1))
        self.stack.enter_context(patch("vp_helper.hydro.time.time", return_value=1200))
        self.api.apply_metadata(self.contest, details())
        self.state = {"mode": "live", "url": URL, "handle": "tester", "start_time": 1000, "contest": self.contest.to_dict()}
        write_json(self.path, self.state)


class LocalTests(IsolatedState):
    def test_hex_receipt_round_trip_and_foreign_context_rejection(self):
        config = {"platform": "cwnuoj", "vp_context": {"state_path": str(self.path), "url": URL, "handle": "tester", "start_time": 1000}}
        self.assertTrue(record_receipt(config, RID, "A", 1100))
        self.assertEqual(ReceiptReader(self.path, self.state).read()[0].id, RID)
        with self.assertRaises(VPError):
            record_receipt(config, "../bad", "A", 1100)
        config["vp_context"]["handle"] = "other"
        with self.assertRaises(VPError):
            record_receipt(config, RID, "A", 1100)

    def test_firefox_reads_oj_and_parent_sso_cookies_but_not_other_sites(self):
        profile = self.root / "firefox"
        profile.mkdir()
        with closing(sqlite3.connect(profile / "cookies.sqlite")) as db:
            db.execute("CREATE TABLE moz_cookies (name TEXT, value TEXT, host TEXT, path TEXT, expiry INTEGER, originAttributes TEXT, lastAccessed INTEGER)")
            db.executemany("INSERT INTO moz_cookies VALUES (?, ?, ?, '/', 9999999999, '', 1)",
                           [("sid", "local-fake", "oj.cwnupaa.com"), ("sso", "parent-fake", ".cwnupaa.com"),
                            ("other", "excluded", "codeforces.com")])
            db.commit()
        with patch.object(browser_login, "firefox_profiles", return_value=[profile]):
            candidates = browser_login.browser_cookies("cwnuoj", {})
        self.assertIn("sid=local-fake", candidates[0].cookie)
        self.assertIn("sso=parent-fake", candidates[0].cookie)
        self.assertNotIn("excluded", candidates[0].cookie)

    def test_browser_verification_accepts_both_transport_cookie_containers(self):
        from requests.cookies import RequestsCookieJar
        jar = RequestsCookieJar()
        jar.set("sid", "local-fake", domain="oj.cwnupaa.com", path="/")
        jar.set("sso", "parent-fake", domain=".cwnupaa.com", path="/")
        jar.set("other", "excluded", domain="codeforces.com", path="/")
        for cookies in (jar, SimpleNamespace(jar=jar)):
            web = Mock()
            web.session.cookies = cookies
            web.session.get.return_value = Mock(url=ORIGIN + "/", text=page())
            candidate = browser_login.BrowserCookies(self.root, "sid=local-fake")
            with patch("vp_helper.http.WebSession", return_value=web):
                verified = browser_login._verify("cwnuoj", candidate, "tester")
            self.assertEqual(verified["uid"], 42)
            self.assertIn("sid=local-fake", verified["cookie"])
            self.assertIn("sso=parent-fake", verified["cookie"])
            self.assertNotIn("excluded", verified["cookie"])

    def test_live_preparation_and_resume_select_hydro_entry_without_losing_sources(self):
        from vp_helper import cli
        native = self.root / "venv/bin/vp-submit"
        native.parent.mkdir(parents=True)
        native.write_text("#!/bin/sh\n")
        native.chmod(0o755)
        script = self.root / "submit"
        script.symlink_to(self.path.parent / "submit")
        self.api.account.update(cookie="sid=local-fake", submit_script=str(script))
        self.api.web = Mock()
        self.api.web.session.headers = {"User-Agent": "synthetic"}
        self.api.prepare = Mock(return_value=(self.contest, {}))
        with patch.object(cli, "check_terminal"), patch.object(cli, "load_account", return_value={"cwnuoj": self.api.account}) as load, \
                patch("vp_helper.hydro.HydroLive", return_value=self.api), patch("vp_helper.live.sys.prefix", str(self.root / "venv")), \
                patch("vp_helper.live.desktop", return_value=self.root / "desktop"):
            path, state = live.prepare_live(URL)
            directory = Path(state["directory"])
            (directory / "A.cpp").write_text("preserved solution")
            state["submissions"] = [{"id": RID}]
            write_json(path, state)
            resumed_path, resumed = live.prepare_live(URL)
        self.assertEqual(path, resumed_path)
        self.assertEqual(resumed["submissions"], [{"id": RID}])
        self.assertEqual((directory / "A.cpp").read_text(), "preserved solution")
        self.assertEqual(script.resolve(), native.resolve())
        config = read_json(directory / "contest.json")
        self.assertEqual(config["uid"], 42)
        self.assertEqual(config["problem_ids"], {"A": "10", "B": "11"})
        self.assertEqual(config["vp_context"]["state_path"], str(path))
        self.assertTrue(resumed["history_path"].endswith("cwnuoj_live_history.csv"))
        load.assert_called_with("cwnuoj", live=True)

    def test_hidden_judgement_never_finalizes(self):
        self.state["history_path"] = str(self.root / "history.csv")
        own = {"points": 0, "penalty": "0:00", "rank": "1"}
        live.save_progress(self.path, self.state, self.contest,
                           {"own": own, "phase": "FINISHED", "frozen": False, "at": 1},
                           {RID: {"id": RID, "team": "me", "problem": "A", "time": 1, "verdict": "HIDDEN"}}, 2)
        self.assertEqual(read_json(self.path)["status"], "running")


class SubmitTests(IsolatedState):
    def setUp(self):
        super().setUp()
        self.source = self.root / "A.cpp"
        self.source.write_text("int main() {}\n")
        self.config = {"platform": "cwnuoj", "mode": "live", "base_url": URL, "contest_id": TID,
                       "handle": "tester", "uid": 42, "cookie_source": "manual", "problem_ids": {"A": "10", "B": "11"},
                       "vp_context": {"state_path": str(self.path), "url": URL, "handle": "tester", "start_time": 1000}}
        self.api.web = Mock()
        self.api.identity = Mock(return_value={"uid": 42, "handle": "tester"})
        self.api.details = Mock(return_value=details())
        self.record = {"_id": RID, "uid": 42, "pid": 10, "contest": TID, "domainId": "system"}
        self.api.get_json = Mock(side_effect=[{"langRange": {"cc": "C++17", "cc.cc20o2": "C++20"}}, {"rdoc": self.record}])
        self.form = '<form method="post"><input name="csrf" value="synthetic"><select name="lang"><option value="cc">C++</option></select><textarea name="code"></textarea></form>'
        self.api.web.soup.return_value = BeautifulSoup(page(self.form), "html.parser")
        self.api.web.session.post.return_value = Mock(status_code=200, headers={}, json=Mock(return_value={"rid": RID}))
        self.stack.enter_context(patch("vp_helper.hydro_submit.HydroLive", return_value=self.api))
        self.stack.enter_context(patch("vp_helper.hydro_submit.refresh_submission_login"))
        self.stack.enter_context(patch("vp_helper.hydro_submit.submission_configuration", return_value=(self.root / "contest.json", self.config)))

    def test_single_post_dynamic_language_and_hex_receipt(self):
        self.assertEqual(hydro_submit.submit(self.source), RID)
        self.api.web.session.post.assert_called_once()
        call = self.api.web.session.post.call_args
        self.assertEqual(call.kwargs["data"]["lang"], "cc.cc20o2")
        self.assertEqual(call.kwargs["data"]["code"], "int main() {}\n")
        self.assertEqual(call.kwargs["data"]["tid"], TID)
        self.assertFalse(call.kwargs["allow_redirects"])
        self.assertEqual(ReceiptReader(self.path, self.state).read()[0].id, RID)

    def test_login_redirect_cannot_be_mistaken_for_submission_receipt(self):
        self.api.web.session.post.return_value.json.return_value = {"url": "/login"}
        with self.assertRaisesRegex(VPError, "submission ID"):
            hydro_submit.submit(self.source)
        self.assertFalse(self.path.with_suffix(".receipts").exists())

    def test_timeouts_are_not_retried(self):
        self.api.web.session.post.side_effect = _RequestException("synthetic timeout")
        with self.assertRaisesRegex(VPError, "outcome unknown"):
            hydro_submit.submit(self.source)
        self.api.web.session.post.assert_called_once()

    def test_changed_problem_or_account_or_before_contest_blocks_post(self):
        self.config["problem_ids"]["A"] = "999"
        with self.assertRaisesRegex(VPError, "problem list"):
            hydro_submit.submit(self.source)
        self.config["problem_ids"]["A"] = "10"
        self.config["vp_context"]["handle"] = "other"
        with self.assertRaisesRegex(VPError, "context changed"):
            hydro_submit.submit(self.source)
        self.config["vp_context"]["handle"] = "tester"
        with patch("vp_helper.hydro.time.time", return_value=900):
            with self.assertRaisesRegex(VPError, "not started"):
                hydro_submit.submit(self.source)
        self.api.web.session.post.assert_not_called()

    def test_other_users_record_is_never_notified(self):
        self.record["uid"] = 99
        with self.assertRaisesRegex(VPError, "does not match"):
            hydro_submit.submit(self.source)
        self.assertFalse(self.path.with_suffix(".receipts").exists())

    def test_finished_contest_uses_normal_problem_submission_without_tid(self):
        self.record.pop("contest")
        with patch("vp_helper.hydro.time.time", return_value=9000):
            self.assertEqual(hydro_submit.submit(self.source), RID)
        call = self.api.web.session.post.call_args
        self.assertNotIn("tid", call.kwargs["data"])
        self.assertEqual(call.args[0], ORIGIN + "/p/10/submit")
        self.assertTrue(ReceiptReader(self.path, self.state).read()[0].practice)

    def test_bt_without_registration_uses_normal_problem_form_without_tid(self):
        self.record.pop("contest")
        self.config["mode"] = self.state["mode"] = "practice"
        self.state["start_time"] = self.config["vp_context"]["start_time"] = 8500
        write_json(self.path, self.state)
        self.api.details.return_value = details(attend=0)
        with patch("vp_helper.hydro.time.time", return_value=9000):
            self.assertEqual(hydro_submit.submit(self.source), RID)
        self.assertNotIn("tid", self.api.web.session.post.call_args.kwargs["data"])

    def test_oi_hidden_receipt_does_not_trigger_second_submission(self):
        self.api.web.session.post.return_value.json.return_value = {"tid": TID}
        self.assertIsNone(hydro_submit.submit(self.source))
        self.api.web.session.post.assert_called_once()

    def test_python_correction_uses_problem_endpoint_and_has_no_tid_in_payload(self):
        self.source = self.root / "a.py"
        self.source.write_text("print(0)\n")
        self.record.pop("contest")
        self.api.get_json.side_effect = [{"langRange":{"py.py3":"Python 3"}},{"rdoc":self.record}]
        with patch("vp_helper.hydro.time.time",return_value=9000):
            self.assertEqual(hydro_submit.submit(self.source),RID)
        call=self.api.web.session.post.call_args
        self.assertEqual(call.args[0],ORIGIN+"/p/10/submit")
        self.assertNotIn("tid",call.kwargs["data"])
        self.assertEqual(call.kwargs["data"]["lang"],"py.py3")
        self.assertEqual(ReceiptReader(self.path,self.state).read()[0].problem,"A")

    def test_form_permission_error_does_not_send_code(self):
        self.api.get_json.side_effect=VPError("oj.cwnupaa.com returned HTTP 403")
        with patch("vp_helper.hydro.time.time",return_value=9000),self.assertRaisesRegex(VPError,"补题提交页失败"):
            hydro_submit.submit(self.source)
        self.api.web.session.post.assert_not_called()

    def test_record_permission_error_after_acknowledgement_does_not_repeat_code(self):
        self.api.get_json.side_effect=[{"langRange":{"cc":"C++"}},VPError("HTTP 403")]
        with self.assertRaisesRegex(VPError,"提交已返回编号"):
            hydro_submit.submit(self.source)
        self.api.web.session.post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
