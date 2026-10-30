"""Current-console dashboards; no real Windows processes or judge requests."""
from contextlib import ExitStack, nullcontext
import ctypes
import io
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from isolation import isolate_local_state
from vp_helper import cli, storage, windows_terminal as terminal
from vp_helper.models import VPError
from vp_helper.tui import TerminalDashboard


class CurrentTerminalTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        isolate_local_state(self.stack, self.root)
        self.path = self.root/'attempt.json'
        self.directory = self.root/'Contest with spaces'; self.directory.mkdir()
        self.state = {'directory': str(self.directory), 'status': 'scheduled'}
        storage.write_json(self.path, self.state)
        self.stack.enter_context(patch('sys.platform', 'win32'))
        self.stack.enter_context(patch.object(terminal, 'file_lock', side_effect=lambda *a, **k: nullcontext()))
        self.token = self.stack.enter_context(patch.object(terminal, 'process_token', return_value=123))
        self.run = self.stack.enter_context(patch.object(terminal.subprocess, 'run'))
        self.popen = self.stack.enter_context(patch.object(terminal.subprocess, 'Popen'))
        self.monitor = self.stack.enter_context(patch.object(cli, 'monitor'))
        self.shell = self.stack.enter_context(patch.object(terminal, 'command_shell'))
        self.foreground = self.stack.enter_context(patch.object(terminal, 'foreground_terminal', return_value=False))
        self.find = self.stack.enter_context(patch.object(terminal.shutil, 'which', return_value='wt.exe'))
        self.stack.enter_context(patch('sys.stdout', io.StringIO()))

    def assert_closed(self):
        self.assertFalse(self.path.with_suffix('.windows-monitor').exists())
        self.assertFalse(self.path.with_suffix('.windows-reload').exists())
        self.assertNotIn(self.path, terminal._monitors)
        self.popen.assert_not_called()

    def test_current_pane_is_the_monitor_with_only_a_bottom_command_split(self):
        self.foreground.return_value = True
        def observe(path):
            self.assertEqual(Path.cwd(), self.directory)
            stamp = storage.read_json(path.with_suffix('.windows-monitor'))
            self.assertEqual(stamp['pid'], os.getpid())
            self.assertEqual(stamp['layout'], 'current')
        self.monitor.side_effect = observe
        previous = Path.cwd()
        terminal.open_dashboard(self.path, self.state)
        args = self.run.call_args.args[0]
        self.assertEqual(args[:4], ['wt.exe', '-w', '0', 'split-pane'])
        self.assertIn('0.35', args); self.assertIn('vp_helper.windows_terminal', args)
        self.assertNotIn('new-tab', args); self.assertNotIn('vp_helper.cli', args)
        self.assertNotIn('creationflags', self.run.call_args.kwargs)
        self.assertEqual(Path.cwd(), previous)
        self.monitor.assert_called_once_with(self.path); self.shell.assert_not_called()
        self.assert_closed()

    def test_no_wt_session_never_starts_wt_and_uses_the_same_console(self):
        terminal.open_dashboard(self.path, self.state)
        self.run.assert_not_called(); self.find.assert_not_called()
        self.monitor.assert_called_once_with(self.path)
        self.shell.assert_called_once_with(self.path)
        self.assert_closed()

    def test_missing_wt_or_failed_split_keeps_current_console_without_a_retry(self):
        self.foreground.return_value = True
        self.find.return_value = None
        terminal.open_dashboard(self.path, self.state)
        self.run.assert_not_called(); self.assert_closed()
        self.find.return_value = 'wt.exe'
        self.run.side_effect = OSError('split unavailable')
        terminal.open_dashboard(self.path, self.state)
        self.assertEqual(self.run.call_count, 1)
        self.assertEqual(self.monitor.call_count, 2)
        self.assert_closed()

    def test_q_in_an_existing_contest_shell_returns_without_nesting_a_shell(self):
        previous = Path.cwd()
        try:
            os.chdir(self.directory)
            terminal.open_dashboard(self.path, self.state)
        finally:
            os.chdir(previous)
        self.shell.assert_not_called(); self.assert_closed()

    def test_semicolon_paths_are_kept_literal_without_passing_through_wt_parser(self):
        self.foreground.return_value = True
        directory = self.root/'Contest; new-tab'; directory.mkdir()
        terminal.open_dashboard(self.path, self.state | {'directory':str(directory)})
        self.run.assert_not_called(); self.find.assert_not_called()
        self.monitor.assert_called_once_with(self.path)
        self.assert_closed()

    def test_reopening_a_current_monitor_requests_reload_without_killing_or_splitting(self):
        storage.write_json(self.path.with_suffix('.windows-monitor'), {'pid':42,'token':123,'layout':'current'})
        terminal.open_dashboard(self.path, self.state | {'_reload_monitor':True})
        self.assertTrue(storage.read_json(self.path.with_suffix('.windows-reload'))['id'])
        self.token.assert_called_once_with(42)
        self.monitor.assert_not_called(); self.run.assert_not_called(); self.popen.assert_not_called()

    def test_scheduled_monitor_is_registered_before_waiting_and_reloads_in_place(self):
        def first(path):
            self.assertTrue(terminal.monitor_current(path))
            terminal.open_dashboard(path, self.state | {'_reload_monitor':True})
            self.assertFalse(terminal.monitor_current(path))
        def second(path):
            self.assertTrue(terminal.monitor_current(path))
        self.monitor.side_effect = [None, None]
        count = 0
        def observe(path):
            nonlocal count
            count += 1
            (first if count == 1 else second)(path)
        self.monitor.side_effect = observe
        terminal.open_dashboard(self.path, self.state)
        self.assertEqual(count, 2); self.run.assert_not_called()
        self.assertFalse(any(call.kwargs.get('terminate') for call in self.token.call_args_list))
        self.assert_closed()

    def test_only_verified_legacy_monitor_can_be_terminated_during_migration(self):
        storage.write_json(self.path.with_suffix('.windows-monitor'), {'pid':42,'token':123})
        terminal.open_dashboard(self.path, self.state)
        self.assertTrue(any(call.args == (42,) and call.kwargs == {'terminate':123} for call in self.token.call_args_list))
        self.assert_closed()
        self.token.reset_mock(); self.token.side_effect = lambda pid, **kwargs: 456
        storage.write_json(self.path.with_suffix('.windows-monitor'), {'pid':42,'token':123})
        terminal.open_dashboard(self.path, self.state)
        self.assertFalse(any(call.kwargs.get('terminate') for call in self.token.call_args_list))
        self.assert_closed()

    def test_monitor_failure_restores_cwd_and_cleans_registration(self):
        previous = Path.cwd(); self.monitor.side_effect = VPError('synthetic failure')
        with self.assertRaisesRegex(VPError, 'synthetic failure'):
            terminal.open_dashboard(self.path, self.state)
        self.assertEqual(Path.cwd(), previous); self.shell.assert_not_called()
        self.assert_closed()

    def test_all_participation_modes_use_the_same_current_terminal_dispatch(self):
        with patch.object(cli, 'bind_submit_context'), patch('vp_helper.cloud.ensure_access'), \
                patch.object(terminal, 'open_dashboard') as display:
            for mode in ['vp', 'live', 'practice']:
                cli.open_state(self.path, self.state | {'mode':mode})
        self.assertEqual(display.call_count,3)


