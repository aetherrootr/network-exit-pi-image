"""Textual Pilot interactions with fixture-only services and persistent homes."""
from _stage_support import ROOT
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import threading
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock
from textual.widgets import Button, DataTable, Input, Label, Select, Static, TextArea
import components
import network_management
import service_manager
import service_diagnostics
import network_config
import setup_state
import sshpub
from service_tui import ServiceApp
from ssh_editor import SSHEditor
from ssh_view import SSHScreen
from enodectl_core.textual_ui import FormScreen, Confirm, ReadOnlyScreen, Details, Field, error_message, navigate
from network_view import WiFiScanScreen


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
                with self.subTest(name=row['name']),self.assertRaises(ValueError):service_manager.change_startup(row['name'],False)
                with self.assertRaises(ValueError):components.set_enabled(row['name'],False)
        self.assertTrue(model.enabled['ssh']);self.assertTrue(model.enabled['tailscale'])

    def test_local_enable_validates_before_changes(self):
        self.run.reset_mock()
        with patch.object(components,'validate',side_effect=ValueError('missing token')):
            with self.assertRaises(ValueError):service_manager.change_startup('cloudflared',True)
        self.run.assert_not_called()
        self.assertFalse((self.root/'components.json').exists())

    def test_runtime_queries_are_batched_and_placeholder_rows_need_no_process(self):
        model=service_manager.ServiceModel();self.run.reset_mock()
        pending=model.records(query_runtime=False)
        self.assertTrue(any(row['state']=='checking...' for row in pending))
        self.run.assert_not_called()
        def query(args, **kwargs):
            self.assertEqual(args[:2],['systemctl','is-active'])
            return subprocess.CompletedProcess(args,0,stdout='\n'.join('inactive' if unit=='rauc.service' else 'active' for unit in args[2:]))
        self.run.side_effect=query
        rows={row['name']:row for row in model.records()}
        self.run.assert_called_once()
        self.assertEqual(rows['ota']['state'],'on-demand')
        self.assertEqual(rows['wireguard']['state'],'active, active')

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
        with patch.object(components,'validate'),patch.object(components,'set_enabled',side_effect=subprocess.CalledProcessError(1,['systemctl'])):
            with self.assertRaises(subprocess.CalledProcessError):service_manager.change_startup('cloudflared',True)
        self.assertFalse(marker.exists())
        marker.write_text('ready')
        guard=Mock()
        with patch.object(components,'validate'),patch.object(components,'set_enabled'),patch.object(service_manager.registry,'hooks',return_value=[guard]),patch.object(setup_state,'complete') as complete:
            service_manager.change_startup('cloudflared',True)
        guard.assert_called_once();complete.assert_called_once_with(self.root)

    def test_failed_off_profile_save_restores_prior_startup(self):
        (self.root/'components.json').write_text('{"cloudflared": true}')
        def fail(): raise ValueError('invalid edited profile')
        with patch.object(service_manager,'change_startup') as change:
            with self.assertRaisesRegex(ValueError,'invalid edited profile'):
                service_manager.apply_service_settings('cloudflared',False,True,fail)
        self.assertEqual([call.args for call in change.call_args_list],[('cloudflared',False),('cloudflared',True)])


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

    async def test_arrow_and_enter_navigation_without_tab(self):
        app=ServiceApp(model=service_manager.ServiceModel())
        async with app.run_test(size=(110,36)) as pilot:
            await self.settle(app,pilot)
            table=app.query_one('#services',DataTable)
            self.assertIs(app.focused,table)
            target=list(app.records).index('cloudflared')
            for _ in range(target): await pilot.press('down')
            self.assertEqual(app.selected,'cloudflared')
            await pilot.press('enter');await self.settle(app,pilot)
            self.assertIsInstance(app.screen,FormScreen)
            self.assertEqual(app.focused.id,'field-token')
            await pilot.press(*'fixture-token','enter')
            self.assertEqual(app.focused.id,'form-save')
            await pilot.press('enter');await pilot.pause()
            self.assertIsInstance(app.screen,Confirm)
            self.assertEqual(app.focused.id,'confirm-cancel')
            await pilot.press('right','enter');await self.settle(app,pilot)
            self.assertEqual((self.config/'cloudflare-token').read_text(),'fixture-token\n')
            await pilot.press('escape');await self.settle(app,pilot)
            self.assertIs(app.focused,table)
            await pilot.press('right')
            self.assertEqual(app.focused.id,'configure')
            await pilot.press('right')
            self.assertEqual(app.focused.id,'exit')

    async def test_choice_multiline_and_login_dialog_use_native_editing_keys(self):
        app=ServiceApp(model=service_manager.ServiceModel())
        form=FormScreen('Fixture', [Field('choice','Choice','choice',(('One','one'),('Two','two'))),
                                    Field('config','Multiline','multiline')],
                        lambda: {'choice':'one','config':''}, lambda values: 'https://login.example.test/a/fixture',
                        result_dialog=True)
        async with app.run_test(size=(110,36)) as pilot:
            await self.settle(app,pilot);app.push_screen(form);await self.settle(app,pilot)
            choice=form.query_one('#field-choice',Select)
            self.assertIs(app.focused,choice)
            await pilot.press('right');self.assertEqual(choice.value,'two')
            await pilot.press('left');self.assertEqual(choice.value,'one')
            await pilot.press('enter','down','enter');await pilot.pause()
            self.assertEqual(choice.value,'two')
            await pilot.press('down');await pilot.pause()
            area=form.query_one('#field-config',TextArea)
            self.assertIs(app.focused,area);self.assertTrue(area.read_only)
            await pilot.press('enter','a','enter','b','escape');await pilot.pause()
            self.assertEqual(area.text,'a\nb');self.assertTrue(area.read_only)
            await pilot.press('down','enter');await pilot.pause()
            self.assertIsInstance(app.screen,Confirm)
            await pilot.press('right','enter');await self.settle(app,pilot)
            self.assertIsInstance(app.screen,Details)
            self.assertIn('https://login.example.test/a/fixture',app.screen.text)
            self.assertEqual(app.focused.id,'details-back')
            await pilot.press('enter');await pilot.pause()
            self.assertIs(app.screen,form)

    async def test_fast_arrow_focus_changes_are_not_deferred(self):
        app=ServiceApp(model=service_manager.ServiceModel())
        async with app.run_test(size=(110,36)) as pilot:
            await self.settle(app,pilot);await self.select(app,pilot,'cloudflared')
            self.assertEqual(app.focused.id,'services')
            for expected in ('configure','exit'):
                navigate(app.screen,'right')
                self.assertEqual(app.focused.id,expected)
            for expected in ('configure','services'):
                navigate(app.screen,'left')
                self.assertEqual(app.focused.id,expected)

    async def test_dashboard_shows_read_only_bootchain_banner(self):
        app=ServiceApp(model=service_manager.ServiceModel())
        with patch('status_summary.bootchain_banner',return_value='Bootchain: v0.0.2 | Slot A | Confirmed A | Pending - | confirmed'):
            async with app.run_test(size=(110,36)) as pilot:
                await self.settle(app,pilot)
                self.assertIn('Slot A',str(app.query_one('#bootchain-summary',Static).content))

    async def test_dashboard_loads_rows_before_runtime_query_and_updates_without_losing_focus(self):
        model=service_manager.ServiceModel();original=model.records
        gate=threading.Event();self.addCleanup(gate.set)
        runtime=['active']
        def records(query_runtime=True):
            rows=original(query_runtime=False)
            if query_runtime:
                gate.wait(5)
                for row in rows:
                    if row['name']=='cloudflared': row['state']=runtime[0]
            return rows
        app=ServiceApp(model=model)
        with patch.object(model,'records',side_effect=records),patch.object(ServiceApp,'REFRESH_SECONDS',0.2):
            async with app.run_test(size=(110,36)) as pilot:
                await pilot.pause()
                table=app.query_one('#services',DataTable)
                self.assertGreater(table.row_count,0)
                self.assertTrue(app.refreshing)
                await self.select(app,pilot,'cloudflared')
                self.assertFalse(app.query_one('#configure',Button).disabled)
                row_before=table.cursor_row
                gate.set();await pilot.pause(0.4)
                self.assertEqual(app.records['cloudflared']['state'],'active')
                runtime[0]='failed';await pilot.pause(0.5)
                self.assertEqual(app.records['cloudflared']['state'],'failed')
                self.assertEqual(table.cursor_row,row_before)
                self.assertIs(app.focused,table)
                self.assertEqual([button.id for button in app.query(Button)],['configure','exit'])

    async def test_single_exit_finishes_incomplete_setup_and_checks_ready_device(self):
        app=ServiceApp(model=service_manager.ServiceModel(),first_boot=True)
        with patch.object(service_manager,'finish_setup') as finish,patch.object(service_diagnostics,'health') as health:
            async with app.run_test(size=(110,36)) as pilot:
                await self.settle(app,pilot)
                await pilot.click('#exit');await pilot.pause()
                self.assertIsInstance(app.screen,Confirm)
                await pilot.click('#confirm-apply');await pilot.pause()
            finish.assert_called_once();health.assert_not_called()
        (self.config/'initialized').write_text('1\n')
        app=ServiceApp(model=service_manager.ServiceModel())
        with patch.object(service_manager,'finish_setup') as finish,patch.object(service_diagnostics,'health') as health:
            async with app.run_test(size=(110,36)) as pilot:
                await self.settle(app,pilot)
                await pilot.click('#exit');await pilot.pause()
            health.assert_called_once();finish.assert_not_called()

    async def test_status_dialog_is_immediate_and_can_escape_while_query_runs(self):
        app=ServiceApp(model=service_manager.ServiceModel())
        gate=threading.Event()
        self.addCleanup(gate.set)
        def status():
            gate.wait(5)
            return 'Fixture status'
        form=FormScreen('Fixture',[Field('value','Value')],lambda:{'value':'old'},lambda values:'saved',status)
        async with app.run_test(size=(110,36)) as pilot:
            await self.settle(app,pilot);app.push_screen(form);await self.settle(app,pilot)
            await pilot.click('#form-details');await pilot.pause()
            self.assertIsInstance(app.screen,Details)
            self.assertIn('Checking service status',app.screen.text)
            self.assertFalse(app.screen.query_one('#details-back',Button).disabled)
            await pilot.press('escape');await pilot.pause()
            self.assertIs(app.screen,form)
            self.assertFalse(form.query_one('#form-save',Button).disabled)
            gate.set()
            await self.settle(app,pilot)
            self.assertIs(app.screen,form)
            await pilot.click('#form-details');await self.settle(app,pilot)
            self.assertEqual(app.screen.text,'Fixture status')
            await pilot.press('left');self.assertEqual(app.focused.id,'details-refresh')
            await pilot.press('right');self.assertEqual(app.focused.id,'details-back')
            await pilot.press('enter');await pilot.pause()
            self.assertIs(app.screen,form)

    async def test_cloudflare_diagnostics_are_on_its_form_and_exit_checks_health(self):
        app=ServiceApp(model=service_manager.ServiceModel())
        (self.config/'initialized').write_text('1\n')
        with patch.object(service_diagnostics,'logs',return_value='Fixture tunnel log'), \
                patch.object(service_diagnostics,'health',side_effect=ValueError('Cloudflare HTTPS failed')):
            async with app.run_test(size=(110,36)) as pilot:
                await self.settle(app,pilot);await self.select(app,pilot,'cloudflared')
                await pilot.press('enter');await self.settle(app,pilot)
                self.assertIsInstance(app.screen,FormScreen)
                await pilot.click('#form-logs');await self.settle(app,pilot)
                self.assertIn('Fixture tunnel log',app.screen.text)
                await pilot.press('escape','escape');await self.settle(app,pilot)
                await pilot.press('ctrl+q');await self.settle(app,pilot)
                self.assertIn('Cloudflare HTTPS failed',app.screen.text)
                await pilot.press('escape');await pilot.pause()
                self.assertIsInstance(app.screen,Confirm)
                await self.confirm(app,pilot,False)

    async def test_headscale_address_only_visible_for_headscale_and_skipped_by_navigation(self):
        import tailscale_config as backend
        from tailscale_view import screen
        target='https://headscale.example.test'
        for initial in ('official','headscale'):
            with self.subTest(initial=initial), patch.object(backend,'load',return_value={
                    'provider':initial,'headscale_url':target if initial=='headscale' else '', 'advertise_exit':False}):
                app=ServiceApp(model=service_manager.ServiceModel())
                async with app.run_test(size=(110,36)) as pilot:
                    await self.settle(app,pilot);app.push_screen(screen());await self.settle(app,pilot)
                    form=app.screen
                    provider=form.query_one('#field-provider',Select)
                    address=form.query_one('#field-headscale_url',Input)
                    label=form.query_one('#label-headscale_url',Label)
                    advertise=form.query_one('#field-advertise_exit',Select)
                    self.assertEqual(address.display,initial=='headscale')
                    self.assertEqual(label.display,initial=='headscale')
                    self.assertEqual(address.value,target if initial=='headscale' else '')
                    provider.value='official';await pilot.pause()
                    self.assertFalse(address.display);self.assertFalse(label.display)
                    self.assertNotIn(address,form.focus_chain)
                    form.set_focus(provider);await pilot.press('down');await pilot.pause()
                    self.assertIs(form.focused,advertise)
                    form.set_focus(provider);await pilot.press('right');await pilot.pause()
                    self.assertEqual(provider.value,'headscale')
                    self.assertTrue(address.display);self.assertTrue(label.display)
                    await pilot.press('down');await pilot.pause()
                    self.assertIs(form.focused,address)
                    address.value=target
                    provider.value='official';await pilot.pause()
                    self.assertIsNot(form.focused,address)
                    self.assertEqual(form.values()['headscale_url'],'')
                    provider.value='headscale';await pilot.pause()
                    self.assertEqual(address.value,target)

    async def test_tailscale_reauthentication_confirmation_is_conditional(self):
        import tailscale_config as backend
        from tailscale_view import screen
        app=ServiceApp(model=service_manager.ServiceModel())
        target='https://headscale.example.test'
        def save(values):
            if values['provider']=='headscale' and not values.get('reauth_confirm'):
                raise backend.ReauthenticationRequired('https://controlplane.tailscale.com',target)
            return 'Configuration saved'
        with patch.object(backend,'load',return_value={'provider':'official','headscale_url':'','advertise_exit':False}), \
                patch.object(backend,'save',side_effect=save) as apply:
            async with app.run_test(size=(110,36)) as pilot:
                await self.settle(app,pilot);app.push_screen(screen());await self.settle(app,pilot)
                form=app.screen
                self.assertFalse(list(form.query('#field-reauth_confirm')))
                await pilot.click('#form-save');await self.confirm(app,pilot)
                self.assertIsInstance(app.screen,Details)
                self.assertNotIn('reauth_confirm',apply.call_args.args[0])
                await pilot.press('escape');await pilot.pause()
                form.query_one('#field-provider',Select).value='headscale'
                form.query_one('#field-headscale_url',Input).value=target
                await pilot.click('#form-save');await self.confirm(app,pilot)
                self.assertIsInstance(app.screen,Confirm)
                self.assertIn(target,app.screen.message)
                await self.confirm(app,pilot,False)
                self.assertEqual(apply.call_count,2)
                await pilot.click('#form-save');await self.confirm(app,pilot)
                await self.confirm(app,pilot)
                self.assertIsInstance(app.screen,Details)
                self.assertTrue(apply.call_args.args[0]['reauth_confirm'])

    async def test_status_timeout_is_visible_and_back_still_works(self):
        app=ServiceApp(model=service_manager.ServiceModel())
        gate=threading.Event()
        self.addCleanup(gate.set)
        def status():
            gate.wait(5)
            return 'Late result'
        form=FormScreen('Fixture',[Field('value','Value')],lambda:{'value':'old'},lambda values:'saved',status)
        with patch.object(Details,'STATUS_TIMEOUT',0.1):
            async with app.run_test(size=(110,36)) as pilot:
                await self.settle(app,pilot);app.push_screen(form);await self.settle(app,pilot)
                await pilot.click('#form-details');await pilot.pause(0.25)
                self.assertIn('timed out',app.screen.text)
                self.assertFalse(app.screen.query_one('#details-refresh',Button).disabled)
                gate.set();await pilot.pause()
                self.assertIn('timed out',app.screen.text)
                await pilot.press('escape');await pilot.pause()
                self.assertIs(app.screen,form)

    async def test_read_only_status_can_return_before_loader_finishes(self):
        app=ServiceApp(model=service_manager.ServiceModel())
        gate=threading.Event()
        self.addCleanup(gate.set)
        def status():
            gate.wait(5)
            return 'Late policy result'
        async with app.run_test(size=(110,36)) as pilot:
            await self.settle(app,pilot)
            app.push_screen(ReadOnlyScreen('Fixture',status));await pilot.pause()
            self.assertFalse(app.screen.query_one('#policy-back',Button).disabled)
            await pilot.press('escape');await pilot.pause()
            self.assertEqual(len(app.screen_stack),1)
            gate.set();await self.settle(app,pilot)
            self.assertEqual(len(app.screen_stack),1)

    async def test_configuration_errors_use_scrollable_dialog_and_hide_command_secrets(self):
        app=ServiceApp(model=service_manager.ServiceModel())
        def fail(values): raise ValueError('Invalid configuration: check the port number')
        form=FormScreen('Fixture',[Field('value','Value')],lambda:{'value':'bad'},fail)
        async with app.run_test(size=(110,36)) as pilot:
            await self.settle(app,pilot);app.push_screen(form);await self.settle(app,pilot)
            await pilot.press('enter','enter');await pilot.pause()
            await pilot.press('right','enter');await self.settle(app,pilot)
            self.assertIsInstance(app.screen,Details)
            self.assertIn('check the port number',app.screen.text)
            await pilot.press('enter');await pilot.pause()
            self.assertIs(app.screen,form)
            self.assertEqual(form.query_one('#field-value',Input).value,'bad')
        self.assertNotIn('secret-token',error_message(subprocess.CalledProcessError(1,['tool','secret-token'])))

    async def test_wifi_scan_retry_and_select_ssid_with_arrows(self):
        app=ServiceApp(model=service_manager.ServiceModel())
        networks=[{'ssid':'Cafe','signal':-20.0,'modes':()},
                  {'ssid':'Home \u5bb6','signal':-42.0,'modes':('sae',)}]
        with patch.object(network_management,'scan',side_effect=[ValueError('Radio busy; try again'),networks]) as scan:
            async with app.run_test(size=(110,42)) as pilot:
                await self.settle(app,pilot);await self.select(app,pilot,'network')
                await pilot.press('enter');await self.settle(app,pilot)
                form=app.screen
                for _ in range(7): await pilot.press('down')
                self.assertEqual(app.focused.id,'scan-wifi')
                await pilot.press('enter');await self.settle(app,pilot)
                self.assertIsInstance(app.screen,Details)
                self.assertIn('Radio busy',app.screen.text)
                await pilot.press('enter');await pilot.pause()
                self.assertIsInstance(app.screen,WiFiScanScreen)
                self.assertEqual(app.focused.id,'scan-again')
                await pilot.press('enter');await self.settle(app,pilot)
                self.assertEqual(app.focused.id,'wifi-networks')
                await pilot.press('enter');await pilot.pause()
                self.assertIsInstance(app.screen,Details)
                self.assertIn('does not advertise WPA2/WPA3',app.screen.text)
                await pilot.press('enter','down','enter');await pilot.pause()
                self.assertIs(app.screen,form)
                self.assertTrue(form.query_one('#field-wifi_enabled',Select).value)
                self.assertEqual(form.query_one('#field-wifi_ssid',Input).value,'Home \u5bb6')
                self.assertEqual(form.query_one('#field-wifi_security',Select).value,'sae')
                self.assertEqual(app.focused.id,'field-wifi_password')
                self.assertEqual(scan.call_count,2)
                self.assertFalse((self.config/'connectivity/profile.json').exists())

    async def test_startup_and_cloudflare_token_apply_together_on_service_page(self):
        app=ServiceApp(model=service_manager.ServiceModel())
        async with app.run_test(size=(110,36)) as pilot:
            await self.settle(app,pilot)
            self.assertFalse(list(app.query('#toggle')))
            self.assertFalse(list(app.query('#apply')))
            await self.select(app,pilot,'cloudflared')
            await pilot.click('#configure');await self.settle(app,pilot)
            form=app.screen
            startup=form.query_one('#form-startup',Select)
            self.assertEqual(startup.parent.id,'fields')
            self.assertGreaterEqual(startup.region.height,5)
            self.assertEqual(startup.region.width,form.query_one('#field-token',Input).region.width)
            form.query_one('#field-token',Input).value='fixture-token'
            form.query_one('#form-startup',Select).value=True
            await pilot.click('#form-save');await self.confirm(app,pilot,False)
            self.assertFalse((self.config/'components.json').exists())
            self.assertFalse((self.config/'cloudflare-token').exists())
            await pilot.click('#form-save');await self.confirm(app,pilot)
            self.assertTrue(components.choices()['cloudflared'])
            self.assertEqual((self.config/'cloudflare-token').read_text(),'fixture-token\n')
            self.assertTrue(form.query_one('#form-startup',Select).value)
            form.query_one('#field-token',Input).value='new-token'
            form.query_one('#form-startup',Select).value=False
            self.run.reset_mock()
            await pilot.click('#form-save');await self.confirm(app,pilot)
            self.assertFalse(components.choices()['cloudflared'])
            self.assertEqual((self.config/'cloudflare-token').read_text(),'new-token\n')
            self.assertFalse(any('restart' in call.args[0] for call in self.run.call_args_list))

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
                password=app.screen.query_one('#field-wifi_password',Input)
                self.assertTrue(password.password)
                self.assertEqual(password.value,'')
                app.screen.query_one('#field-wifi_enabled',Select).value=True
                app.screen.query_one('#field-wifi_ssid',Input).value='Fixture Home'
                password.value='fixture-private-wifi'
                await pilot.click('#form-save');await self.confirm(app,pilot)
                self.assertIsInstance(app.screen,Confirm)
                self.assertGreater(app.screen.seconds,0);self.assertLessEqual(app.screen.seconds,90)
                await self.confirm(app,pilot,False)
                rollback.assert_called_once();keep.assert_not_called()
                self.assertTrue(begin.call_args.args[0]['firewall'])
                self.assertEqual(begin.call_args.kwargs['link_settings']['wifi_ssid'],'Fixture Home')
                self.assertEqual(begin.call_args.kwargs['link_settings']['wifi_password'],'fixture-private-wifi')

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

    async def test_singbox_generated_profile_is_displayed_and_reapply_keeps_password(self):
        app=ServiceApp(model=service_manager.ServiceModel())
        async with app.run_test(size=(110,42)) as pilot:
            await self.settle(app,pilot)
            await self.select(app,pilot,'singbox')
            await pilot.click('#configure');await self.settle(app,pilot)
            form=app.screen
            form.query_one('#form-startup',Select).value=True
            await pilot.click('#form-save');await self.confirm(app,pilot)
            path=self.config/'sing-box.json'
            saved=path.read_text()
            self.assertEqual(form.query_one('#field-config',TextArea).text,saved)
            self.assertFalse(form.has_unsaved())
            await pilot.click('#form-save');await self.confirm(app,pilot)
            self.assertEqual(path.read_text(),saved)
            self.assertEqual(form.query_one('#field-config',TextArea).text,saved)

    async def test_system_views_are_read_only_and_refresh_from_backend(self):
        app=ServiceApp(model=service_manager.ServiceModel())
        with ExitStack() as stack:
            loaders=[]
            for name in ('state_policy','overlay_policy','watchdog_policy','power_status'):
                loaders.append(stack.enter_context(patch(name+'.details',return_value='Fixture system status')))
            async with app.run_test(size=(110,42)) as pilot:
                await self.settle(app,pilot)
                for name in ('system-state','system-overlay','system-watchdog','system-power'):
                    await self.select(app,pilot,name)
                    self.assertFalse(list(app.query('#toggle')))
                    await pilot.click('#configure');await self.settle(app,pilot)
                    self.assertIsInstance(app.screen,ReadOnlyScreen)
                    self.assertEqual([button.id for button in app.screen.query(Button)],['policy-refresh','policy-back'])
                    await pilot.click('#policy-refresh');await self.settle(app,pilot)
                    await pilot.click('#policy-back');await self.settle(app,pilot)
            for loader in loaders:self.assertEqual(loader.call_count,2)

    async def test_ota_bypass_is_explicit_cancelled_without_install_and_reset_after_success(self):
        app=ServiceApp(model=service_manager.ServiceModel())
        from ota_recovery import CONFIRMATION
        with patch('ota_update_config.save',return_value='Installed to inactive slot; reboot manually.') as install:
            async with app.run_test(size=(120,42)) as pilot:
                await self.settle(app,pilot);await self.select(app,pilot,'ota')
                self.assertFalse(list(app.query('#toggle')))
                await pilot.click('#configure');await self.settle(app,pilot)
                form=app.screen
                self.assertFalse(form.query_one('#field-skip_signature_verification',Select).value)
                labels='\n'.join(str(label.content) for label in form.query(Label))
                self.assertIn(f'type exactly `{CONFIRMATION}` (without backticks)',labels)
                form.query_one('#field-bundle',Input).value='/fixture/recovery.raucb'
                form.query_one('#field-skip_signature_verification',Select).value=True
                form.query_one('#field-confirmation',Input).value=CONFIRMATION
                await pilot.click('#form-save');await self.confirm(app,pilot,False)
                install.assert_not_called()
                await pilot.click('#form-save');await self.confirm(app,pilot)
                self.assertTrue(install.call_args.args[0]['skip_signature_verification'])
                self.assertEqual(install.call_args.args[0]['confirmation'],CONFIRMATION)
                self.assertFalse(form.query_one('#field-skip_signature_verification',Select).value)
                self.assertEqual(form.query_one('#field-confirmation',Input).value,'')

    async def test_ota_reports_progress_while_install_worker_is_running(self):
        app=ServiceApp(model=service_manager.ServiceModel())
        gate=threading.Event(); started=threading.Event(); self.addCleanup(gate.set)
        def save(values,report):
            report('RAUC: 61% - Copying image to rootfs.1')
            started.set(); gate.wait(5)
            return 'OTA installed.'
        with patch('ota_update_config.save',side_effect=save):
            async with app.run_test(size=(120,42)) as pilot:
                await self.settle(app,pilot);await self.select(app,pilot,'ota')
                await pilot.click('#configure');await self.settle(app,pilot)
                form=app.screen
                await pilot.click('#form-save');await pilot.pause()
                await pilot.click('#confirm-apply')
                for _ in range(30):
                    await pilot.pause(.05)
                    if started.is_set(): break
                self.assertTrue(started.is_set())
                self.assertTrue(form.busy)
                self.assertIn('RAUC: 61%',str(form.query_one('#form-result',Static).content))
                gate.set();await self.settle(app,pilot)
                self.assertFalse(form.busy)
                self.assertIn('OTA installed.',str(form.query_one('#form-result',Static).content))

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
        if 0 < os.getuid() < 1000:
            self.skipTest('Real ownership changes require root or an ordinary UID of at least 1000')
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
            self.assertIsInstance(app.screen,Details)
            await pilot.press('enter');await pilot.pause()
            await pilot.click('#ssh-save');await self.confirm(app,pilot)
            self.assertEqual((self.config/'ssh-user-ca.pub').read_text(),keys[1])
            self.assertEqual((home/'.ssh/authorized_keys').read_text(),'')

    async def test_ssh_table_arrows_open_details_and_reach_actions(self):
        row={'id':'one','role':'login','type':'ssh-ed25519','fingerprint':'SHA256:fixture','comment':'Fixture','restricted':False}
        editor=Mock();editor.records.return_value=[row];editor.changed.return_value=False
        app=ServiceApp(model=service_manager.ServiceModel())
        async with app.run_test(size=(110,36)) as pilot:
            await self.settle(app,pilot)
            app.push_screen(SSHScreen(model=editor));await self.settle(app,pilot)
            self.assertEqual(app.focused.id,'keys')
            await pilot.press('enter');await pilot.pause()
            self.assertIsInstance(app.screen,Details)
            self.assertIn('SHA256:fixture',app.screen.text)
            await pilot.press('enter','right');await pilot.pause()
            self.assertEqual(app.focused.id,'ssh-add-key')
            await pilot.press('right');await pilot.pause()
            self.assertEqual(app.focused.id,'ssh-add-ca')
