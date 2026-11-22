from _stage_support import source_path, source_library, assemble_layers
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
import components
import ddns
import ddns_config


class DDNSTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.profile = dict(provider='cloudflare', hostname='node.example.com', zone='example.com',
                            login='', secret='fixture-token', server='', family='ipv4', interval=300)
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(components, 'CONFIG', self.root))
        stack.enter_context(patch.object(ddns, 'STATE', self.root / 'ddns'))
        # Keep all temporary credentials inside the fixture instead of host /run.
        temporary = tempfile.TemporaryDirectory
        stack.enter_context(patch.object(ddns.tempfile, 'TemporaryDirectory',
            side_effect=lambda **kwargs: temporary(prefix='ddns-test-', dir=self.root)))

    def test_profiles_and_generated_configuration_reject_injection_and_private_addresses(self):
        for update in [dict(hostname='node.example.com\npostscript=/tmp/evil'), dict(secret='token\nlogin=evil'),
                       dict(provider='exec'), dict(zone='other.com'), dict(interval=5), dict(family='both')]:
            with self.subTest(update=update), self.assertRaises(ValueError): ddns.validate({**self.profile, **update})
        for address in ('192.168.1.2', '100.64.1.2', 'fd00::1', '2606:4700:4700::1111'):
            with self.subTest(address=address), self.assertRaises(ValueError): ddns.render(self.profile, address)
        text = ddns.render(self.profile, '8.8.8.8')
        self.assertIn('usev4=ipv4\nusev6=disabled\nipv4=8.8.8.8', text)
        self.assertIn('ssl=yes', text)
        self.assertIn('login=token', text)
        self.assertIn('ipv6=2606:4700:4700::1111', ddns.render({**self.profile, 'family': 'ipv6'}, '2606:4700:4700::1111'))

    def test_address_discovery_binds_uplink_ignores_proxies_and_does_not_fall_back(self):
        with patch.object(ddns.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout='8.8.8.8\n')) as run:
            self.assertEqual(ddns.discover('ipv4', 'end0'), '8.8.8.8')
            command = run.call_args.args[0]
            self.assertEqual(command[command.index('--interface') + 1], 'end0')
            self.assertIn('--noproxy', command)
            self.assertIn('-4', command)
            run.side_effect = subprocess.CalledProcessError(1, command)
            with self.assertRaises(subprocess.CalledProcessError): ddns.discover('ipv4', 'end0')
            self.assertEqual(run.call_count, 2)
            with self.assertRaises(ValueError): ddns.discover('ipv4', 'wg0')

    def test_periodic_update_uses_private_config_and_persists_sanitized_status(self):
        components.save(self.root / 'ddns.json', json.dumps(self.profile))
        components.save(self.root / 'components.json', json.dumps({'ddns': True}))
        def update(command, **kwargs):
            path = Path(next(arg[6:] for arg in command if arg.startswith('-file=')))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertIn('fixture-token', path.read_text())
            self.assertNotIn('fixture-token', ' '.join(command))
            self.assertTrue(kwargs['capture_output'])
            return subprocess.CompletedProcess(command, 0)
        with patch.object(ddns.network_config, 'uplink', return_value='end0'), \
                patch.object(ddns, 'discover', return_value='8.8.8.8'), \
                patch.object(ddns.subprocess, 'run', side_effect=update) as run:
            ddns.update()
            self.assertEqual(ddns.status()['address'], '8.8.8.8')
            self.assertIsNone(ddns.status()['error'])
            ddns.update()
            run.assert_called_once()
            self.assertEqual((ddns.STATE / 'status.json').stat().st_mode & 0o777, 0o600)
            old_success = ddns.status()['last_success']
            with patch.object(ddns.time, 'time', return_value=old_success + 301):
                run.side_effect = subprocess.CalledProcessError(1, ['ddclient'], stderr='secret fixture-token')
                with self.assertRaises(ValueError): ddns.update()
            self.assertEqual(ddns.status()['last_success'], old_success)
            self.assertNotIn('fixture-token', (ddns.STATE / 'status.json').read_text())

    def test_disabled_profile_does_not_contact_network(self):
        with patch.object(ddns, 'discover') as discover:
            ddns.update()
            discover.assert_not_called()


    def test_generated_profiles_parse_with_real_upstream_ddclient_without_network(self):
        client = os.environ.get('ENODECTL_TEST_DDCLIENT') or shutil.which('ddclient')
        if not client:
            if os.environ.get('NETWORK_EXIT_DDNS_TESTS') == 'required': self.fail('ddclient is required')
            self.skipTest('ddclient is not installed')
        code = 'require $ARGV[0]; require JSON::PP; my (%c,%g); ddclient::read_config($ARGV[1],\\%c,\\%g); print JSON::PP::encode_json(\\%c);'
        for provider in ddns.PROVIDERS:
            for family, address in [('ipv4', '8.8.8.8'), ('ipv6', '2606:4700:4700::1111')]:
                profile = {**self.profile, 'provider': provider, 'family': family,
                           'hostname': 'node.duckdns.org' if provider == 'duckdns' else 'node.example.com',
                           'server': 'dns.example.com', 'login': 'user@example.com',
                           'secret': 'quote"back\\hash#space comma, dollar$apostrophe\''}
                with self.subTest(provider=provider, family=family):
                    path = self.root / 'ddclient.conf'
                    components.save(path, ddns.render(profile, address))
                    parsed = subprocess.run(['perl', '-e', code, str(Path(client).resolve()), str(path)],
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(parsed.returncode, 0, parsed.stderr)
                    entry = json.loads(parsed.stdout)[profile['hostname']]
                    self.assertEqual(entry['password'], profile['secret'])
                    self.assertEqual(entry[family], address)
                    self.assertEqual(entry['protocol'], provider)
                    self.assertEqual(entry['usev4' if family == 'ipv4' else 'usev6'], family)




if __name__ == '__main__': unittest.main()
