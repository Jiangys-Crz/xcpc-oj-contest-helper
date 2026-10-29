from __future__ import annotations

from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from vp_helper import cli
from vp_helper.models import Contest, Rules, VPError
from vp_helper.storage import read_json, write_json


REAL_RUN = subprocess.run
REAL_TMUX_EXISTS = cli.tmux_exists
REAL_LAUNCH = cli.launch
HAVE_TMUX = shutil.which("tmux") is not None


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.directory = self.root / "contest code"
        self.directory.mkdir()
        self.source = self.directory / "A.cpp"
        self.source.write_text("// existing solution\n")
        self.history = self.root / "vp_history.csv"
        self.history.write_text("existing history\n")
        submit = self.root / "submit"
        submit.write_text("#!/bin/sh\nexit 0\n")
        submit.chmod(0o755)
        self.api = Mock(handle="jiangys", account={"submit_script": str(submit)})
        self.api.submit_configuration.return_value = {"platform": "qoj", "contest_id": 4538}
        self.api.prepare.side_effect = AssertionError("Resume must not prepare a new VP")
        self.api.start.side_effect = AssertionError("Resume must not request native participation")
        contest = Contest("qoj", "4538", "https://qoj.ac/contest/4538", "test contest",
                          self.directory.name, {"A": "10000"}, Rules(duration=18000))
        self.state = {"url": contest.url, "contest": contest.to_dict(), "handle": "jiangys",
                      "directory": str(self.directory), "tmux_session": "vp-qoj-4538-1000",
                      "start_time": 1000, "status": "running", "history_path": str(self.history),
                      "submissions": [{"id": "123", "team": "jiangys", "problem": "A",
                                       "time": 60, "verdict": "AC"}],
                      "recent": [{"id": "123", "result": "AC"}],
                      "submission_history": [{"id": "123", "result": "AC"}],
                      "medal_lines": {"gold": "1/1"}}
        self.path = self.root / "vp-qoj-4538-1000.json"
        self.save_state()
        self.stack.enter_context(patch.object(cli.sys, "platform", "linux"))
        self.stack.enter_context(patch.object(cli.sys.stdin, "isatty", return_value=True))
        self.stack.enter_context(patch.object(cli.shutil, "which", return_value="/usr/bin/tmux"))
        self.stack.enter_context(patch.object(cli, "state_directory", return_value=self.root))
        self.stack.enter_context(patch.object(cli.time, "time", return_value=2000))
        self.account = self.stack.enter_context(patch.object(cli, "load_account", return_value={}))
        self.adapter = self.stack.enter_context(patch.object(cli, "adapter", return_value=self.api))
        self.tmux = self.stack.enter_context(patch.object(cli, "tmux_exists", return_value=False))
        self.launch = self.stack.enter_context(patch.object(cli, "launch"))
        self.attach = self.stack.enter_context(patch.object(cli, "attach"))
        self.run = self.stack.enter_context(patch.object(cli.subprocess, "run"))
        self.run.return_value.stdout = "0\n"

    def save_state(self):
        write_json(self.path, self.state)
        write_json(self.root / "current.json", {"state_path": str(self.path)})

    def assert_preserved(self, before):
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.source.read_text(), "// existing solution\n")
        self.assertEqual(self.history.read_text(), "existing history\n")
        self.api.prepare.assert_not_called()
        self.api.start.assert_not_called()

    def test_missing_session_recreates_panel_and_preserves_participation(self):
        before = self.path.read_bytes()
        cli.start(self.state["url"], resume=True)
        self.launch.assert_called_once_with(self.path, self.state)
        self.attach.assert_not_called()
        self.assert_preserved(before)
        config = read_json(self.directory / "contest.json")
        self.assertEqual(config["vp_context"]["start_time"], 1000)
        self.assertEqual(config["vp_context"]["state_path"], str(self.path))

    def test_existing_session_attaches(self):
        self.tmux.return_value = True
        before = self.path.read_bytes()
        cli.start(self.state["url"], resume=True)
        self.attach.assert_called_once_with(self.state["tmux_session"])
        self.launch.assert_not_called()
        self.assert_preserved(before)

    def test_dead_monitor_respawns(self):
        self.tmux.return_value = True
        self.run.return_value.stdout = "1\n"
        cli.start(self.state["url"], resume=True)
        commands = [call.args[0] for call in self.run.call_args_list]
        self.assertTrue(any(command[:2] == ["tmux", "respawn-pane"] for command in commands))
        self.attach.assert_called_once()
        self.api.start.assert_not_called()

    def test_expired_or_complete_participation_is_still_resumed(self):
        for status in ("running", "complete"):
            with self.subTest(status=status):
                self.state.update(start_time=-20000, status=status)
                self.save_state()
                before = self.path.read_bytes()
                self.assertEqual(cli.prepare_resume(self.state["url"]), (self.path, self.state))
                self.assert_preserved(before)

    def test_other_active_contest_does_not_block_an_independent_vp(self):
        before = self.path.read_bytes()
        url = "https://qoj.ac/contest/4539"
        contest = Contest("qoj", "4539", url, "another contest", "another contest", {"A":"10000"}, Rules(18000))
        self.api.prepare.side_effect = None
        self.api.prepare.return_value = contest
        self.api.start.side_effect = None
        self.api.start.return_value = 2000
        with patch.object(cli, "desktop", return_value=self.root):
            path, state = cli.prepare_resume(url)
        self.assertNotEqual(path, self.path)
        self.assertEqual(state["url"], url)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.source.read_text(), "// existing solution\n")

    def test_practice_reopen_refreshes_only_the_score_pane(self):
        self.state["practice_view"] = True
        self.tmux.return_value = True
        cli.open_state(self.path, self.state)
        commands = [call.args[0] for call in self.run.call_args_list]
        refreshed = next(command for command in commands if command[:2] == ["tmux", "respawn-pane"])
        self.assertIn("-k", refreshed)
        self.assertEqual(refreshed[refreshed.index("-t")+1], self.state["tmux_session"]+":0.0")
        self.assertFalse(any(command[:2] == ["tmux", "kill-session"] for command in commands))
        self.api.start.assert_not_called()

    def test_account_mismatch_does_not_modify_or_start_vp(self):
        self.api.handle = "someone_else"
        before = self.path.read_bytes()
        with self.assertRaisesRegex(VPError, "configured account changed"):
            cli.start(self.state["url"], resume=True)
        self.assert_preserved(before)
        self.launch.assert_not_called()
        self.api.submit_configuration.assert_not_called()

    def test_submission_config_failure_keeps_saved_state(self):
        self.api.account["submit_script"] = str(self.root / "missing")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(VPError, "submit script is not installed"):
            cli.start(self.state["url"], resume=True)
        self.assert_preserved(before)
        self.launch.assert_not_called()

    def test_pdf_download_failure_does_not_prevent_starting_contest(self):
        (self.root / "current.json").unlink()
        self.path.unlink()
        contest = Contest.from_dict(self.state["contest"])
        contest.pdfs = {"Statements.pdf": "https://qoj.ac/statement.pdf"}
        self.api.prepare.side_effect = None
        self.api.prepare.return_value = contest
        self.api.start.side_effect = None
        self.api.start.return_value = 2000
        self.api.web.pdf.side_effect = VPError("download failed")
        with patch.object(cli, "desktop", return_value=self.root):
            path, state = cli.prepare_start(self.state["url"])
        self.api.start.assert_called_once()
        self.assertEqual(state["start_time"], 2000)
        self.assertTrue(path.is_file())

    @unittest.skipUnless(HAVE_TMUX, "tmux is required for the integration check")
    def test_real_practice_refresh_preserves_the_lower_shell(self):
        self.state["practice_view"] = True
        self.save_state()
        name = self.state["tmux_session"]
        with patch.dict(os.environ, {"TMUX_TMPDIR": str(self.root), "SHELL": "/bin/bash"}), \
                patch.object(cli.subprocess, "run", REAL_RUN), \
                patch.object(cli, "tmux_exists", REAL_TMUX_EXISTS), \
                patch.object(cli, "launch", REAL_LAUNCH), \
                patch.object(cli.shlex, "join", return_value="sleep 60"):
            try:
                cli.open_state(self.path, self.state)
                before = REAL_RUN(["tmux", "list-panes", "-t", name, "-F", "#{pane_pid}"],
                                  check=True, capture_output=True, text=True).stdout.splitlines()
                cli.open_state(self.path, self.state)
                after = REAL_RUN(["tmux", "list-panes", "-t", name, "-F", "#{pane_pid}"],
                                 check=True, capture_output=True, text=True).stdout.splitlines()
                self.assertNotEqual(before[0], after[0])
                self.assertEqual(before[1], after[1])
                self.assertEqual(self.source.read_text(), "// existing solution\n")
                self.api.start.assert_not_called()
            finally:
                REAL_RUN(["tmux", "kill-server"], capture_output=True)

    @unittest.skipUnless(HAVE_TMUX, "tmux is required for the integration check")
    def test_real_tmux_server_can_be_recreated_without_new_participation(self):
        before = self.path.read_bytes()
        name = self.state["tmux_session"]
        with patch.dict(os.environ, {"TMUX_TMPDIR": str(self.root), "SHELL": "/bin/bash"}), \
                patch.object(cli.subprocess, "run", REAL_RUN), \
                patch.object(cli, "tmux_exists", REAL_TMUX_EXISTS), \
                patch.object(cli, "launch", REAL_LAUNCH), \
                patch.object(cli.shlex, "join", return_value="sleep 60"):
            try:
                for attempt in range(2):
                    with self.subTest(attempt=attempt):
                        self.assertFalse(REAL_TMUX_EXISTS(name))
                        cli.start(self.state["url"], resume=True)
                        self.assertTrue(REAL_TMUX_EXISTS(name))
                        panes = REAL_RUN(["tmux", "list-panes", "-t", name, "-F", "#{pane_dead}"],
                                         check=True, capture_output=True, text=True).stdout.splitlines()
                        self.assertEqual(panes, ["0", "0"])
                        self.assert_preserved(before)
                        REAL_RUN(["tmux", "kill-server"], check=True)
            finally:
                REAL_RUN(["tmux", "kill-server"], capture_output=True)


