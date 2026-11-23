from _stage_support import source_path, source_library, assemble_layers
"""Automatic first-login setup; all privileged commands use fixture backends."""
from contextlib import ExitStack, nullcontext, redirect_stdout, redirect_stderr
import importlib.machinery
import importlib.util
import io
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
LIB = source_path('usr/lib/network-exit')
sys.path.insert(0, str(LIB))
import initialize
import setup_state


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class FirstBootTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.config = self.root / 'config'
        self.config.mkdir(mode=0o700)
        self.public = self.root / 'run/setup-complete'
        self.login = load('login_entry', LIB / 'login-bootstrap')
        self.console = load('console_entry', LIB / 'console-bootstrap')
        stack = ExitStack()
        self.addCleanup(stack.close)
        for target, name, value in [(initialize, 'CONFIG', self.config), (initialize, 'STATE', self.root),
                                    (setup_state, 'RUNTIME_READY', self.public), (self.login, 'READY', self.public),
                                    (self.console, 'READY', self.config / 'initialized')]:
            stack.enter_context(patch.object(target, name, value))
        stack.enter_context(redirect_stdout(io.StringIO()))
        stack.enter_context(redirect_stderr(io.StringIO()))

    def root_setup(self, first_boot=True):
        with patch.object(initialize.os, 'geteuid', return_value=0), \
                patch.object(initialize.os.path, 'ismount', return_value=True), \
                patch.object(initialize, 'initialization_lock', nullcontext):
            initialize.main(first_boot=first_boot)

    def test_private_state_recreates_readable_hint_and_invalidation_removes_both(self):
        setup_state.complete(self.config)
        self.assertEqual(self.public.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.public.parent.stat().st_mode & 0o777, 0o755)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o700)
        self.public.unlink()  # /run is fresh after an ordinary boot or OTA.
        setup_state.publish(self.config)
        self.assertTrue(self.public.is_file())
        setup_state.invalidate(self.config)
        self.assertFalse(self.public.exists())
        self.assertFalse((self.config / 'initialized').exists())

    def test_completed_first_boot_skips_wizard_but_manual_configuration_still_runs(self):
        (self.config / 'initialized').write_text('1\n')
        with patch.object(initialize, 'initialize') as wizard:
            self.root_setup()
            wizard.assert_not_called()
            self.assertTrue(self.public.exists())
            self.root_setup(first_boot=False)
            wizard.assert_called_once_with()

    def test_stale_public_hint_cannot_bypass_private_setup_check(self):
        self.public.parent.mkdir()
        self.public.write_text('1\n')
        with patch.object(initialize, 'initialize', side_effect=lambda: setup_state.complete(self.config)) as wizard:
            self.root_setup()
            wizard.assert_called_once_with()
        self.assertTrue((self.config / 'initialized').exists())

    def test_first_boot_refuses_ephemeral_state(self):
        with patch.object(initialize.os, 'geteuid', return_value=0), \
                patch.object(initialize.os.path, 'ismount', return_value=False), \
                patch.object(initialize, 'initialization_lock', nullcontext), \
                patch.object(initialize, 'initialize') as wizard:
            with self.assertRaisesRegex(ValueError, 'STATE is not mounted'): initialize.main(first_boot=True)
            wizard.assert_not_called()

    def test_pi_interactive_login_runs_exact_bootstrap_command_and_then_stops_repeating(self):
        with patch.object(self.login.os, 'getuid', return_value=1000), \
                patch.object(self.login.sys.stdin, 'isatty', return_value=True), \
                patch.object(self.login.sys.stdout, 'isatty', return_value=True), \
                patch.object(self.login.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as command:
            self.assertEqual(self.login.main(), 0)
            command.assert_called_once_with(['sudo', '--', '/usr/local/sbin/enodectl', 'config', '--first-boot'], check=False)
            setup_state.complete(self.config)
            command.reset_mock()
            self.assertEqual(self.login.main(), 0)
            command.assert_not_called()

    def test_non_pi_and_non_interactive_sessions_do_not_start_setup(self):
        for uid, tty in [(0, True), (1000, False), (1001, True)]:
            with self.subTest(uid=uid, tty=tty), patch.object(self.login.os, 'getuid', return_value=uid), \
                    patch.object(self.login.sys.stdin, 'isatty', return_value=tty), \
                    patch.object(self.login.sys.stdout, 'isatty', return_value=tty), \
                    patch.object(self.login.subprocess, 'run') as command:
                self.assertEqual(self.login.main(), 0)
                command.assert_not_called()

    def test_failed_login_setup_returns_failure_instead_of_a_normal_shell(self):
        with patch.object(self.login.os, 'getuid', return_value=1000), \
                patch.object(self.login.sys.stdin, 'isatty', return_value=True), \
                patch.object(self.login.sys.stdout, 'isatty', return_value=True), \
                patch.object(self.login.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)):
            self.assertEqual(self.login.main(), 1)
            self.assertFalse(self.public.exists())

    def test_console_retries_until_full_setup_instead_of_stopping_after_passwords(self):
        (self.config / 'accounts-ready').write_text('1\n')
        calls = []
        def configure(command, **kwargs):
            calls.append(command)
            if len(calls) == 1: raise subprocess.CalledProcessError(1, command)
            setup_state.complete(self.config)
            return subprocess.CompletedProcess(command, 0)
        with patch.object(self.console.sys.stdin, 'isatty', return_value=True), \
                patch('builtins.input', return_value='') as enter, \
                patch.object(self.console.subprocess, 'run', side_effect=configure):
            self.assertEqual(self.console.main(), 0)
            self.assertEqual(enter.call_count, 2)
            for call in enter.call_args_list:
                self.assertIn('Press `Enter` to begin setup', call.args[0])
            self.assertEqual(calls, [['/usr/local/sbin/enodectl', 'config', '--first-boot']] * 2)

    def test_controller_routes_automatic_setup_and_unified_services(self):
        import service_manager
        controller = load('firstboot_controller', source_path('usr/local/sbin/enodectl'))
        with patch.object(controller.os, 'geteuid', return_value=0), patch.object(initialize, 'main') as wizard:
            self.assertEqual(controller.main(['config', '--first-boot']), 0)
            wizard.assert_called_once_with(first_boot=True)
            wizard.reset_mock()
            self.assertEqual(controller.main(['config']), 0)
            wizard.assert_called_once_with(first_boot=False)
            with patch.object(service_manager, 'open_services') as dashboard:
                self.assertEqual(controller.main(['service']), 0)
                dashboard.assert_called_once_with()
            for argv in (['config', 'ssh'], ['config', 'service'], ['config', 'network'], ['init'], ['addpub']):
                with self.subTest(argv=argv), self.assertRaises(SystemExit) as error:
                    controller.main(argv)
                self.assertEqual(error.exception.code, 2)

    def test_profile_hook_uses_real_terminal_and_stops_shell_after_failure(self):
        tools = self.root / 'tools'
        tools.mkdir()
        (tools / 'id').write_text('#!/bin/sh\nprintf "1000\\n"\n')
        (tools / 'id').chmod(0o755)
        helper = tools / 'login-fixture'
        helper.write_text('#!/bin/sh\necho "$STARTUP_RESULT" >> "$STARTUP_LOG"\nexit "$STARTUP_RESULT"\n')
        helper.chmod(0o755)
        profile = self.root / 'profile.sh'
        profile.write_text((source_path('etc/profile.d/90-enodectl-firstboot.sh')).read_text().replace(
            '/usr/lib/network-exit/login-bootstrap', str(helper)))
        env = {**os.environ, 'PATH': str(tools) + os.pathsep + os.environ['PATH'],
               'STARTUP_LOG': str(self.root / 'calls')}
        for code in [0, 1]:
            master, slave = pty.openpty()
            try:
                result = subprocess.run(['bash', '--noprofile', '--norc', '-i', '-c',
                                         '. "$1"; echo NORMAL_SHELL', 'fixture', str(profile)],
                                        stdin=slave, stdout=slave, stderr=slave,
                                        env={**env, 'STARTUP_RESULT': str(code)}, timeout=5)
                os.close(slave)
                slave = None
                output = b''
                while select.select([master], [], [], 0)[0]:
                    try: chunk = os.read(master, 4096)
                    except OSError: break
                    if not chunk: break
                    output += chunk
                self.assertEqual(result.returncode, code)
                self.assertEqual(b'NORMAL_SHELL' in output, code == 0, output)
            finally:
                os.close(master)
                if slave is not None: os.close(slave)
        self.assertEqual((self.root / 'calls').read_text(), '0\n1\n')
        (self.root / 'calls').unlink()
        subprocess.run(['bash', '--noprofile', '--norc', '-c', '. "$1"', 'fixture', str(profile)],
                       env={**env, 'STARTUP_RESULT': '1'}, check=True)
        self.assertFalse((self.root / 'calls').exists())


if __name__ == '__main__': unittest.main()
