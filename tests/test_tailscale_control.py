"""Noninteractive control-plane changes, explicit reauth and authorization state."""
from _stage_support import source_path
from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import components
import tailscale_config as config
import tailscale_control as control

HEADSCALE='https://headscale.example.com:8443'


class TailscaleControlTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.live=control.OFFICIAL;self.state='NeedsLogin';self.have=False
        self.after='Running';self.keep=False;self.url='';self.commands=[];self.health=[];self.stdout='{}'
        stack=ExitStack();self.addCleanup(stack.close)
        stack.enter_context(patch.object(components,'CONFIG',self.root))
        stack.enter_context(patch.object(config.setup_state,'RUNTIME_READY',self.root/'runtime-ready'))
        stack.enter_context(patch.object(config.network_config,'sync'))
        def output(command,**kwargs):
            if command==['tailscale','debug','prefs']:return json.dumps({'ControlURL':self.live})
            return json.dumps({'BackendState':self.state,'HaveNodeKey':self.have,'AuthURL':self.url,'Health':self.health})
        def run(command,**kwargs):
            self.commands.append(command)
            self.assertEqual(command[:2],['tailscale','up'])
            self.assertTrue(kwargs['capture_output']);self.assertEqual(kwargs['timeout'],20)
            self.assertFalse((self.root/'initialized').exists())
            if not self.keep:self.live=next(v.split('=',1)[1] for v in command if v.startswith('--login-server='))
            self.state=self.after;self.have=True
            return subprocess.CompletedProcess(command,0,stdout=self.stdout)
        stack.enter_context(patch.object(control.subprocess,'check_output',side_effect=output))
        self.run=stack.enter_context(patch.object(control.subprocess,'run',side_effect=run))

    def values(self,**updates):return dict(provider='official',headscale_url='',advertise_exit=False,reauth_confirm=False,**updates)

    def test_official_login_explicit_and_ordinary_openssh_only(self):
        self.live=''
        config.save(self.values())
        self.assertIn('--login-server='+control.OFFICIAL,self.commands[0])
        self.assertIn('--ssh=false',self.commands[0]);self.assertIn('--timeout=5s',self.commands[0])
        self.assertNotIn('--force-reauth',self.commands[0])
        self.assertEqual((self.root/'tailscale-control.json').stat().st_mode & 0o777,0o600)

    def test_first_headscale_and_same_control_keep_existing_identity(self):
        values=self.values();values.update(provider='headscale',headscale_url=HEADSCALE)
        config.save(values);self.assertNotIn('--force-reauth',self.commands[-1])
        self.assertEqual(config.load()['headscale_url'],HEADSCALE)
        config.save(values);self.assertNotIn('--force-reauth',self.commands[-1])

    def test_legacy_headscale_is_loaded_without_writing_or_switching(self):
        self.live=HEADSCALE;self.state='Running';self.have=True
        self.assertEqual(config.load()['headscale_url'],HEADSCALE)
        self.run.assert_not_called();self.assertFalse((self.root/'tailscale-control.json').exists())

    def test_control_switch_requires_explicit_confirmation_before_any_mutation(self):
        self.state='Running';self.have=True
        (self.root/'initialized').write_text('1\n')
        values=self.values();values.update(provider='headscale',headscale_url=HEADSCALE)
        with self.assertRaisesRegex(ValueError,'Explicitly confirm'):config.save(values)
        self.run.assert_not_called();self.assertTrue((self.root/'initialized').exists())
        values['reauth_confirm']=True
        # Completion cannot be restored by this unit fixture's absent SSH keys.
        (self.root/'initialized').unlink()
        config.save(values);self.assertIn('--force-reauth',self.commands[-1])

    def test_pending_auth_url_is_presented_and_setup_remains_incomplete(self):
        self.after='NeedsLogin';self.url='https://login.tailscale.com/a/fixture'
        message=config.save(self.values())
        self.assertIn(self.url,message)
        self.assertFalse((self.root/'initialized').exists())
        with self.assertRaisesRegex(ValueError,'authorization'):config.check()

    def test_empty_url_reports_actual_login_health_and_retry(self):
        self.after='NeedsLogin';self.health=['fetch control key: failed to resolve headscale.example.com']
        with self.assertRaisesRegex(ValueError,'failed to resolve') as failure:config.save(self.values())
        self.assertIn('not available yet',str(failure.exception))
        self.assertIn('failed to resolve',config.details())
        self.assertIn('Check network connectivity',config.details())

    def test_pending_url_can_come_from_cli_json_stream(self):
        self.after='NeedsLogin'
        self.stdout='{}\n'+json.dumps({'AuthURL':'https://login.tailscale.com/a/fixture'})+'\n{}'
        self.assertIn('https://login.tailscale.com/a/fixture',config.save(self.values()))

    def test_wrong_actual_control_fails_without_automatic_official_fallback(self):
        self.keep=True
        values=self.values();values.update(provider='headscale',headscale_url=HEADSCALE)
        with self.assertRaises(ValueError):config.save(values)
        self.assertEqual(len(self.commands),1)
        self.assertEqual(json.loads((self.root/'tailscale-control.json').read_text())['login_server'],HEADSCALE)
        self.assertFalse((self.root/'initialized').exists())

    def test_invalid_control_urls_leave_state_untouched(self):
        for url in ('http://heads.example.com','https://user:secret@heads.example.com','https://heads.example.com/path','https://heads.example.com?x=1','https://heads.example.com:99999'):
            values=self.values();values.update(provider='headscale',headscale_url=url)
            with self.subTest(url=url),self.assertRaises(ValueError):config.save(values)
        self.run.assert_not_called();self.assertFalse((self.root/'tailscale-control.json').exists())

    def test_failed_local_preferences_do_not_overwrite_configuration(self):
        with patch.object(control.subprocess,'check_output',side_effect=subprocess.CalledProcessError(1,['tailscale'])):
            with self.assertRaises(subprocess.CalledProcessError):config.save(self.values())
        self.run.assert_not_called();self.assertFalse((self.root/'tailscale-control.json').exists())

    def test_successful_health_check_uses_only_local_client_state(self):
        self.state='Running';self.live=HEADSCALE
        (self.root/'tailscale-control.json').write_text(json.dumps({'login_server':HEADSCALE}))
        config.check();self.run.assert_not_called()