class RepairScriptTests(unittest.TestCase):
    def test_backup_idempotence_and_unknown_version(self):
        script = Path(__file__).resolve().parents[1] / "tools" / "repair-vp.py"
        spec = importlib.util.spec_from_file_location("repair_vp_resume", script)
        repair = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(repair)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cli.py"
            old = "def example():\n    if True:\n        if True:\n" + repair.OLD + "\n                pass\n"
            path.write_text(old)
            path.chmod(0o640)
            backup = repair.repair(path)
            self.assertEqual(backup.read_text(), old)
            self.assertIn(repair.NEW + "\n", path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            self.assertIsNone(repair.repair(path))
            path.write_text("# unknown recovery code\n")
            with self.assertRaisesRegex(RuntimeError, "Unrecognized"):
                repair.repair(path)
            self.assertEqual(path.read_text(), "# unknown recovery code\n")

    def test_statistics_patch_matches_project_source_and_is_idempotent(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("repair_vp", root / "tools" / "repair-vp.py")
        repair = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(repair)
        fixed = (root / "vp_helper" / "tui.py").read_text()
        legacy = fixed
        for old, new in repair.STATISTICS_REPLACEMENTS:
            self.assertIn(new, legacy)
            legacy = legacy.replace(new, old)
        previous = fixed.replace(repair.STATISTICS_REPLACEMENTS[1][1],
                                 repair.STATISTICS_REPLACEMENTS[1][1].replace(', ratio=1', ''))
        for original in (legacy, previous):
            with self.subTest(previous_layout=original == previous), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "tui.py"
                path.write_text(original)
                backup = repair.repair(path, repair.STATISTICS_REPLACEMENTS)
                self.assertEqual(backup.read_text(), original)
                self.assertEqual(path.read_text(), fixed)
                self.assertIsNone(repair.repair(path, repair.STATISTICS_REPLACEMENTS))


if __name__ == "__main__":
    unittest.main()
