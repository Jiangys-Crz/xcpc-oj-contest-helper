from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
import io
import os
import shutil
import sys
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup
from rich.console import Console

from vp_helper import attempt_history, cli, live, practice, submit, vp
from vp_helper.gym import Gym
from vp_helper.models import Contest, Rules, Submission, VPError, StartRejected
from vp_helper.parsing import contest_id_url
from vp_helper.receipts import record_receipt, ReceiptReader
from vp_helper.scheduling import parse_start, prompt_start, registration_time
from vp_helper.scoring import score_submissions, submission_history
from vp_helper.sessions import attempts, boundary, close_attempt, merge_records, merge_submissions, end_attempt, save_state
from vp_helper.storage import read_json, state_directory, write_json
from vp_helper.submit_context import submission_configuration
from vp_helper.tui import Dashboard

URL = "https://qoj.ac/contest/123"


class Isolated(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root/"state"),
                                                       "XDG_CONFIG_HOME": str(self.root/"config"),
                                                       "TZ": "Asia/Shanghai"}))
        self.clock = 1000
        self.stack.enter_context(patch("time.time", side_effect=lambda: self.clock))
        self.stack.enter_context(patch.object(cli, "check_terminal"))
        self.stack.enter_context(patch.object(cli, "desktop", return_value=self.root/"desktop"))
        self.script = self.root/"submit"
        self.script.write_text("#!/bin/sh\n")
        self.script.chmod(0o755)
        self.api = Mock(handle="tester", account={"handle":"tester", "submit_script": str(self.script), "cookie": "synthetic"})
        self.api.native_post_sent = False
        self.api.prepare.side_effect = lambda: Contest("qoj", "123", URL, "test contest", "test contest",
                                                      {"A": "10"}, Rules(18000))
        self.api.submit_configuration.return_value = {"platform": "qoj", "base_url": URL,
                                                      "problems": ["A"], "offset": 10, "cookie": "synthetic"}
        self.api.start.side_effect = lambda c, **kw: self.clock
        self.stack.enter_context(patch.object(cli, "load_account", return_value={"qoj": self.api.account}))
        self.stack.enter_context(patch.object(cli, "adapter", return_value=self.api))

    def legacy_round(self, previous_path, scheduled_start=None):
        """Load the shape of an older version's history without starting another VP."""
        old=read_json(previous_path)
        state=deepcopy(old)
        state.update(start_time=scheduled_start or self.clock, requested_start=scheduled_start or self.clock,
                     created_at=self.clock, attempt=old.get('attempt',1)+1, attempt_id='legacy'+str(int(self.clock)),
                     submissions=[],recent=[],status='scheduled' if scheduled_start else 'running',
                     previous_current={'state_path':str(previous_path)})
        state['contest']['extra'].update(attempt=state['attempt'],attempt_id=state['attempt_id'])
        path=previous_path.with_name('vp-qoj-123-'+state['attempt_id']+'.json')
        state['tmux_session']=path.stem
        write_json(path,state)
        if scheduled_start is None:
            vp._publish_started(path,state,self.api)
        else:
            write_json(path.parent/'current.json',{'state_path':str(path)})
        return path,state


