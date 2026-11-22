"""Textual Pilot interactions with fixture-only services and persistent homes."""
from _stage_support import ROOT
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock
from textual.widgets import Button, DataTable, Input, Select, Static
import components
import service_manager
import network_config
import setup_state
import sshpub
from service_tui import ServiceApp
from ssh_editor import SSHEditor
from ssh_view import SSHScreen
from enodectl_core.textual_ui import FormScreen, Confirm, ReadOnlyScreen


class ServiceModelTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        stack=ExitStack();self.addCleanup(stack.close)
        stack.enter_context(patch.object(components,'CONFIG',self.root))
        self.run=stack.enter_context(patch.object(components.subprocess,'run',return_value=subprocess.CompletedProcess([],0,stdout='active\n')))
        stack.enter_context(patch.object(network_config,'sync'))

    def test_required_services_reject_disable_in_model_and_backend(self):
        model=service_manager.ServiceModel()
        for row in model.records():
            if row['mandatory']:
                with self.subTest(name=row['name']),self.assertRaises(ValueError):model.toggle(row['name'])
                with self.assertRaises(ValueError):components.set_enabled(row['name'],False)
        self.assertTrue(model.draft['ssh']);self.assertTrue(model.draft['tailscale'])

    def test_toggle_is_staged_and_all_enables_validate_before_changes(self):
        model=service_manager.ServiceModel();self.run.reset_mock()
        model.toggle('wireguard');model.toggle('cloudflared')
        self.run.assert_not_called()
        self.assertFalse((self.root/'components.json').exists())
        with patch.object(components,'validate',side_effect=[None,ValueError('missing token')]):
            with self.assertRaises(ValueError):model.apply()
        self.run.assert_not_called()
        self.assertFalse((self.root/'components.json').exists())

    def test_disabling_wireguard_keeps_guard_and_failed_stop_does_not_persist(self):
        components.set_enabled('wireguard',False)
        self.run.assert_called_once_with(['systemctl','disable','--now','wg-quick@wg-home.service'],check=True,capture_output=True)
        before=(self.root/'components.json').read_bytes()
        self.run.side_effect=subprocess.CalledProcessError(1,['systemctl'])
        with self.assertRaises(subprocess.CalledProcessError):components.set_enabled('singbox',False)
        self.assertEqual((self.root/'components.json').read_bytes(),before)

    def test_failed_apply_clears_completion_and_success_rechecks_authorization(self):
        self.root.mkdir(exist_ok=True)
        marker=self.root/'initialized';marker.write_text('ready')
        model=service_manager.ServiceModel();model.toggle('cloudflared')
        with patch.object(components,'validate'),patch.object(components,'set_enabled',side_effect=subprocess.CalledProcessError(1,['systemctl'])):
            with self.assertRaises(subprocess.CalledProcessError):model.apply()
        self.assertFalse(marker.exists())
        marker.write_text('ready');model.toggle('cloudflared')
        guard=Mock()
        with patch.object(components,'validate'),patch.object(components,'set_enabled'),patch.object(service_manager.registry,'hooks',return_value=[guard]),patch.object(setup_state,'complete') as complete:
            model.apply()
        guard.assert_called_once();complete.assert_called_once_with(self.root)


class TextualInteractionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.state=self.root/'state';self.config=self.state/'config';self.config.mkdir(parents=True)
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for target,name,value in [(components,'CONFIG',self.config),(components,'STATE',self.state),(network_config,'STATE',self.state),(setup_state,'RUNTIME_READY',self.root/'run/ready')]:
            self.stack.enter_context(patch.object(target,name,value))
        self.run=self.stack.enter_context(patch.object(components.subprocess,'run',return_value=subprocess.CompletedProcess([],0,stdout='active\n')))
        self.stack.enter_context(patch.object(network_config,'sync'))

    async def settle(self,app,pilot):
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

    async def select(self,app,pilot,name):
        table=app.query_one('#services',DataTable)
        table.move_cursor(row=list(app.records).index(name))
        await pilot.pause()

    async def confirm(self,app,pilot,yes=True):
        await pilot.click('#confirm-apply' if yes else '#confirm-cancel')
        await self.settle(app,pilot)

    async def test_dashboard_disables_required_toggles_and_cancel_does_not_apply(self):
        app=ServiceApp(model=service_manager.ServiceModel())
        async with app.run_test(size=(110,36)) as pilot:
            await self.settle(app,pilot)
            for name in ('ssh','tailscale','network','system-state'):
                await self.select(app,pilot,name)
                self.assertTrue(app.query_one('#toggle',Button).disabled)
            await self.select(app,pilot,'cloudflared')
            await pilot.click('#toggle');await self.settle(app,pilot)
            self.assertTrue(app.model.draft['cloudflared'])
            await pilot.click('#apply');await self.confirm(app,pilot,False)
            self.assertFalse((self.config/'components.json').exists())

    async def test_cloudflare_form_keeps_secret_masked_and_only_applies_after_confirmation(self):
        app=ServiceApp(model=service_manager.ServiceModel())
        async with app.run_test(size=(110,36)) as pilot:
            await self.settle(app,pilot);await self.select(app,pilot,'cloudflared')
            await pilot.click('#configure');await self.settle(app,pilot)
            field=app.screen.query_one('#field-token',Input)
            self.assertTrue(field.password);self.assertEqual(field.value,'')
            await pilot.click('#field-token')
            await pilot.press(*'fixture-cloud-token')
            await pilot.click('#form-save');await self.confirm(app,pilot,False)
            self.assertFalse((self.config/'cloudflare-token').exists())
            await pilot.click('#form-save');await self.confirm(app,pilot)
            self.assertEqual((self.config/'cloudflare-token').read_text(),'fixture-cloud-token\n')
            self.assertEqual(field.value,'')
            await pilot.click('#form-back');await self.settle(app,pilot)
            await pilot.click('#configure');await self.settle(app,pilot)
            self.assertEqual(app.screen.query_one('#field-token',Input).value,'')

    async def test_network_trial_cancel_calls_rollback_and_not_confirmation(self):
        app=ServiceApp(model=service_manager.ServiceModel())
        with patch.object(network_config,'begin',return_value='fixture-trial') as begin, \
                patch.object(network_config,'rollback') as rollback,patch.object(network_config,'confirm') as keep:
            async with app.run_test(size=(110,42)) as pilot:
                await self.settle(app,pilot);await self.select(app,pilot,'network')
                await pilot.click('#configure');await self.settle(app,pilot)
                app.screen.query_one('#field-firewall',Select).value=True
                await pilot.click('#form-save');await self.confirm(app,pilot)
                self.assertIsInstance(app.screen,Confirm)
                self.assertGreater(app.screen.seconds,0);self.assertLessEqual(app.screen.seconds,90)
                await self.confirm(app,pilot,False)
                rollback.assert_called_once();keep.assert_not_called()
                self.assertTrue(begin.call_args.args[0]['firewall'])

    async def test_ssh_is_configurable_from_service_and_all_optional_views_open(self):
        app=ServiceApp(model=service_manager.ServiceModel())
        with patch('tailscale_config.load',return_value={'provider':'official','headscale_url':'','advertise_exit':False,'reauth_confirm':False}):
            async with app.run_test(size=(110,42)) as pilot:
                await self.settle(app,pilot)
                for name in ('tailscale','wireguard','cloudflared','singbox','ddns','network'):
                    await self.select(app,pilot,name)
                    await pilot.click('#configure');await self.settle(app,pilot)
                    self.assertIsInstance(app.screen,FormScreen)
                    await pilot.click('#form-back');await self.settle(app,pilot)
                await self.select(app,pilot,'ssh')
                self.assertFalse(app.query_one('#configure',Button).disabled)

    async def test_system_views_are_read_only_and_refresh_from_backend(self):
        app=ServiceApp(model=service_manager.ServiceModel())
        with ExitStack() as stack:
            loaders=[]
            for name in ('state_policy','overlay_policy','watchdog_policy','power_status','ota_status'):
                loaders.append(stack.enter_context(patch(name+'.details',return_value='Fixture system status')))
            async with app.run_test(size=(110,42)) as pilot:
                await self.settle(app,pilot)
                for name in ('system-state','system-overlay','system-watchdog','system-power','ota'):
                    await self.select(app,pilot,name)
                    self.assertTrue(app.query_one('#toggle',Button).disabled)
                    await pilot.click('#configure');await self.settle(app,pilot)
                    self.assertIsInstance(app.screen,ReadOnlyScreen)
                    self.assertEqual([button.id for button in app.screen.query(Button)],['policy-refresh','policy-back'])
                    await pilot.click('#policy-refresh');await self.settle(app,pilot)
                    await pilot.click('#policy-back');await self.settle(app,pilot)
            for loader in loaders:self.assertEqual(loader.call_count,2)

    async def test_form_quit_cancel_retains_unsaved_changes(self):
        app=ServiceApp(model=service_manager.ServiceModel())
        async with app.run_test(size=(110,36)) as pilot:
            await self.settle(app,pilot);await self.select(app,pilot,'cloudflared')
            await pilot.click('#configure');await self.settle(app,pilot)
            app.screen.query_one('#field-token',Input).value='unsaved-token'
            await pilot.press('ctrl+q');await pilot.pause()
            self.assertIsInstance(app.screen,Confirm)
            await self.confirm(app,pilot,False)
            self.assertEqual(app.screen.query_one('#field-token',Input).value,'unsaved-token')
            self.assertFalse((self.config/'cloudflare-token').exists())

    async def test_ssh_key_ca_edits_preserve_last_authorization_and_save_explicitly(self):
        home=self.state/'homes/pi';home.mkdir(parents=True)
        keys=[]
        # The global systemctl mock is paused only for fixture ssh-keygen calls.
        self.run.side_effect=None
        # Generate fixture public encodings using the helper's real key validator
        # and a fixed well-formed Ed25519 public blob; no private key is needed.
        import base64,struct
        for index in (1,2):
            kind=b'ssh-ed25519';blob=struct.pack('!I',len(kind))+kind+struct.pack('!I',32)+bytes([index])*32
            keys.append('ssh-ed25519 '+base64.b64encode(blob).decode()+' fixture-'+str(index)+'\n')
        account=SimpleNamespace(pw_name='pi',pw_uid=os.getuid() if os.getuid()>=1000 else 1000,pw_gid=os.getgid(),pw_dir=str(home))
        for name,value in [('STATE',self.state),('HOMES',self.state/'homes')]:self.stack.enter_context(patch.object(sshpub,name,value))
        self.stack.enter_context(patch.object(sshpub.os,'geteuid',return_value=0))
        self.stack.enter_context(patch.object(sshpub.os.path,'ismount',return_value=True))
        self.stack.enter_context(patch.object(sshpub.pwd,'getpwnam',return_value=account))
        # ssh-keygen -l validation is exercised by the dedicated key tests; return
        # success here while keeping all calls within the fixture system mock.
        (home/'.ssh').mkdir();(home/'.ssh/authorized_keys').write_text(keys[0])
        editor=SSHEditor(self.config)
        app=ServiceApp(model=service_manager.ServiceModel())
        async with app.run_test(size=(140,42)) as pilot:
            await self.settle(app,pilot)
            app.push_screen(SSHScreen(model=editor));await self.settle(app,pilot)
            await pilot.click('#ssh-add-ca');await self.settle(app,pilot)
            app.screen.query_one('#field-source',Input).value=keys[1].strip()
            await pilot.click('#form-save');await self.confirm(app,pilot)
            await pilot.click('#form-back');await self.settle(app,pilot)
            self.assertEqual(len(editor.records()),2)
            self.assertEqual((self.config/'ssh-user-ca.pub').read_text(),'')
            table=app.screen.query_one('#keys',DataTable);table.move_cursor(row=0);await pilot.pause()
            await pilot.click('#ssh-remove');await self.confirm(app,pilot)
            self.assertEqual(len(editor.records()),1)
            await pilot.click('#ssh-remove');await self.confirm(app,pilot)
            self.assertEqual(len(editor.records()),1)
            await pilot.click('#ssh-save');await self.confirm(app,pilot)
            self.assertEqual((self.config/'ssh-user-ca.pub').read_text(),keys[1])
            self.assertEqual((home/'.ssh/authorized_keys').read_text(),'')
