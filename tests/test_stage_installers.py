"""Execute image installers in a disposable native chroot, without host writes."""
from _stage_support import ROOT
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


def copy_command(root, command, target):
    source = Path(shutil.which(command))
    libraries = subprocess.check_output(['ldd', str(source)], text=True)
    paths = {Path(path) for path in re.findall(r'(/[^\s()]+)', libraries)}
    for path in paths:
        destination = root / path.relative_to('/')
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    destination = root / target.lstrip('/')
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


class StageInstallerTests(unittest.TestCase):
    def test_ota_installer_initializes_missing_rauc_directory_and_is_repeatable(self):
        needed = ('unshare', 'ldd', 'bash', 'install', 'ln')
        missing = [name for name in needed if not shutil.which(name)]
        prefix = ['unshare']
        if os.geteuid() != 0: prefix += ['--user', '--map-root-user']
        probe = subprocess.run([*prefix, 'true'], capture_output=True, text=True) if not missing else None
        if missing or probe.returncode:
            reason = str(missing) if missing else probe.stderr
            if os.environ.get('NETWORK_EXIT_OTA_TESTS') == 'required': self.fail(reason)
            self.skipTest(reason)
        with tempfile.TemporaryDirectory(prefix='enodectl-installer-') as directory:
            root = Path(directory)
            for name, target in [('bash', '/bin/bash'), ('install', '/usr/bin/install'), ('ln', '/usr/bin/ln')]:
                copy_command(root, name, target)
            (root / 'etc').mkdir()
            (root / 'etc/armbian-release').write_text('BOARD=orangepizero3\n')
            stage = root / 'stage/ota'
            shutil.copytree(ROOT / 'stage/ota', stage, ignore=shutil.ignore_patterns('__pycache__'))
            (root / 'stage/common.sh').write_text('install_declared_packages() { :; }\n')
            # The mocked package installer must model the files supplied by
            # Debian's rauc-service package. Verify the real installer rejects
            # a missing activation file before testing a complete installation.
            rauc_assets = [root / 'usr/lib/systemd/system/rauc.service',
                           root / 'usr/share/dbus-1/system-services/de.pengutronix.rauc.service',
                           root / 'usr/share/dbus-1/system.d/de.pengutronix.rauc.conf']
            for asset in rauc_assets:
                asset.parent.mkdir(parents=True, exist_ok=True)
                asset.touch()
            service = root / 'usr/bin/systemctl'
            service.write_text('#!/bin/bash\nprintf "%s\\n" "$@" >> /service-actions\n')
            service.chmod(0o755)
            environment = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'ENODECTL_BUILD_CHROOT': '1', 'ENODECTL_STAGE_ROOT': '/stage'}
            self.assertFalse((root / 'etc/rauc').exists())
            command = [*prefix, '--root', str(root), '--wd', '/stage/ota', '/bin/bash', './install.sh']
            rauc_assets[-1].unlink()
            result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=15)
            self.assertNotEqual(result.returncode, 0, 'Installer accepted missing RAUC D-Bus policy')
            self.assertFalse((root / 'service-actions').exists())
            rauc_assets[-1].touch()
            for attempt in range(2):
                result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(os.readlink(root / 'etc/rauc/system.conf'), '/run/rauc/system.conf')
                self.assertEqual((root / 'etc/rauc').stat().st_mode & 0o777, 0o755)
                installed = root / 'usr/share/network-exit/update-source.json'
                self.assertEqual(installed.read_bytes(), (stage / 'config/update-source.json').read_bytes())
                self.assertEqual(installed.stat().st_mode & 0o777, 0o644)
            self.assertEqual((root / 'service-actions').read_text().splitlines(),
                             ['disable', 'rauc.service', 'enable', 'network-exit-confirm.timer', 'network-exit-trial-deadline.timer'] * 2)
