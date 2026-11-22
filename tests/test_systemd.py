from _stage_support import source_path, source_library, assemble_layers
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import tempfile
import unittest
from enodectl_core import registry

ROOT = Path(__file__).resolve().parents[1]
CONFIG = runpy.run_path(str(source_path('usr/local/sbin/network-exit-wg')))


@unittest.skipUnless(shutil.which('systemd-analyze'), 'systemd-analyze is not installed')
class SystemdUnitTests(unittest.TestCase):
    def test_device_units_with_systemd_parser(self):
        # Parse in an isolated filesystem; never contact the host service manager.
        # Vendor units/executables are fixtures. Full builds check installed units.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assemble_layers(root)
            units = root / 'etc/systemd/system'
            (root / 'etc/os-release').write_text('ID=debian\nVERSION_ID=13\n')
            for name in ['sysinit', 'basic', 'shutdown', 'multi-user', 'network-pre',
                         'network-online', 'nss-lookup', 'wg-quick', 'local-fs']:
                (units / f'{name}.target').write_text(f'[Unit]\nDescription={name} fixture\n')
            (units / 'tailscaled.service').write_text(
                '[Service]\nType=notify\nExecStart=/usr/sbin/tailscaled\n'
                '[Install]\nWantedBy=multi-user.target\n')
            (units / 'wg-quick@.service').write_text(
                '[Unit]\nAfter=network-online.target\nWants=network-online.target\n'
                '[Service]\nType=oneshot\nRemainAfterExit=yes\n'
                'ExecStart=/usr/bin/wg-quick up %i\nExecStop=/usr/bin/wg-quick down %i\n'
                '[Install]\nWantedBy=multi-user.target\n')
            (units / 'nftables.service').write_text('[Service]\nExecStart=/bin/true\n')
            for name in ['getty@.service','serial-getty@.service']:
                (units/name).write_text('[Service]\nExecStart=/bin/true\nStandardInput=tty\nTTYPath=/dev/%I\n')
            for destination, text in re.findall(
                    r"cat > (/etc/systemd/system/[^\n]+) <<'UNIT'\n(.*?)\nUNIT", CONFIG['apply_script'](), re.S):
                target = root / destination.lstrip('/')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text + '\n')
            for name in ['bin/true', 'usr/sbin/tailscaled', 'usr/bin/wg-quick',
                         'usr/local/sbin/network-exit-guard', 'usr/local/sbin/enodectl', 'usr/bin/cloudflared', 'usr/bin/sing-box', 'usr/lib/network-exit/prepare-state', 'usr/lib/network-exit/network-runtime', 'usr/lib/network-exit/confirm-boot', 'usr/lib/network-exit/check-trial-deadline', 'usr/lib/network-exit/console-bootstrap']:
                binary = root / name
                binary.parent.mkdir(parents=True, exist_ok=True)
                binary.write_text('#!/bin/sh\nexit 0\n')
                binary.chmod(0o755)
            result = subprocess.run(['systemd-analyze', f'--root={root}', '--man=no',
                                     '--generators=no', 'verify', 'tailscaled.service',
                                     'network-exit-guard.service', 'wg-quick@wg-home.service', 'network-exit-network.service', 'network-exit-network-rollback.timer', 'network-exit-ddns.timer', 'network-exit-ddns.service', 'network-exit-cloudflared.service', 'network-exit-singbox.service', 'network-exit-confirm.service', 'network-exit-trial-deadline.timer', 'network-exit-trial-deadline.service', 'getty@tty1.service', 'serial-getty@ttyS0.service',
                                     *[unit for stage in registry.stages() for unit in stage.get('systemd_units', [])]],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