class AttemptTests(Isolated):
    def test_second_vp_is_rejected_without_touching_original_context_or_code(self):
        p1, s1 = cli.prepare_start(URL)
        original = Path(s1["directory"])/"A.cpp"
        original.write_text("saved solution")
        self.clock = 2000
        with self.assertRaisesRegex(VPError,'只支持一次'):
            cli.prepare_start(URL)
        self.assertEqual(original.read_text(), "saved solution")
        self.assertIsNone(boundary(p1))
        self.api.start.assert_called_once()
        self.assertEqual(read_json(original.parent/"contest.json")["vp_context"]["state_path"],str(p1))
        self.assertEqual(len(attempts()),1)

    def test_prior_practice_keeps_verdict_color_and_history_after_restart(self):
        p1, s1 = cli.prepare_start(URL)
        close_attempt(p1, 1500)
        cfg = read_json(Path(s1["directory"])/"contest.json")
        record_receipt(cfg, "77", "A", 1700)
        self.clock = 2000
        p2, s2 = self.legacy_round(p1)
        s1 = read_json(p1)
        event = {"id": "77", "team": "me", "problem": "A", "time": 700, "verdict": "CORRECT"}
        old = merge_records(p1, s1, [event])
        self.assertTrue(old[0]["practice"])
        score = score_submissions([Submission(**e) for e in old], ["A"], Rules(18000), 2000)
        self.assertEqual(score.solved, 0)
        incoming_new = [{**event, "time": 100}]
        self.assertEqual(merge_records(p2, s2, incoming_new), [])
        received = ReceiptReader(p1, s1).read()[0]
        self.assertTrue(received.practice)
        table = Dashboard.records(SimpleNamespace(submissions=old, _record_table=Dashboard._record_table))
        segments = list(Console(color_system="truecolor").render(table))
        self.assertTrue(any(s.style and s.style.color and s.style.color.get_truecolor() == (0, 128, 0) for s in segments))

    def test_new_attempt_excludes_old_server_history_and_accepts_new_unowned_records(self):
        p1, s1 = cli.prepare_start(URL)
        self.clock = 2000
        p2, s2 = self.legacy_round(p1)
        old = Submission("7", "me", "A", 1500, "CORRECT")
        self.assertEqual(merge_submissions(p1, s1, [old]), [])
        new = Submission("8", "me", "A", 10, "CORRECT")
        self.assertEqual(merge_submissions(p2, s2, [new])[0].id, "8")

    def test_scheduled_qoj_waits_and_activates_once_without_manual_start(self):
        path, state = cli.prepare_start(URL, scheduled_start=3000)
        self.assertEqual(state["status"], "scheduled")
        self.api.start.assert_not_called()
        self.clock = 3000
        state = vp.activate(path, state)
        self.assertEqual(state["status"], "running")
        self.assertEqual(state["start_time"], 3000)
        vp.activate(path, state)
        self.api.start.assert_called_once()

    def test_second_reservation_is_rejected_and_original_timer_starts_once(self):
        path, state = cli.prepare_start(URL, scheduled_start=3000)
        self.clock = 2000
        with self.assertRaisesRegex(VPError,'只支持一次'):
            cli.prepare_start(URL)
        self.clock = 3000
        self.assertEqual(vp.activate(path, state)["status"], "running")
        self.api.start.assert_called_once()
        self.assertEqual(read_json(path)["start_time"], 3000)

    def test_start_timeout_only_queries_registration_afterwards(self):
        def timeout(*args, **kwargs):
            self.api.native_post_sent = True
            raise VPError("timeout")
        self.api.start.side_effect = timeout
        self.api._registration.return_value = None
        path, state = cli.prepare_start(URL)
        self.assertTrue(state["activation_uncertain"])
        with self.assertRaisesRegex(VPError, "will not be repeated"):
            vp.activate(path, state)
        self.api.start.assert_called_once()
        self.api._registration.return_value = (1000, 18000)
        recovered = vp.activate(path, state)
        self.assertEqual(recovered["status"], "running")
        self.api.start.assert_called_once()

    def test_timer_worker_invokes_activation_at_deadline(self):
        path, state = cli.prepare_start(URL, scheduled_start=3000)
        updates = []
        class Stop(Exception):
            pass
        outer = self
        class View:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def input(self):
                if updates:
                    outer.clock = 3000
                return True
            def update(self, dashboard): updates.append(dashboard)
        with patch.object(cli, "TerminalDashboard", View), patch("vp_helper.vp.activate", side_effect=Stop) as activate, patch("time.sleep"):
            with self.assertRaises(Stop):
                cli.monitor(path)
        self.assertLess(updates[0].elapsed, 0)
        activate.assert_called_once()

    def test_history_lists_and_queries_both_attempts_with_practice(self):
        p1, s1 = cli.prepare_start(URL)
        close_attempt(p1, 1500)
        record_receipt(read_json(Path(s1["directory"])/"contest.json"), "77", "A", 1700)
        self.clock = 2000
        _, s2 = self.legacy_round(p1)
        s1 = read_json(p1)
        self.clock = 2200
        out = io.StringIO()
        with patch.object(cli, "console", Console(file=out, width=200)):
            attempt_history.history([])
            attempt_history.history([s1["attempt_id"]])
        text = out.getvalue()
        self.assertIn(s1["attempt_id"], text)
        self.assertIn(s2["attempt_id"], text)
        self.assertIn("#77", text)
        self.assertIn("practice", text)

    def test_shared_source_uses_latest_restart_submission_context(self):
        p1, s1 = cli.prepare_start(URL)
        self.clock = 2000
        _, s2 = self.legacy_round(p1)
        s1 = read_json(p1)
        source = Path(s1["directory"])/"A.cpp"
        source.write_text("source")
        with patch("vp_helper.submit_context.Path.cwd", return_value=Path(s2["directory"])):
            _, config = submission_configuration(source)
            self.assertEqual(config["vp_context"]["start_time"], s2["start_time"])


