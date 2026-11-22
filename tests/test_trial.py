from _stage_support import source_path, source_library, assemble_layers
from contextlib import ExitStack
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
import bootstate
import trial


class TrialDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.control = self.root / '1234abcd-03'
        with self.control.open('wb') as stream:
            stream.truncate(bootstate.STRIDE * 2)
            for bank in (0, 1):
                stream.seek(bank * bootstate.STRIDE)
                stream.write(bootstate.encode(bootstate.initial()))
        bootstate.update(self.control, lambda state: {**state, 'ne_pending':'B', 'ne_left':'0'})
        (self.root/'cmdline').write_text('rauc.slot=B network_exit.trial=1 network_exit.state=1234abcd-04')
        (self.root/'uptime').write_text('301.0 100.0\n')
        stack = ExitStack()
        self.addCleanup(stack.close)
        for name, value in [('CMDLINE',self.root/'cmdline'), ('UPTIME',self.root/'uptime'),
                            ('LOCK',self.root/'decision.lock'), ('DEVICES',self.root)]:
            stack.enter_context(patch.object(trial,name,value))

    def test_unconfirmed_final_trial_expires_without_state_preparation(self):
        # No STATE mount or runtime platform.json exists in this fixture.
        before = bootstate.load(self.control)
        with self.assertRaisesRegex(ValueError, 'not confirmed'): trial.check_deadline()
        self.assertEqual(bootstate.load(self.control), before)

    def test_confirmed_slot_and_ordinary_boot_do_not_expire(self):
        bootstate.update(self.control, lambda state: {**state, 'ne_pending':'none', 'ne_confirmed':'B'})
        trial.check_deadline()
        # An operator can already have staged the next update to the other slot.
        bootstate.update(self.control, lambda state: {**state, 'ne_pending':'A', 'ne_left':'3'})
        trial.check_deadline()
        (self.root/'cmdline').write_text('rauc.slot=A network_exit.state=1234abcd-04')
        self.control.unlink()
        trial.check_deadline()

    def test_unreadable_control_and_busy_confirmation_fail_without_waiting(self):
        with trial.decision_lock():
            with self.assertRaises(BlockingIOError): trial.check_deadline()
        self.control.unlink()
        with self.assertRaises(OSError): trial.check_deadline()

    def test_backend_accepts_timely_confirmation_and_rejects_late_confirmation(self):
        loader = importlib.machinery.SourceFileLoader('trial_backend', str(source_path('usr/lib/network-exit/boot-backend')))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        backend = importlib.util.module_from_spec(spec)
        loader.exec_module(backend)
        (self.root/'platform').write_text(json.dumps({'boot_control':str(self.control)}))
        with patch.object(backend,'PLATFORM',self.root/'platform'), \
                patch.object(backend,'current_slot',return_value='B'), \
                patch.object(sys,'argv',['backend','set-state','B','good']):
            before = bootstate.load(self.control)
            with self.assertRaisesRegex(ValueError, 'deadline expired'): backend.main()
            self.assertEqual(bootstate.load(self.control), before)
            (self.root/'uptime').write_text('299.0 100.0\n')
            backend.main()
            self.assertEqual(bootstate.load(self.control)['ne_confirmed'], 'B')
            (self.root/'uptime').write_text('302.0 100.0\n')
            trial.check_deadline()

    def test_timer_policy_has_no_network_or_state_startup_dependency(self):
        import configparser
        directory = source_path('etc/systemd/system')
        timer, service = configparser.ConfigParser(), configparser.ConfigParser()
        timer.read(directory/'network-exit-trial-deadline.timer')
        service.read(directory/'network-exit-trial-deadline.service')
        self.assertEqual(timer['Timer']['OnBootSec'], f'{trial.DEADLINE_SECONDS}s')
        for config in (timer, service):
            self.assertEqual(config['Unit']['DefaultDependencies'], 'no')
            self.assertEqual(config['Unit']['ConditionKernelCommandLine'], 'network_exit.trial=1')
            self.assertFalse(any(key in config['Unit'] for key in ['After','Requires','Wants']))
        self.assertEqual(service['Unit']['OnFailure'], 'enodectl-reboot@trial-deadline.service')
        recovery = configparser.ConfigParser(interpolation=None)
        recovery.read(directory/'enodectl-reboot@.service')
        self.assertEqual(recovery['Service']['ExecStart'], '/usr/local/sbin/enodectl reboot --force --reason %i')
        self.assertEqual(service['Service']['TimeoutStartSec'], '15s')
