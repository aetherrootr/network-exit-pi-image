"""Repeatable profile operations use only private fixture files and mock tools."""
from _stage_support import source_path
from contextlib import ExitStack
import base64
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import components
import setup_state
import cloudflared_config
import ddns_config
import ddns
import singbox_config
import wireguard_config
import network_management
import network_config
from service_config import TrialChange


class ServiceConfigurationTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.state=self.root/'state';self.config=self.state/'config';self.config.mkdir(parents=True)
        stack=ExitStack();self.addCleanup(stack.close)
        for target,name,value in [(components,'CONFIG',self.config),(components,'STATE',self.state),
                                  (setup_state,'RUNTIME_READY',self.root/'run/ready'),(ddns,'STATE',self.state/'ddns')]:
            stack.enter_context(patch.object(target,name,value))
        self.run=stack.enter_context(patch.object(subprocess,'run',return_value=subprocess.CompletedProcess([],0,stdout='')))
        stack.enter_context(patch.object(network_config,'sync'))
        stack.enter_context(patch.object(network_config,'uplink',return_value='end0'))

    def profile(self):return dict(provider='cloudflare',hostname='node.example.com',zone='example.com',server='',login='',secret='original-token',family='ipv4',interval='300')

    def test_cloudflare_blank_keeps_token_and_opening_never_reveals_it(self):
        path=self.config/'cloudflare-token';path.write_text('old-token\n')
        self.assertEqual(cloudflared_config.load(),{'token':''})
        cloudflared_config.save({'token':''})
        self.assertEqual(path.read_text(),'old-token\n')
        cloudflared_config.save({'token':'new-token'})
        self.assertEqual(path.read_text(),'new-token\n')
        self.assertEqual(path.stat().st_mode & 0o777,0o600)
        self.assertFalse(any('restart' in call.args[0] for call in self.run.call_args_list))

    def test_ddns_secret_is_masked_kept_for_same_provider_and_not_reused_across_providers(self):
        ddns_config.save(self.profile())
        loaded=ddns_config.load();self.assertEqual(loaded['secret'],'')
        loaded['hostname']='other.example.com'
        ddns_config.save(loaded)
        self.assertEqual(ddns.load()['secret'],'original-token')
        changed={**loaded,'provider':'duckdns','hostname':'node.duckdns.org'}
        with self.assertRaisesRegex(ValueError,'token or password'):ddns_config.save(changed)
        self.assertEqual(ddns.load()['provider'],'cloudflare')

    def test_ddns_reconfiguration_stops_existing_worker_clears_cache_then_restarts_timer(self):
        ddns_config.save(self.profile())
        components.save(self.config/'components.json',json.dumps({'ddns':True}))
        ddns.STATE.mkdir(parents=True);(ddns.STATE/'ddclient.cache').write_text('old')
        ddns_config.save({**self.profile(),'hostname':'other.example.com'})
        commands=[c.args[0] for c in self.run.call_args_list]
        self.assertIn(['systemctl','stop','network-exit-ddns.timer','network-exit-ddns.service'],commands)
        self.assertIn(['systemctl','restart','network-exit-ddns.timer'],commands)
        self.assertFalse((ddns.STATE/'ddclient.cache').exists())

    def test_singbox_open_does_not_generate_credentials_and_existing_json_is_retained(self):
        self.assertEqual(singbox_config.load(),{'config':''})
        path=self.config/'sing-box.json';self.assertFalse(path.exists())
        singbox_config.save({'config':''})
        saved=json.loads(path.read_text());secret=saved['inbounds'][0]['users'][0]['password']
        singbox_config.save(singbox_config.load())
        self.assertEqual(json.loads(path.read_text())['inbounds'][0]['users'][0]['password'],secret)
        before=path.read_bytes();self.run.return_value=subprocess.CompletedProcess([],1)
        with self.assertRaisesRegex(ValueError,'rejected'):singbox_config.save({'config':'{}'})
        self.assertEqual(path.read_bytes(),before)

    def test_wireguard_reconfiguration_preserves_private_key_and_changes_peer(self):
        private=base64.b64encode(bytes([1])*32).decode()
        peer1=base64.b64encode(bytes([2])*32).decode();peer2=base64.b64encode(bytes([3])*32).decode()
        key=self.config/'wireguard-device.key';key.write_text(private+'\n')
        wireguard_config.save(dict(peer_key=peer1,peer_host='relay.example.com',peer_port='51820'))
        wireguard_config.save(dict(peer_key=peer2,peer_host='new-relay.example.com',peer_port='51821'))
        self.assertEqual(key.read_text(),private+'\n')
        self.assertEqual(wireguard_config.load()['peer_key'],peer2)
        self.assertEqual(wireguard_config.load()['peer_port'],'51821')
        self.assertFalse(any('genkey' in c.args[0] for c in self.run.call_args_list))

    def test_network_form_returns_trial_callbacks_and_parses_typed_fields(self):
        with patch.object(network_config,'begin',return_value='token') as begin,patch.object(network_config,'confirm') as keep,patch.object(network_config,'rollback') as rollback:
            value=network_management.load();value.update(firewall=True,tcp_ports='8080,8443')
            result=network_management.save(value)
            self.assertIsInstance(result,TrialChange)
            self.assertEqual(begin.call_args.args[0]['tcp_ports'],[8080,8443])
            result.confirm();keep.assert_called_once_with('token')
            result.rollback();rollback.assert_called_once_with()