class SchedulingTests(unittest.TestCase):
    def test_prompt_time_and_full_date_and_past_date_validation(self):
        now = datetime.fromisoformat("2026-10-29T19:00:00+08:00").timestamp()
        with patch.dict(os.environ, {"TZ": "Asia/Shanghai"}):
            self.assertEqual(parse_start("", now), now + 180)
            self.assertEqual(parse_start("   ", now), now + 180)
            self.assertEqual(parse_start("now", now), now)
            self.assertEqual(parse_start("立即", now), now)
            self.assertEqual(parse_start("20:00", now)-now, 3600)
            self.assertEqual(parse_start("18:00", now)-now, 23*3600)
            self.assertEqual(parse_start("2026-10-30 19:00", now)-now, 86400)
            for value in ("25:00", "2026-10-29 18:00", "invalid"):
                with self.assertRaises(VPError): parse_start(value, now)

    def test_enter_prompt_schedules_three_minutes_after_input(self):
        with patch('builtins.input', return_value='') as ask, patch('time.time', return_value=1000):
            self.assertEqual(prompt_start(), 1180)
        self.assertIn('回车三分钟后开始', ask.call_args.args[0])

    def test_codeforces_form_explicit_time_and_timezone_are_set_automatically(self):
        when = datetime.fromisoformat("2026-10-29T20:30:00+08:00").timestamp()
        form = BeautifulSoup('<form><input name="startTime" value="Oct/29/2026 12:00"><input name="timezoneOffset" value="-480"></form>', "html.parser")
        payload = {"startTime": "Oct/29/2026 12:00", "timezoneOffset": "-480"}
        registration_time(form, payload, when)
        self.assertEqual(payload["startTime"], "Oct/29/2026 20:30")
        api = Gym("https://codeforces.com/contest/123", {"handle": "tester"})
        api.web = Mock()
        api.web.soup.side_effect = [BeautifulSoup('<div id="header"><a href="/profile/tester">me</a></div>', "html.parser"),
                                   BeautifulSoup('<form id="registerForm"><input name="csrf_token" value="fake"><input name="startTime" value="Oct/29/2026 12:00"><input name="timezoneOffset" value="-480"></form>', "html.parser")]
        api._virtual_party = Mock(side_effect=[None, {"startTimeSeconds": when}])
        self.assertEqual(api.start(Contest("cf","123",api.url,"test","test",{},Rules(7200)), requested_start=when), when)
        self.assertEqual(api.web.post.call_args.args[1]["startTime"], "Oct/29/2026 20:30")
        api.web.post.assert_called_once()


class CommandTests(unittest.TestCase):
    def test_live_aliases(self):
        for alias, ident, url in (("cf", "123", "https://codeforces.com/contest/123"),
                                  ("nk", "123", "https://ac.nowcoder.com/acm/contest/123"),
                                  ("paa", "a"*24, "https://oj.cwnupaa.com/contest/"+"a"*24)):
            with patch("sys.argv", ["live", alias, ident]), patch.object(cli, "start") as start:
                live.main()
                start.assert_called_once_with(url, live=True)

    def test_vp_commands_prompt_and_live_alias_is_removed(self):
        for alias, url in (("cf","https://codeforces.com/contest/123"),("gym","https://codeforces.com/gym/123"),
                           ("qoj", URL)):
            with patch("sys.argv", ["vp", alias, "123"]), patch("vp_helper.scheduling.prompt_start", return_value=3000), patch.object(cli, "start") as start:
                cli.main()
                start.assert_called_once_with(url)
        with patch("sys.argv", ["vp", "live", "cf", "123"]), patch.object(cli, "start") as start:
            with self.assertRaises(SystemExit): cli.main()
            start.assert_not_called()

    def test_bt_entry_accepts_each_platform_url(self):
        for url in (URL, "https://codeforces.com/contest/123", "https://codeforces.com/gym/123",
                    "https://oj.cwnupaa.com/contest/"+"a"*24, "https://ac.nowcoder.com/acm/contest/123"):
            with patch("sys.argv", ["bt", url]), patch.object(cli, "start") as start:
                practice.main()
                start.assert_called_once_with(url, practice=True)


