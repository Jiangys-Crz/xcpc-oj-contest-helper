from contextlib import ExitStack
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from isolation import isolate_local_state

from vp_helper import cli, commands, live
from vp_helper.models import Contest, Rules, VPError
from vp_helper.storage import config_directory, read_json, set_workspace_directory, workspace_directory, write_json


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        isolate_local_state(self.stack, self.root)
        self.config = config_directory()/"config.json"

    def test_unconfigured_output_defaults_to_desktop(self):
        with patch("vp_helper.storage.desktop", return_value=self.root/"Desktop"):
            self.assertEqual(workspace_directory(), self.root/"Desktop")

    def test_setting_creates_folder_preserves_accounts_and_private_permissions(self):
        write_json(self.config, {"qoj":{"handle":"tester","cookie":"synthetic"}})
        target = self.root/"比赛文件 with spaces"
        self.assertEqual(set_workspace_directory(str(target)), target)
        self.assertTrue(target.is_dir())
        self.assertEqual(workspace_directory(), target)
        config = read_json(self.config)
        self.assertEqual(config["qoj"], {"handle":"tester","cookie":"synthetic"})
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)

    def test_file_and_empty_paths_do_not_overwrite_setting(self):
        target = self.root/"output"
        set_workspace_directory(str(target))
        before = self.config.read_bytes()
        bad = self.root/"file"
        bad.write_text("existing file")
        for value in (str(bad), ""):
            with self.assertRaises(VPError):
                set_workspace_directory(value)
        self.assertEqual(before, self.config.read_bytes())

    def test_xcpc_path_supports_interactive_and_direct_forms(self):
        first, second = self.root/"first", self.root/"second"
        with patch("sys.argv", ["xcpc","path",str(first)]):
            commands.main()
        with patch("sys.argv", ["xcpc","path"]), patch("builtins.input", return_value=str(second)):
            commands.main()
        self.assertEqual(workspace_directory(), second)
        before = self.config.read_bytes()
        with patch("sys.argv", ["xcpc","path"]), patch("builtins.input", return_value=""):
            commands.main()
        self.assertEqual(self.config.read_bytes(), before)

    def test_central_help_lists_all_commands_without_loading_accounts(self):
        stream = io.StringIO()
        with patch("sys.argv",["xcpc","--help"]), patch("sys.stdout",stream):
            commands.main()
        for value in ("vp cf ID","vp qoj ID","vp gym ID","live paa ID","live nk ID","bt CONTEST_URL","xcpc path"):
            self.assertIn(value, stream.getvalue())
        self.assertFalse(self.config.exists())

    def test_live_resume_keeps_original_folder_and_csv_after_path_change(self):
        self.stack.enter_context(patch.object(cli,"check_terminal"))
        self.stack.enter_context(patch("time.time",return_value=1200))
        self.stack.enter_context(patch.object(cli,"load_account",return_value={}))
        api = Mock(handle="tester")
        url = "https://codeforces.com/contest/123"
        contest = Contest("cf","123",url,"fixture","CF 123",{"A":"A"},Rules(7200),
                          original_start=1000,extra={"scoring":"CF","phase":"CODING"})
        api.prepare.return_value = (contest,{})
        self.stack.enter_context(patch.object(live,"live_adapter",return_value=api))
        self.stack.enter_context(patch.object(live,"configure_submission",
            side_effect=lambda api,c,d: write_json(d/"contest.json",{"handle":"tester"})))
        first, second = self.root/"first", self.root/"second"
        set_workspace_directory(str(first))
        path, state = live.prepare_live(url)
        set_workspace_directory(str(second))
        resumed_path, resumed = live.prepare_live(url)
        self.assertEqual(path,resumed_path)
        self.assertEqual(Path(state["directory"]).parent,first)
        self.assertEqual(state["directory"],resumed["directory"])
        self.assertEqual(resumed["history_path"],str(first/"cf_live_history.csv"))

    def test_cli_uses_configured_root_for_new_vp_and_bt(self):
        # Entry points share the same root resolver instead of having separate
        # desktop-only defaults for VP, live and practice.
        target = self.root/"chosen"
        set_workspace_directory(str(target))
        self.assertEqual(cli.desktop(),target)
        self.assertEqual(live.desktop(),target)
