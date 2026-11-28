from _stage_support import source_path, source_library, assemble_layers
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('upgrade', str(source_path('usr/lib/network-exit/update.py')))
spec = importlib.util.spec_from_loader(loader.name, loader)
upgrade = importlib.util.module_from_spec(spec)
loader.exec_module(upgrade)
SOURCE = json.loads((ROOT / 'stage/ota/config/update-source.json').read_text())


class OperatorUpdateTests(unittest.TestCase):
    def test_default_only_checks_without_installing_or_rebooting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'source').write_text(json.dumps(SOURCE))
            (root/'release').write_text(json.dumps({'version':'v1'}))
            with patch.object(upgrade,'SOURCE',root/'source'), patch.object(upgrade,'RELEASE',root/'release'), \
                    patch.object(sys,'argv',['enodectl update']), patch.object(upgrade,'latest',return_value={'version':'v2'}), \
                    patch.object(upgrade.subprocess,'run') as command, patch.object(upgrade.subprocess,'Popen') as spawn, \
                    patch('sys.stdout',new=io.StringIO()) as output:
                upgrade.main()
                command.assert_not_called()
                spawn.assert_not_called()
                self.assertIn('Update available',output.getvalue())

    def test_latest_rejects_factory_assets_and_external_downloads(self):
        version='v1.2.3'
        name=SOURCE['asset_prefix']+version+SOURCE['asset_suffix']
        release={'tag_name':version,'assets':[{'name':name,'size':123,'browser_download_url':
                 f'https://github.com/{SOURCE["repository"]}/releases/download/{version}/{name}'}]}
        with patch.object(upgrade,'urlopen',return_value=io.BytesIO(json.dumps(release).encode())):
            self.assertEqual(upgrade.latest(SOURCE)['name'],name)
        release['assets'][0]['browser_download_url']='https://example.invalid/malicious.raucb'
        with patch.object(upgrade,'urlopen',return_value=io.BytesIO(json.dumps(release).encode())):
            with self.assertRaises(ValueError): upgrade.latest(SOURCE)
        release['assets'][0]['name']='network-exit-factory-v1.tar.gz'
        with patch.object(upgrade,'urlopen',return_value=io.BytesIO(json.dumps(release).encode())):
            with self.assertRaises(ValueError): upgrade.latest(SOURCE)

    def test_invalid_signature_never_reaches_install_or_reboot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'config').mkdir();(root/'config/initialized').write_text('1')
            (root/'bundle').write_bytes(b'unsigned fixture');(root/'cert').write_text('fixture')
            state={'ne_pending':'none','ne_left':'0'}
            with patch.object(upgrade,'STATE',root),patch.object(upgrade,'TRUST',root/'cert'), \
                    patch.object(upgrade,'platform',return_value=({},'A',state)), \
                    patch.object(upgrade.os,'geteuid',return_value=0), \
                    patch.object(upgrade.subprocess,'Popen') as spawn, patch.object(upgrade,'reboot_system') as reboot, \
                    patch.object(upgrade.fcntl,'flock'), patch.object(upgrade,'LOCK',root/'lock',create=True):
                process=spawn.return_value.__enter__.return_value
                process.communicate.return_value=('Invalid bundle signature',None)
                process.returncode=1
                with self.assertRaisesRegex(ValueError,'Invalid bundle signature'):
                    upgrade.install(root/'bundle',SOURCE,reboot=True,report=lambda message:None)
                spawn.assert_called_once()
                self.assertEqual(spawn.call_args.args[0][:2],['rauc','info'])
                reboot.assert_not_called()
                self.assertFalse(list((root/'update-cache').iterdir()))

    def test_last_trial_cannot_overwrite_the_confirmed_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'config').mkdir(); (root/'config/initialized').write_text('1')
            with patch.object(upgrade,'STATE',root), patch.object(upgrade,'LOCK',root/'lock'), \
                    patch.object(upgrade.os,'geteuid',return_value=0), \
                    patch.object(upgrade,'platform',return_value=({},'B',{'ne_pending':'B','ne_left':'0'})), \
                    patch.object(upgrade.subprocess,'run') as command, patch.object(upgrade.subprocess,'Popen') as spawn:
                with self.assertRaisesRegex(ValueError,'already pending'):
                    upgrade.install(None,SOURCE,retry_failed=True)
                command.assert_not_called()
                spawn.assert_not_called()

    def test_no_update_timer_or_service_is_shipped(self):
        for path in (ROOT/'stage').glob('*/rootfs_layer/etc/systemd/**/*'):
            if path.is_file():
                self.assertNotIn('enodectl update install',path.read_text())
