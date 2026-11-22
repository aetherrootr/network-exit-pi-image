from _stage_support import source_path, source_library, assemble_layers
import argparse
import base64
import importlib.machinery
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

MODULE = source_path('usr/local/sbin/network-exit-wg')
loader = importlib.machinery.SourceFileLoader('config', str(MODULE))
spec = importlib.util.spec_from_loader(loader.name, loader)
config = importlib.util.module_from_spec(spec)
loader.exec_module(config)
KEY = base64.b64encode(bytes(range(32))).decode()


class ExitConfigTests(unittest.TestCase):
    def test_input_cannot_inject_shell_or_nft(self):
        for fn, bad in [(config.interface, 'eth0"; drop'), (config.endpoint, 'a;reboot'),
                        (config.key, KEY + '\nPostUp = reboot'), (config.port, '0'),
                        (config.port, '65536'), (config.port, '51820;reboot')]:
            with self.subTest(value=bad), self.assertRaises(argparse.ArgumentTypeError):
                fn(bad)

    def test_exit_bundle_permissions_and_generated_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'key').write_text(KEY)
            out = root / 'home'
            args = argparse.Namespace(output=str(out), private_key=str(root / 'key'),
                                      peer_key=KEY, peer_host='relay.example.com', peer_port=51900,
                                      uplink='end0')
            config.render(args)
            self.assertEqual({path.name for path in out.iterdir()},
                             {'wg-home.conf', 'rules.nft', 'forwarding.conf', 'apply.sh'})
            subprocess.run(['bash', '-n', str(out / 'apply.sh')], check=True)
            subprocess.run(['bash', '-n'], input=config.guard_script(), text=True, check=True)
            self.assertEqual(out.stat().st_mode & 0o777, 0o700)
            self.assertEqual((out / 'wg-home.conf').stat().st_mode & 0o777, 0o600)
            self.assertEqual((out / 'apply.sh').stat().st_mode & 0o777, 0o700)
            with self.assertRaises(ValueError):
                config.render(args)
            tunnel = (out / 'wg-home.conf').read_text()
            self.assertIn('AllowedIPs = 10.77.0.1/32', tunnel)
            self.assertIn('Endpoint = relay.example.com:51900', tunnel)
            self.assertIn('PersistentKeepalive = 25', tunnel)
            self.assertIn('Table = off', tunnel)
            self.assertNotIn('0.0.0.0/0', tunnel)
            self.assertNotIn('net.ipv4.ip_forward=1', (out / 'forwarding.conf').read_text())
            apply = (out / 'apply.sh').read_text()
            self.assertLess(apply.index('systemctl enable --now network-exit-guard.service'),
                            apply.index('/usr/lib/network-exit/network-runtime sync-wg'))

    def test_only_device_configuration_is_available(self):
        for unsupported in ['vps', 'client']:
            result = subprocess.run(['python3', str(MODULE), unsupported], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('invalid choice', result.stderr)


if __name__ == '__main__':
    unittest.main()