class PracticeTests(Isolated):
    def test_bt_restores_ended_vp_directory_state_and_submission_context(self):
        path, state = cli.prepare_start(URL)
        directory = Path(state["directory"])
        (directory/"A.cpp").write_text("saved solution")
        state["submissions"] = [{"id":"1","team":"me","problem":"A","time":60,"verdict":"CORRECT","practice":False}]
        state["summary"] = {"solved":1,"penalty":1,"rank":"3","prize":"Gold"}
        write_json(path, state)
        self.clock = 20000
        self.api.reset_mock()
        resumed_path, resumed = practice.prepare(URL)
        self.assertEqual(resumed_path, path)
        self.assertEqual(resumed["directory"], state["directory"])
        self.assertEqual(resumed["start_time"], state["start_time"])
        self.assertEqual(resumed["mode"], "vp")
        self.assertEqual(resumed["summary"], state["summary"])
        self.assertEqual(resumed["submissions"], state["submissions"])
        self.assertTrue(resumed["practice_view"])
        self.assertEqual((directory/"A.cpp").read_text(), "saved solution")
        cfg = read_json(directory/"contest.json")
        self.assertEqual(cfg["vp_context"]["state_path"], str(path))
        self.assertEqual(cfg["vp_context"]["start_time"], state["start_time"])
        self.assertEqual(len(attempts()), 1)
        self.assertEqual(read_json(path.parent/"current.json")["state_path"], str(path))
        self.api.prepare.assert_not_called()
        self.api.start.assert_not_called()
        self.api._page.assert_not_called()

    def test_bt_chooses_latest_ended_vp_and_reopens_the_same_attempt(self):
        old_path, old = cli.prepare_start(URL)
        self.clock = 20000
        path, state = self.legacy_round(old_path)
        self.clock = 40000
        for _ in range(2):
            resumed_path, resumed = practice.prepare(URL)
            self.assertEqual(resumed_path, path)
            self.assertEqual(resumed["attempt_id"], state["attempt_id"])
        self.assertEqual(len(attempts()), 2)
        self.assertFalse(read_json(old_path).get("practice_view"))

    def test_bt_does_not_interrupt_active_vp_or_replace_its_files(self):
        path, state = cli.prepare_start(URL)
        before = path.read_bytes()
        config = Path(state["directory"])/"contest.json"
        config_before = config.read_bytes()
        with self.assertRaisesRegex(VPError, "xcpc end"):
            practice.prepare(URL)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(config.read_bytes(), config_before)
        self.assertIsNone(boundary(path))
        self.assertEqual(len(attempts()), 1)

    def test_bt_restores_manually_ended_vp_and_new_records_remain_practice(self):
        path, state = cli.prepare_start(URL)
        self.clock = 1500
        end_attempt()
        stale = read_json(path)
        resumed_path, resumed = practice.prepare(URL)
        self.assertEqual(resumed_path, path)
        self.assertEqual(resumed["status"], "archived")
        self.assertEqual(boundary(path), 1500)
        self.clock = 1600
        record_receipt(read_json(Path(resumed["directory"])/"contest.json"), "77", "A", self.clock)
        stale["submissions"] = [{"id":"77","team":"me","problem":"A","time":600,"verdict":"CORRECT"}]
        save_state(path, stale)
        saved = read_json(path)
        self.assertTrue(saved["practice_view"])
        self.assertTrue(saved["submissions"][0]["practice"])
        self.assertEqual(score_submissions([Submission(**e) for e in saved["submissions"]], ["A"], Rules(18000), 600).solved, 0)

    def test_bt_missing_original_directory_is_recreated_without_new_attempt(self):
        path, state = cli.prepare_start(URL)
        self.clock = 20000
        shutil.rmtree(state['directory'])
        restored,current=practice.prepare(URL)
        self.assertEqual(restored,path)
        self.assertEqual(current['directory'],state['directory'])
        self.assertEqual(current['start_time'],state['start_time'])
        self.assertTrue(Path(state['directory']).is_dir())
        self.assertEqual(read_json(Path(state['directory'])/'contest.json')['vp_context']['state_path'],str(path))
        self.assertEqual(len(attempts()), 1)
        self.api.start.assert_called_once()

    def test_legacy_cf_dispatch_passes_explicit_problem_for_absolute_paths(self):
        source = self.root/"A.cpp"
        source.write_text("source")
        script = self.root/"third_party/cf_submit/submit"
        script.parent.mkdir(parents=True)
        script.write_text("#!/bin/sh\n")
        with patch("sys.argv",["submit",str(source)]), patch("vp_helper.submit.sys.prefix",str(self.root/"venv")), \
                patch("vp_helper.submit.submission_configuration",return_value=(self.root/"contest.json",{"platform":"gym"})), \
                patch("vp_helper.submit.os.chdir"), patch("vp_helper.submit.os.execv") as execute, \
                patch('vp_helper.bootstrap.launch_python') as launch:
            submit.main()
        expected = [str(script), str(source.resolve()), "-p", "A"]
        if sys.platform == 'win32':
            launch.assert_called_once_with(sys.executable,expected)
            execute.assert_not_called()
            return
        if sys.platform == 'darwin':
            expected.insert(0, sys.executable)
        self.assertEqual(execute.call_args.args[1], expected)

    def test_cf_and_gym_practice_prepare_without_live_registration(self):
        import test_codeforces_live as fixtures
        for platform, url in (("cf", "https://codeforces.com/contest/1234"), ("gym", "https://codeforces.com/gym/1234")):
            account = {"handle": "tester", "cookie_source": "manual"}
            client = Mock(account=account, handle="tester")
            client.web.soup.return_value = fixtures.html()
            client.api.return_value = fixtures.standings(phase="FINISHED")
            with patch("vp_helper.practice.CodeforcesLive", return_value=client):
                adapter = practice.Practice(url, {platform: account})
                contest = adapter.prepare()
            self.assertEqual(contest.platform, platform)
            self.assertTrue(contest.extra["practice"])
            client.prepare.assert_not_called()
            client.start.assert_not_called()

    def test_hydro_and_nowcoder_practice_prepare_without_registration(self):
        import test_hydro_live as h
        import test_nowcoder_live as n
        from vp_helper.hydro import HydroLive
        from vp_helper.nowcoder import NowcoderLive
        self.clock = 9000
        for url, client, data in ((h.URL, HydroLive(h.URL, {"handle":"tester","uid":42,"cookie_source":"manual"}), h.details(attend=0)),
                                  (n.URL, NowcoderLive(n.URL, {"handle":"42","uid":42,"cookie_source":"manual"}), {**n.details(now=9000),"isSignUp":False})):
            client.identity = Mock()
            client.details = Mock(return_value=data)
            if isinstance(client, NowcoderLive):
                client.problem_list = Mock()
            with patch("vp_helper.live.live_adapter", return_value=client):
                adapter = practice.Practice(url,{})
                contest = adapter.prepare()
            self.assertTrue(contest.extra["practice"])
            self.assertEqual(contest.original_start, 9000)

    def test_qoj_practice_needs_no_native_vp_and_reopens_its_own_folder(self):
        html = '<ul class="nav-pills"><a href="/user/profile/tester">me</a></ul><h1>Open practice</h1><table><tr><td>A</td><td><a href="/contest/123/problem/10">problem</a></td></tr></table>'
        self.api._page.return_value = BeautifulSoup(html, "html.parser")
        self.api.contest_name.return_value = 'Open practice'
        p1, s1 = practice.prepare(URL)
        self.clock = 3000
        p2, s2 = practice.prepare(URL)
        self.assertEqual(p1, p2)
        self.assertEqual(s1["start_time"], s2["start_time"])
        self.assertEqual(s2["mode"], "practice")
        cfg = read_json(Path(s2["directory"])/"contest.json")
        self.assertEqual(cfg["mode"], "practice")
        self.assertEqual(cfg["problems"], "A")
        self.api.start.assert_not_called()

    def test_practice_even_during_first_second_is_never_scored(self):
        p, state = cli.prepare_start(URL)
        state["mode"] = "practice"
        values = merge_records(p, state, [{"id":"77","team":"me","problem":"A","time":0,"verdict":"CORRECT"}])
        self.assertTrue(values[0]["practice"])
        self.assertEqual(score_submissions([Submission(**e) for e in values],["A"],Rules(18000),10).solved,0)

    def test_dispatch_uses_source_platform_not_last_selected_contest(self):
        source = self.root/"A.cpp"
        source.write_text("source")
        config = {"platform":"nowcoder", "mode":"practice"}
        with patch("sys.argv",["submit",str(source)]), patch("vp_helper.submit.submission_configuration",return_value=(self.root/"contest.json",config)), patch("vp_helper.nowcoder_submit.main") as target:
            submit.main()
            target.assert_called_once()

    def test_dispatch_blocks_source_before_scheduled_start(self):
        p, state = cli.prepare_start(URL, scheduled_start=3000)
        source = Path(state["directory"])/"A.cpp"
        source.write_text("source")
        with patch("sys.argv",["submit",str(source)]), patch("vp_helper.submit.os.execv") as execute:
            with self.assertRaises(SystemExit): submit.main()
            execute.assert_not_called()
