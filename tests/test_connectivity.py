"""Persistent Wi-Fi transactions, early-boot restore and real Netplan parsing."""
from _stage_support import ROOT
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import components
import connectivity
import network_config as network
import network_management

LAYER = ROOT / 'stage/network/rootfs_layer'
ACTIVATE = connectivity.activate


class ConnectivityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / 'var/lib/network-exit'
        self.config = self.state / 'config'
        self.config.mkdir(parents=True)
        self.sysnet = self.root / 'sys/class/net'
        (self.sysnet / 'wlan0/wireless').mkdir(parents=True)
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(components, 'CONFIG', self.config))
        stack.enter_context(patch.object(network, 'STATE', self.state))
        stack.enter_context(patch.object(connectivity, 'SYS_NET', self.sysnet))
        self.wired = self.root / 'etc/netplan/10-dhcp-all-interfaces.yaml'
        self.wired.parent.mkdir(parents=True)
        self.dhcp = self.root / 'usr/share/network-exit/wired-dhcp.yaml'
        self.dhcp.parent.mkdir(parents=True)
        self.dhcp.write_text('dhcp-only-fixture\n')
        self.wired.write_text('initial-static-fixture\n')
        stack.enter_context(patch.object(connectivity, 'WIRED_ACTIVE', self.wired))
        stack.enter_context(patch.object(connectivity, 'WIRED_DHCP', self.dhcp))
        stack.enter_context(patch.object(network.os, 'geteuid', return_value=0))
        stack.enter_context(patch.object(network.os.path, 'ismount', return_value=True))
        self.apply = stack.enter_context(patch.object(network, 'apply'))
        self.activate = stack.enter_context(patch.object(connectivity, 'activate'))
        self.run = stack.enter_context(patch.object(network.subprocess, 'run',
            return_value=subprocess.CompletedProcess([], 0, stdout='', stderr='')))

    def profile(self, **updates):
        return {**connectivity.DEFAULT, 'wifi_enabled': True, 'wifi_ssid': 'Home " Wi-Fi',
                'wifi_password': 'private-fixture-psk', **updates}

    def test_masked_reopen_retains_secret_only_for_same_ssid_and_authentication(self):
        profile = self.profile()
        token = network.begin(dict(network.DEFAULT), link_settings=profile)
        network.confirm(token)
        values = network_management.load()
        self.assertEqual(values['wifi_password'], '')
        self.assertEqual(connectivity.prepare(values), profile)
        for update in ({'wifi_ssid': 'Other Wi-Fi'}, {'wifi_security': 'sae'}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                connectivity.prepare({**values, **update})
        self.assertEqual((connectivity.directory() / 'profile.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(connectivity.directory().stat().st_mode & 0o777, 0o700)
        self.assertNotIn(profile['wifi_password'], '\n'.join(network.diagnostics()))

    def test_invalid_values_and_wrong_radio_rejected_without_command_injection(self):
        for changes in ({'wifi_ssid': 'x\nnetwork:'}, {'wifi_ssid': 'a' * 33},
                        {'wifi_ssid': '\u3042' * 11},
                        {'wifi_enabled': 'yes'}, {'wifi_interface': 'wlan0;reboot'},
                        {'wifi_interface': 'tailscale0'}, {'wifi_password': 'short'},
                        {'wifi_country': 'JP\n'}, {'wifi_security': 'open'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                connectivity.validate(self.profile(**changes))
        with self.assertRaises(ValueError): connectivity.netplan(self.profile(wifi_interface='eth0'))
        (self.sysnet / 'wlan1/wireless').mkdir(parents=True)
        with self.assertRaises(ValueError): connectivity.netplan(self.profile())
        data = json.loads(connectivity.netplan(self.profile(wifi_interface='wlan0')))
        self.assertEqual(list(data['network']['wifis']), ['wlan0'])

    def test_cancel_and_timeout_restore_private_profile_and_generated_netplan(self):
        first = self.profile()
        token = network.begin(dict(network.DEFAULT), link_settings=first)
        network.confirm(token)
        original = (connectivity.directory() / 'netplan.yaml').read_bytes()
        second = self.profile(wifi_ssid='Second', wifi_password='different-fixture-psk')
        network.begin(dict(network.DEFAULT), link_settings=second)
        network.rollback()
        self.assertEqual(connectivity.load(), first)
        self.assertEqual((connectivity.directory() / 'netplan.yaml').read_bytes(), original)
        token = network.begin(dict(network.DEFAULT), link_settings=second)
        journal = json.loads((self.state / 'network-pending.json').read_text())
        with patch.object(network.time, 'monotonic', return_value=journal['deadline']):
            with self.assertRaisesRegex(ValueError, 'expired'): network.confirm(token)
        self.assertEqual(connectivity.load(), first)
        self.assertFalse((connectivity.directory() / 'pending').exists())

    def test_failed_activation_and_firewall_restore_old_settings_and_leave_no_pending_trial(self):
        for failure in ('activate', 'firewall'):
            with self.subTest(failure=failure):
                self.activate.side_effect = [ValueError('activation failed'), None] if failure == 'activate' else None
                self.apply.side_effect = [ValueError('firewall failed'), None] if failure == 'firewall' else None
                with self.assertRaises(ValueError): network.begin(dict(network.DEFAULT), link_settings=self.profile())
                self.assertEqual(connectivity.load(), connectivity.DEFAULT)
                self.assertFalse((self.state / 'network-pending.json').exists())
                self.assertFalse((connectivity.directory() / 'pending').exists())

    def test_unchanged_wifi_does_not_restart_network_and_disabling_keeps_identity(self):
        token = network.begin(dict(network.DEFAULT), link_settings=self.profile())
        network.confirm(token)
        self.activate.reset_mock()
        token = network.begin(dict(network.DEFAULT), link_settings=self.profile())
        network.confirm(token)
        self.activate.assert_not_called()
        profile = self.profile(wifi_enabled=False)
        token = network.begin(dict(network.DEFAULT), link_settings=profile)
        network.confirm(token)
        self.assertEqual(connectivity.load()['wifi_password'], self.profile()['wifi_password'])
        self.assertEqual(json.loads((connectivity.directory() / 'netplan.yaml').read_text())['network']['wifis'], {})

    def test_early_boot_creates_defaults_restores_unconfirmed_and_keeps_committed_wifi(self):
        real_run = subprocess.Popen  # network.subprocess.run is mocked; invoke a private shell directly.
        self.root.joinpath('etc/network-exit').parent.mkdir(parents=True, exist_ok=True)
        self.root.joinpath('etc/network-exit').symlink_to(self.config)
        templates = self.root / 'usr/share/network-exit'
        shutil.copytree(LAYER / 'usr/share/network-exit', templates, dirs_exist_ok=True)
        (templates / 'wired-bootstrap.yaml').write_text('initial-static-fixture\n')
        script = LAYER / 'etc/initramfs-tools/scripts/init-bottom/zzz-enode-connectivity'
        def boot():
            process = real_run(['sh', str(script)], env={**os.environ, 'rootmnt': str(self.root)},
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out, err = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, (out + err).decode())
        boot()
        self.assertEqual(connectivity.load(), connectivity.DEFAULT)
        self.assertEqual(self.wired.read_text(), 'initial-static-fixture\n')
        network.begin(dict(network.DEFAULT), link_settings=self.profile())
        boot()
        self.assertEqual(connectivity.load(), connectivity.DEFAULT)
        network.restore()
        token = network.begin(dict(network.DEFAULT), link_settings=self.profile())
        network.confirm(token)
        # Simulate power loss after the main journal commit and before cleanup.
        components.save(connectivity.directory() / 'pending', '1\n')
        components.save(connectivity.directory() / 'previous-profile.json', json.dumps(connectivity.DEFAULT))
        boot()
        self.assertEqual(connectivity.load(), self.profile())
        self.assertFalse((connectivity.directory() / 'pending').exists())
        components.save(self.config / 'initialized', '1\n')
        boot()
        self.assertEqual(self.wired.read_text(), self.dhcp.read_text())
        (self.config / 'initialized').unlink()
        boot()
        self.assertEqual(self.wired.read_text(), self.dhcp.read_text())

    def test_successful_setup_retires_static_address_and_later_failures_do_not_reopen_it(self):
        connectivity.finish_wired_provisioning()
        self.activate.assert_not_called()
        self.assertEqual(self.wired.read_text(), 'initial-static-fixture\n')
        components.save(self.config / 'initialized', '1\n')
        connectivity.finish_wired_provisioning()
        self.assertEqual(self.wired.read_text(), self.dhcp.read_text())
        self.assertTrue((self.config / 'network-provisioned').exists())
        self.activate.assert_called_once()
        (self.config / 'initialized').unlink()
        connectivity.finish_wired_provisioning()
        self.activate.assert_called_once()
        self.assertTrue((self.config / 'network-provisioned').exists())

    def test_failed_dhcp_activation_retries_without_losing_the_bootstrap_connection(self):
        components.save(self.config / 'initialized', '1\n')
        self.activate.side_effect = ValueError('activation failed')
        with self.assertRaises(ValueError): connectivity.finish_wired_provisioning()
        self.assertEqual(self.wired.read_text(), 'initial-static-fixture\n')
        self.activate.side_effect = None
        connectivity.finish_wired_provisioning()
        self.assertEqual(self.wired.read_text(), self.dhcp.read_text())

    def test_netplan_errors_never_expose_passwords(self):
        # Exercise the real function behind the mock with a captured tool error.
        self.run.side_effect = subprocess.CalledProcessError(1, ['netplan', 'apply'], stderr='secret-fixture-psk')
        with self.assertRaises(ValueError) as error: ACTIVATE()
        self.assertNotIn('secret-fixture-psk', str(error.exception))


class NetplanGenerationTests(unittest.TestCase):
    def test_real_netplan_renders_dhcp_bootstrap_static_wpa2_and_wpa3_in_a_private_root(self):
        generator = os.environ.get('ENODECTL_TEST_NETPLAN_GENERATE')
        command = [generator] if generator else ([shutil.which('netplan'), 'generate'] if shutil.which('netplan') else None)
        if command is None:
            if os.environ.get('NETWORK_EXIT_NETPLAN_TESTS') == 'required': self.fail('Netplan is required')
            self.skipTest('Netplan is not available')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'etc/netplan').mkdir(parents=True)
            (root / 'sys/class/net/wlan0/wireless').mkdir(parents=True)
            shutil.copy2(LAYER / 'etc/netplan/10-dhcp-all-interfaces.yaml', root / 'etc/netplan/10-dhcp-all-interfaces.yaml')
            (root / 'etc/netplan/10-dhcp-all-interfaces.yaml').chmod(0o600)
            for mode in ('psk', 'sae'):
                profile = {**connectivity.DEFAULT, 'wifi_enabled': True, 'wifi_ssid': 'Home \u81ea\u5b85 " # ssid',
                           'wifi_password': 'private-netplan-psk', 'wifi_security': mode, 'wifi_country': 'JP'}
                with patch.object(connectivity, 'SYS_NET', root / 'sys/class/net'):
                    path = root / 'etc/netplan/90-enode-wifi.yaml'
                    path.write_text(connectivity.netplan(profile)); path.chmod(0o600)
                result = subprocess.run([*command, '--root-dir', str(root)], capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, 'Netplan rejected private configuration; stderr suppressed to protect test PSKs')
                wired = (root / 'run/systemd/network/10-netplan-all-eth-interfaces.network').read_text()
                wifi = (root / 'run/systemd/network/10-netplan-wlan0.network').read_text()
                self.assertIn('DHCP=yes', wired)
                self.assertIn('LinkLocalAddressing=ipv6', wired)
                self.assertNotIn('192.168.77.1', wired)
                self.assertIn('RouteMetric=100', wired)
                self.assertIn('RouteMetric=600', wifi)
                wpa = (root / 'run/netplan/wpa-wlan0.conf')
                self.assertEqual(wpa.stat().st_mode & 0o777, 0o600)
                expected = 'WPA-PSK' if mode == 'psk' else 'SAE'
                self.assertIn(expected, wpa.read_text())
                self.assertNotIn(profile['wifi_password'], result.stdout + result.stderr)
            import yaml
            config = yaml.safe_load((root / 'etc/netplan/10-dhcp-all-interfaces.yaml').read_text())
            config['network']['ethernets']['all-eth-interfaces']['addresses'] = ['192.168.77.1/24']
            (root / 'etc/netplan/10-dhcp-all-interfaces.yaml').write_text(json.dumps(config))
            result = subprocess.run([*command, '--root-dir', str(root)], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0)
            wired = (root / 'run/systemd/network/10-netplan-all-eth-interfaces.network').read_text()
            self.assertIn('Address=192.168.77.1/24', wired)
            self.assertIn('DHCP=yes', wired)
