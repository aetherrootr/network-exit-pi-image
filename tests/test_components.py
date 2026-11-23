"""Configuration completion and transaction failures, without host account changes."""
from _stage_support import source_path
from contextlib import ExitStack, nullcontext
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import components
import service_manager
import service_config
import initialize
import setup_state
import singbox_setup


class InitializationTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.config=self.root/'config';self.config.mkdir()
        stack=ExitStack();self.addCleanup(stack.close)
        for target,name,value in [(components,'CONFIG',self.config),(components,'STATE',self.root),
                                  (initialize,'CONFIG',self.config),(initialize,'STATE',self.root),
                                  (setup_state,'RUNTIME_READY',self.root/'run/ready')]:
            stack.enter_context(patch.object(target,name,value))

    def test_finish_setup_requires_accounts_keys_and_control_authorization(self):
        for failing in ('account','required','key'):
            with self.subTest(failing=failing), \
                    patch('accounts.check_account_policy', side_effect=ValueError('account') if failing=='account' else None), \
                    patch('accounts.retain_admin_accounts'), \
                    patch.object(service_manager.registry,'hooks', side_effect=lambda key: [Mock(side_effect=ValueError(failing))] if (key=='required_check' and failing=='required') or (key=='before_complete' and failing=='key') else []), \
                    patch.object(service_manager,'ensure_management') as start:
                with self.assertRaises(ValueError):service_manager.finish_setup()
                self.assertFalse((self.config/'initialized').exists())
                start.assert_not_called()

    def test_service_failures_leave_setup_incomplete_and_block_ota(self):
        import update
        setup_state.complete(self.config)
        with patch('accounts.check_account_policy'), patch('accounts.retain_admin_accounts'), \
                patch.object(service_manager.registry,'hooks',return_value=[]), \
                patch.object(service_manager,'ensure_management'), \
                patch.object(components,'set_enabled',side_effect=subprocess.CalledProcessError(1,['systemctl'])):
            with self.assertRaises(subprocess.CalledProcessError):service_manager.finish_setup()
        self.assertFalse((self.config/'initialized').exists())
        with patch.object(update,'STATE',self.root),patch.object(update,'LOCK',self.root/'ota.lock'), \
                patch.object(update.os,'geteuid',return_value=0),patch.object(update,'platform',return_value=({},'A',{})):
            with self.assertRaisesRegex(ValueError,'`sudo enodectl config`'):update.install(None,{})

    def test_successful_setup_persists_mandatory_services_after_all_operations(self):
        operations=[]
        def enable(name,on):
            self.assertFalse((self.config/'initialized').exists())
            operations.append((name,on))
        with patch('accounts.check_account_policy'),patch('accounts.retain_admin_accounts'), \
                patch.object(service_manager.registry,'hooks',return_value=[]),patch.object(service_manager,'ensure_management'), \
                patch.object(components,'set_enabled',side_effect=enable):service_manager.finish_setup()
        self.assertTrue((self.config/'initialized').exists())
        self.assertEqual([n for n,on in operations],list(components.UNITS))

    def test_profile_publish_failure_restores_files_and_invalidates_completion(self):
        path=self.config/'cloudflare-token';path.write_text('old-token\n')
        components.save(self.config/'components.json',json.dumps({'cloudflared':True}))
        setup_state.complete(self.config)
        with patch.object(components,'validate'),patch.object(service_config.subprocess,'run',side_effect=subprocess.CalledProcessError(1,['systemctl'])):
            with self.assertRaisesRegex(ValueError,'previous files restored'):service_config.publish('cloudflared',{path:'new-token\n'})
        self.assertEqual(path.read_text(),'old-token\n')
        self.assertFalse((self.config/'initialized').exists())

    def test_firstboot_account_prompt_precedes_textual_and_cancel_does_not_finish(self):
        calls=[]
        with patch.object(initialize,'configure_accounts',side_effect=lambda *a:calls.append('accounts')), \
                patch.object(initialize.sys.stdin,'isatty',return_value=True),patch.object(initialize.sys.stdout,'isatty',return_value=True), \
                patch.object(service_manager,'ensure_management',side_effect=lambda:calls.append('required services')), \
                patch('service_tui.ServiceApp') as app:
            app.return_value.run.return_value=False
            with self.assertRaisesRegex(ValueError,'incomplete'):initialize.initialize()
            self.assertEqual(calls,['accounts','required services'])
            self.assertFalse((self.config/'initialized').exists())

    def test_service_command_rejects_unprivileged_or_unmounted_state(self):
        for uid,mounted in ((1000,True),(0,False)):
            with patch('os.geteuid',return_value=uid),patch('os.path.ismount',return_value=mounted):
                with self.assertRaises(ValueError):service_manager.open_services()

    def test_private_proxy_has_unique_credentials(self):
        self.assertNotEqual(singbox_setup.private_proxy('127.0.0.1'),singbox_setup.private_proxy('127.0.0.1'))
        with self.assertRaises(ValueError):singbox_setup.private_proxy('0.0.0.0')
