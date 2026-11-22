from _stage_support import source_path, source_library, assemble_layers
import importlib.machinery
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT / 'tests'))
import power_policy
loader = importlib.machinery.SourceFileLoader('power_guard',str(source_path('usr/lib/network-exit/systemctl-guard')))
spec=importlib.util.spec_from_loader(loader.name,loader)
guard=importlib.util.module_from_spec(spec)
loader.exec_module(guard)


class PowerPolicyTests(unittest.TestCase):
    def test_power_requests_cannot_hide_behind_options_or_target_names(self):
        for command in [['reboot'],['--force','--force','poweroff'],['start','reboot.target'],
                        ['isolate','poweroff.target'],['unmask','poweroff.target'],
                        ['--output','cat','reboot'],['--host','status','poweroff'],
                        ['--unknown-option','cat','reboot'],['--','halt'],['-ocat','reboot']]:
            with self.subTest(command=command): self.assertIsNotNone(power_policy.blocked_reason(command))
        for command in [['--no-pager','status','tailscaled.service'],['status','poweroff.target'],
                        ['show','--property=RuntimeWatchdogUSec'],['enable','wg-quick@wg-home'],
                        ['--host','poweroff','status','ssh.service']]:
            with self.subTest(command=command): self.assertIsNone(power_policy.blocked_reason(command))

    def test_systemctl_service_management_delegates_without_running_a_real_backend(self):
        with patch.object(guard.os,'execv') as execute:
            guard.main(['status','ssh.service'])
            execute.assert_called_once_with(str(guard.REAL_SYSTEMCTL),[str(guard.REAL_SYSTEMCTL),'status','ssh.service'])
        with patch.object(guard.os,'execv') as execute:
            self.assertEqual(guard.main(['poweroff']),1)
            execute.assert_not_called()

    def test_legacy_frontends_only_print_controller_instructions(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ['reboot','poweroff','shutdown','halt','telinit']:
                target=Path(tmp)/name
                shutil.copy2(source_path('usr/lib/network-exit/power-command-guard'),target)
                result=subprocess.run([str(target)],capture_output=True,text=True)
                self.assertEqual(result.returncode,1)
                self.assertIn('enodectl reboot',result.stderr)
