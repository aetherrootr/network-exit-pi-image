from _stage_support import source_path, source_library, assemble_layers
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('controller', str(source_path('usr/local/sbin/enodectl')))
spec = importlib.util.spec_from_loader(loader.name, loader)
controller = importlib.util.module_from_spec(spec)
loader.exec_module(controller)
import bootchain
import bootstate
import update
import power_control


class NodeControllerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.control = self.root/'control'
        with self.control.open('wb') as stream:
            stream.truncate(bootstate.STRIDE*2)
            for bank in (0, 1):
                stream.seek(bank*bootstate.STRIDE)
                stream.write(bootstate.encode(bootstate.initial()))
        (self.root/'platform').write_text(json.dumps({'layout':'ab-v2','boot_control':str(self.control)}))
        (self.root/'release').write_text(json.dumps({'version':'local-test'}))
        (self.root/'cmdline').write_text('rauc.slot=A network_exit.state=1234abcd-04')
        stack = ExitStack()
        self.addCleanup(stack.close)
        for name,value in [('PLATFORM',self.root/'platform'),('RELEASE',self.root/'release'),
                           ('CMDLINE',self.root/'cmdline'),('LOCK',self.root/'operation.lock'),
                           ('RUNTIME',self.root/'run'),('STATE',self.root)]:
            stack.enter_context(patch.object(bootchain,name,value))
            if hasattr(power_control, name): stack.enter_context(patch.object(power_control,name,value))
        stack.enter_context(patch.object(bootchain.os,'geteuid',return_value=0))
        stack.enter_context(patch.object(bootchain,'uptime',return_value=100.0))
        stack.enter_context(patch.object(power_control,'uptime',return_value=100.0))
        stack.enter_context(redirect_stdout(io.StringIO()))

    def test_bootchain_state_transitions_and_fallback(self):
        self.assertEqual(bootchain.status()['state'],'confirmed')
        bootstate.update(self.control,lambda x:{**x,'ne_pending':'B','ne_left':'3'})
        self.assertEqual(bootchain.status()['state'],'pending-reboot')
        (self.root/'cmdline').write_text('rauc.slot=B network_exit.trial=1 network_exit.state=1234abcd-04')
        bootstate.update(self.control,lambda x:{**x,'ne_left':'0'})
        current=bootchain.status()
        self.assertEqual(current['state'],'trial')
        self.assertEqual(current['trial_seconds_remaining'],200)
        (self.root/'cmdline').write_text('rauc.slot=A network_exit.state=1234abcd-04')
        self.assertEqual(bootchain.status()['state'],'rolled-back')
        bootstate.update(self.control,lambda x:{**x,'ne_left':'2'})
        (self.root/'cmdline').write_text('rauc.slot=A network_exit.fallback=1 network_exit.state=1234abcd-04')
        self.assertEqual(bootchain.status()['state'],'fallback')

    def test_slot_switch_stages_trial_without_reboot_and_rejects_invalid_slot(self):
        def activate(command,**kwargs):
            self.assertEqual(command,['rauc','status','mark-active','rootfs.1'])
            bootstate.update(self.control,lambda x:{**x,'ne_pending':'B','ne_left':'3'})
            return subprocess.CompletedProcess(command,0)
        with patch.object(bootchain.subprocess,'run',side_effect=activate) as run, \
                patch.object(bootchain,'reboot_system') as reboot:
            bootchain.switch_slot('B')
            run.assert_called_once()
            reboot.assert_not_called()
        with self.assertRaises(ValueError): bootchain.switch_slot('A')
        bootstate.update(self.control,lambda x:{**x,'ne_B':'0'})
        with patch.object(bootchain.subprocess,'run') as run:
            with self.assertRaises(ValueError): bootchain.switch_slot('B')
            run.assert_not_called()

    def test_staged_switch_is_idempotent_and_failed_trial_requires_explicit_retry(self):
        bootstate.update(self.control,lambda x:{**x,'ne_pending':'B','ne_left':'2'})
        with patch.object(bootchain.subprocess,'run') as run, patch.object(bootchain,'reboot_system') as reboot:
            bootchain.switch_slot('B',reboot=True)
            run.assert_not_called()
            reboot.assert_called_once_with(reason='slot-switch')
        self.assertEqual(bootstate.load(self.control)['ne_left'],'2')
        bootstate.update(self.control,lambda x:{**x,'ne_left':'0'})
        with self.assertRaisesRegex(ValueError,'retry-failed'): bootchain.switch_slot('B')

    def test_controller_reboot_is_logged_and_uses_private_backend(self):
        with patch.object(bootchain.os.path,'ismount',return_value=False), \
                patch.object(bootchain.subprocess,'run') as run:
            bootchain.reboot_system(force=True,reason='trial-deadline')
            run.assert_called_once_with([str(bootchain.REAL_SYSTEMCTL),'reboot','--force'],check=True,timeout=15)
        record=json.loads((self.root/'run/reboot-request.json').read_text())
        self.assertEqual(record['reason'],'trial-deadline')
        self.assertTrue(record['force'])

    def test_cli_status_does_not_check_upstream_or_reboot(self):
        with patch.object(update,'latest') as latest, \
                patch.object(controller.subprocess,'run',return_value=subprocess.CompletedProcess([],0)) as run:
            self.assertEqual(controller.main(['status']),0)
            latest.assert_not_called()
            self.assertTrue(any('status' in call.args[0] for call in run.call_args_list))
        with patch.object(power_control,'reboot_system') as reboot:
            self.assertEqual(controller.main(['reboot','--reason','operator']),0)
            reboot.assert_called_once_with(False,'operator')

    def test_cli_poweroff_is_rejected_without_a_backend_call(self):
        with patch.object(controller.subprocess,'run') as run, redirect_stderr(io.StringIO()) as error:
            with self.assertRaises(SystemExit) as result: controller.main(['poweroff'])
            self.assertEqual(result.exception.code,1)
            self.assertIn('Poweroff is disabled',error.getvalue())
            run.assert_not_called()
