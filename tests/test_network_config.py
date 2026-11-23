from _stage_support import source_path, source_library, assemble_layers
from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
import components
import network_config as network
import network_management
import bootstate
import trial
UPLINK = network.uplink


class NetworkPolicyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name)
        self.config = self.state / 'config'
        self.config.mkdir()
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(components, 'CONFIG', self.config))
        stack.enter_context(patch.object(network, 'STATE', self.state))
        stack.enter_context(patch.object(network.os, 'geteuid', return_value=0))
        stack.enter_context(patch.object(network.os.path, 'ismount', return_value=True))
        self.device = stack.enter_context(patch.object(network, 'uplink', return_value='end0'))
        stack.enter_context(patch.object(network.connectivity, 'management_interfaces', return_value=['end0']))
        self.command = stack.enter_context(patch.object(network.subprocess, 'run',
            return_value=subprocess.CompletedProcess([], 1, stdout='')))

    def settings(self, **kwargs): return {**network.DEFAULT, **kwargs}

    def test_management_ssh_dhcp_and_mdns_remain_available_on_wired_and_wifi(self):
        rules = network.host_rules(self.settings(firewall=True), 'wlan0', {}, ['end0', 'wlan0'])
        for link in ('end0', 'wlan0'):
            self.assertIn(f'iifname "{link}" ip saddr 192.168.0.0/16 tcp dport 22 accept', rules)
            self.assertIn(f'iifname "{link}" udp sport 67 udp dport 68 accept', rules)
            self.assertIn(f'iifname "{link}" ip ttl 255 udp dport 5353 accept', rules)
        self.assertIn('host-forward-deny', rules)

    def test_auto_uplink_follows_live_route_before_old_wireguard_hint(self):
        (self.config / 'rules.nft').write_text('iifname "wg-home" oifname "end0" accept\n')
        with patch.object(network.subprocess, 'check_output', side_effect=[
            json.dumps([{'dev': 'wlan0', 'metric': 600}]), json.dumps([{'ifname': 'wlan0'}])]):
            self.assertEqual(UPLINK(self.settings()), 'wlan0')
        with patch.object(network.subprocess, 'check_output', side_effect=[
            json.dumps([{'dev': 'wlan0', 'metric': 600}, {'dev': 'end0', 'metric': 100}]),
            json.dumps([{'ifname': 'end0'}])]):
            self.assertEqual(UPLINK(self.settings()), 'end0')

    def test_route_refresh_runs_only_after_an_actual_uplink_change_and_outside_trials(self):
        components.save(self.config / 'network.json', json.dumps(self.settings(firewall=True)))
        components.save(self.config / 'active-uplink', 'end0\n')
        with patch.object(network, 'apply') as apply:
            network.refresh_uplink()
            apply.assert_not_called()
            self.device.return_value = 'wlan0'
            network.refresh_uplink()
            apply.assert_called_once()
            apply.reset_mock()
            (self.state / 'network-pending.json').write_text('{}')
            network.refresh_uplink()
            apply.assert_not_called()

    def test_rejects_injection_invalid_networks_and_unrestricted_ssh_port(self):
        for updates in [dict(uplink='end0;reboot'), dict(uplink='tailscale0'), dict(ssh_sources=[]),
                        dict(ssh_sources=['0.0.0.0/0; drop']), dict(tcp_ports=[22]),
                        dict(udp_ports=[0]), dict(firewall='yes')]:
            with self.subTest(updates=updates), self.assertRaises(ValueError): network.validate(self.settings(**updates))

    def test_owned_tables_atomically_replaced_before_forwarding_with_wg_guard_retained(self):
        (self.config / 'rules.nft').write_text('fixture old guard')
        self.command.return_value = subprocess.CompletedProcess([], 0)
        network.apply(self.settings(firewall=True), {'wireguard': True})
        calls = self.command.call_args_list
        commands = [call.args[0] for call in calls]
        check = commands.index(['nft', '-c', '-f', '-'])
        commit = commands.index(['nft', '-f', '-'])
        self.assertLess(check, commit)
        self.assertEqual(commands[-1][0], 'sysctl')
        batch = calls[commit].kwargs['input']
        self.assertIn('delete table inet enode_host', batch)
        self.assertIn('table inet ne_home_guard', batch)
        self.assertNotIn('flush ruleset', batch)
        self.assertIn('iifname "tailscale0" accept', batch)
        self.assertIn('net.ipv4.ip_forward=1', commands[-1])
        self.assertNotIn('ip_forward=1', (self.config / 'forwarding.conf').read_text())
        network.apply(self.settings(), {'wireguard': False})
        batch = next(call.kwargs['input'] for call in reversed(self.command.call_args_list)
                     if call.args[0] == ['nft', '-f', '-'])
        self.assertIn('delete table inet enode_host', batch)
        self.assertNotIn('table inet enode_host {', batch)
        self.assertIn('table inet ne_home_guard {', batch)

    def test_failed_nft_validation_does_not_open_forwarding(self):
        def run(command, **kwargs):
            if command == ['nft', '-c', '-f', '-']:
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 0)
        self.command.side_effect = run
        with self.assertRaises(subprocess.CalledProcessError): network.apply(self.settings(firewall=True), {'wireguard': True})
        self.assertFalse(any(call.args[0][0] == 'sysctl' for call in self.command.call_args_list))

    def test_trial_confirm_and_timer_rollback_preserve_previous_settings(self):
        with patch.object(network, 'apply') as apply:
            token = network.begin(self.settings(firewall=True))
            self.assertTrue(network.load()['firewall'])
            network.confirm(token)
            self.assertFalse((self.state / 'network-pending.json').exists())
            token = network.begin(self.settings(firewall=False))
            network.rollback()
            self.assertTrue(network.load()['firewall'])
            with self.assertRaises(ValueError): network.confirm(token)
            self.assertEqual(apply.call_args.args[0]['firewall'], True)

    def test_boot_reverts_pending_and_expired_confirmation_cannot_keep_candidate(self):
        with patch.object(network, 'apply') as apply:
            token = network.begin(self.settings(firewall=True))
            trial = json.loads((self.state / 'network-pending.json').read_text())
            with patch.object(network.time, 'monotonic', return_value=trial['deadline']):
                with self.assertRaisesRegex(ValueError, 'expired'): network.confirm(token)
            self.assertFalse(network.load()['firewall'])
            network.begin(self.settings(firewall=True))
            network.restore()
            self.assertFalse(network.load()['firewall'])
            self.assertFalse((self.state / 'network-pending.json').exists())
            self.assertEqual(apply.call_args.args, ())

    def test_failed_apply_reverts_and_a_second_pending_edit_is_rejected(self):
        with patch.object(network, 'apply', side_effect=[ValueError('fixture'), None]):
            with self.assertRaisesRegex(ValueError, 'fixture'): network.begin(self.settings(firewall=True))
        self.assertFalse(network.load()['firewall'])
        with patch.object(network, 'apply'):
            network.begin(self.settings(firewall=True))
            with self.assertRaisesRegex(ValueError, 'pending'): network.begin(self.settings())

    def test_rollback_restores_generated_wg_uplink_before_resolving_auto(self):
        old = 'iifname "wg-home" oifname "end0" accept\n'
        (self.config / 'rules.nft').write_text(old)
        def apply(settings=None, **kwargs):
            if settings and settings['uplink'] == 'eth1':
                (self.config / 'rules.nft').write_text('iifname "wg-home" oifname "eth1" accept\n')
            else:
                self.assertEqual((self.config / 'rules.nft').read_text(), old)
        with patch.object(network, 'apply', side_effect=apply):
            network.begin(self.settings(uplink='eth1'))
            network.rollback()
            self.assertEqual(network.load()['uplink'], 'auto')
            network.begin(self.settings(uplink='eth1'))
            network.restore()
            self.assertEqual((self.config / 'rules.nft').read_text(), old)


    def test_ota_health_requires_network_policy_but_no_external_ddns_or_ping(self):
        control = self.state / 'control'
        control.touch()
        source = (source_path('usr/lib/network-exit/confirm-boot')).read_text()
        for failed in (False, True):
            def run(command, **kwargs):
                if failed and command[-1] == 'network-exit-network.service':
                    raise subprocess.CalledProcessError(1, command)
                return subprocess.CompletedProcess(command, 0)
            with self.subTest(failed=failed), \
                    patch.object(trial, 'is_trial', return_value=True), \
                    patch.object(bootstate, 'load', return_value={'ne_pending': 'B'}), \
                    patch.object(bootstate, 'current_slot', return_value='B'), \
                    patch.object(Path, 'read_text', return_value=json.dumps({'boot_control': str(control)})), \
                    patch.object(subprocess, 'run', side_effect=run) as command:
                if failed:
                    with self.assertRaises(SystemExit): exec(compile(source, 'confirm-boot', 'exec'), {})
                else: exec(compile(source, 'confirm-boot', 'exec'), {})
                calls = [call.args[0] for call in command.call_args_list]
                self.assertEqual(['rauc', 'status', 'mark-good'] in calls, not failed)
                self.assertFalse(any(c[0] in ('curl', 'ddclient', 'ping') for c in calls))


if __name__ == '__main__': unittest.main()