class ForegroundConsoleTests(unittest.TestCase):
    def test_only_the_console_owner_in_the_foreground_may_be_split(self):
        user = Mock(); user.GetForegroundWindow.return_value = 200
        def class_name(window, buffer, size):
            buffer.value = 'CASCADIA_HOSTING_WINDOW_CLASS'; return len(buffer.value)
        user.GetClassNameW.side_effect = class_name
        def window_owner(window, pointer):
            pointer._obj.value = 5; return 1
        user.GetWindowThreadProcessId.side_effect = window_owner
        with patch('sys.platform','win32'), patch.dict(os.environ,{'WT_SESSION':'synthetic'}), \
                patch.object(ctypes,'windll',SimpleNamespace(user32=user),create=True), \
                patch.object(terminal,'process_parents',return_value={10:9,9:5,5:1}) as parents, \
                patch.object(terminal.os,'getpid',return_value=10):
            self.assertTrue(terminal.foreground_terminal())
            parents.return_value = {10:9,9:1}
            self.assertFalse(terminal.foreground_terminal())
            parents.return_value = {10:9,9:10}  # A malformed/reused PID chain cannot loop.
            self.assertFalse(terminal.foreground_terminal())
            parents.return_value = {10:9,9:5,5:1}
            user.GetClassNameW.side_effect = lambda *args: 0
            self.assertFalse(terminal.foreground_terminal())
        with patch.dict(os.environ,{},clear=True):
            self.assertFalse(terminal.foreground_terminal())

    def test_reload_callback_exits_the_dashboard_before_reading_curses_input(self):
        view = TerminalDashboard(continue_monitor=lambda:False)
        view.screen = Mock(); view.curses = Mock()
        self.assertFalse(view.input()); view.screen.getch.assert_not_called()


if __name__ == '__main__':unittest.main()
