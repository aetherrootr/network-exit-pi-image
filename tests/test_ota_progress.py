"""OTA progress checks with disposable files, processes and mocked RAUC state."""
from _stage_support import ROOT
from contextlib import ExitStack
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import update


class OTAProgressTests(unittest.TestCase):
    def test_download_reports_bytes_and_percent_without_changing_verification(self):
        payload = b'x' * (5 * 1024 * 1024)
        asset = {'size': len(payload), 'url': 'https://example.test/bundle'}
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'update.raucb'
            messages = []
            with patch.object(update, 'urlopen', return_value=io.BytesIO(payload)):
                update.download(asset, output, messages.append)
            self.assertEqual(output.read_bytes(), payload)
            self.assertEqual(output.stat().st_mode & 0o777, 0o400)
            self.assertIn('Downloading OTA: 0%', messages[0])
            self.assertTrue(any('Downloading OTA: 80%' in line for line in messages))
            self.assertTrue(any('Downloading OTA: 100%' in line for line in messages))
            self.assertEqual(messages[-1], 'OTA download complete.')

    def test_download_short_response_never_reports_completion(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(update, 'urlopen', return_value=io.BytesIO(b'x')):
            messages = []
            with self.assertRaisesRegex(ValueError, 'Incomplete OTA download'):
                update.download({'size': 2, 'url': 'https://example.test/bundle'}, Path(tmp)/'update.raucb', messages.append)
            self.assertNotIn('OTA download complete.', messages)

    def test_rauc_progress_uses_actual_installer_property_only_during_install(self):
        def result(value): return subprocess.CompletedProcess([], 0, stdout=json.dumps({'data': value}))
        with patch.object(update.subprocess, 'run', side_effect=[result('installing'), result([61, 'Copying image to rootfs.1', 3])]):
            self.assertEqual(update.rauc_progress(), 'RAUC: 61% - Copying image to rootfs.1')
        with patch.object(update.subprocess, 'run', return_value=result('idle')):
            self.assertIsNone(update.rauc_progress())
        with patch.object(update.subprocess, 'run', side_effect=[result('installing'), result([101, 'Invalid', 0])]):
            self.assertIsNone(update.rauc_progress())
        with patch.object(update.subprocess, 'run', side_effect=OSError('D-Bus not available')):
            self.assertIsNone(update.rauc_progress())

    def test_subprocess_reports_progress_without_terminating_running_child(self):
        messages = []
        with patch.object(update, 'PROGRESS_INTERVAL', 0.05):
            update.run_reported([sys.executable, '-c', 'import time; time.sleep(.25)'],
                                'Installing OTA', messages.append, lambda: 'RAUC: 61% - Copying image')
        self.assertEqual(messages[0], 'Installing OTA...')
        self.assertIn('RAUC: 61% - Copying image', messages[1:])

    def test_subprocess_verification_heartbeat_and_real_error(self):
        messages = []
        with patch.object(update, 'PROGRESS_INTERVAL', 0.05):
            update.run_reported([sys.executable, '-c', 'import time; time.sleep(.2)'], 'Verifying', messages.append)
        self.assertTrue(any('still running' in line for line in messages))
        with self.assertRaisesRegex(ValueError, 'signature rejected'):
            update.run_reported([sys.executable, '-c', 'import sys; print("signature rejected",file=sys.stderr); sys.exit(2)'],
                                'Verifying', messages.append)

    def test_signed_local_install_preserves_private_copy_and_activation_checks(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp); state = root/'state'; (state/'config').mkdir(parents=True)
            (state/'config/initialized').touch(); trust = root/'trust';trust.touch()
            bundle = root/'bundle.raucb'; bundle.write_bytes(b'x' * (5 * 1048576))
            before = {'ne_pending':'none','ne_left':'0'}; after = {'ne_pending':'B','ne_left':'3'}
            for name,value in [('STATE',state),('TRUST',trust),('LOCK',root/'lock')]:
                stack.enter_context(patch.object(update,name,value))
            stack.enter_context(patch.object(update.os,'geteuid',return_value=0))
            stack.enter_context(patch.object(update,'platform',side_effect=[({},'A',before),({},'A',after)]))
            commands=[]; messages=[]
            def run(command,phase,report,progress=None):
                staged=Path(command[-1]);self.assertNotEqual(staged,bundle)
                self.assertEqual(staged.read_bytes(),bundle.read_bytes())
                self.assertEqual(staged.stat().st_mode & 0o777,0o400)
                commands.append(command[:2]);report(phase)
            stack.enter_context(patch.object(update,'run_reported',side_effect=run))
            update.install(bundle,{},report=messages.append)
            self.assertEqual(commands,[['rauc','info'],['rauc','install']])
            self.assertTrue(any('Staging local OTA: 100%' in line for line in messages))
            self.assertTrue(any('Installed to slot B' in line for line in messages))
            self.assertFalse(list((state/'update-cache').iterdir()))

    def test_signature_failure_does_not_start_installation(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root=Path(tmp); state=root/'state'; (state/'config').mkdir(parents=True)
            (state/'config/initialized').touch(); trust=root/'trust';trust.touch()
            bundle=root/'bundle.raucb';bundle.write_bytes(b'fixture')
            for name,value in [('STATE',state),('TRUST',trust),('LOCK',root/'lock')]:
                stack.enter_context(patch.object(update,name,value))
            stack.enter_context(patch.object(update.os,'geteuid',return_value=0))
            stack.enter_context(patch.object(update,'platform',return_value=({},'A',{'ne_pending':'none','ne_left':'0'})))
            run=stack.enter_context(patch.object(update,'run_reported',side_effect=ValueError('signature rejected')))
            with self.assertRaisesRegex(ValueError,'signature rejected'):
                update.install(bundle,{},report=lambda message:None)
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0][:2],['rauc','info'])
            self.assertFalse(list((state/'update-cache').iterdir()))
