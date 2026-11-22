from _stage_support import source_path, source_library, assemble_layers
from contextlib import ExitStack, redirect_stdout
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
import sshpub


class RemoteKeys(io.BytesIO):
    def geturl(self):
        return 'https://keys.example.test/admin.keys'


class PublicKeyImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.fixture.cleanup)
        cls.keys = []
        for number in range(2):
            path = Path(cls.fixture.name) / f'key-{number}'
            subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(path)], check=True)
            cls.keys.append(path.with_suffix('.pub').read_text().strip())
        cls.private_key = (Path(cls.fixture.name) / 'key-0').read_text()

    def appliance(self, root):
        state = root / 'state'
        home = state / 'homes/pi'
        home.mkdir(parents=True)
        uid = os.getuid() if os.getuid() >= 1000 else 1000
        account = SimpleNamespace(pw_name='pi', pw_uid=uid, pw_gid=os.getgid(), pw_dir=str(home))
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(sshpub, 'STATE', state))
        stack.enter_context(patch.object(sshpub, 'HOMES', state / 'homes'))
        stack.enter_context(patch.object(sshpub.os, 'geteuid', return_value=0))
        stack.enter_context(patch.object(sshpub.os.path, 'ismount', return_value=True))
        stack.enter_context(patch.object(sshpub.pwd, 'getpwnam', return_value=account))
        stack.enter_context(redirect_stdout(io.StringIO()))
        return home

    def test_url_import_preserves_restrictions_and_deduplicates_changed_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self.appliance(Path(tmp))
            (home / '.ssh').mkdir()
            existing = 'from="100.64.0.0/10",no-agent-forwarding ' + self.keys[0] + " Alice's laptop\n"
            authorized = home / '.ssh/authorized_keys'
            authorized.write_text(existing)
            remote = RemoteKeys(('\n'.join(self.keys) + '\n').encode())
            opener = SimpleNamespace(open=Mock(return_value=remote))
            with patch.object(sshpub, 'build_opener', return_value=opener):
                fingerprints = sshpub.add('https://keys.example.test/admin.keys', 'pi')
            self.assertEqual(len(fingerprints), 1)
            self.assertEqual(authorized.read_text(), existing + self.keys[1] + '\n')
            self.assertEqual(opener.open.call_args.kwargs['timeout'], 20)
            self.assertEqual(authorized.stat().st_mode & 0o777, 0o600)
            self.assertEqual((home / '.ssh').stat().st_mode & 0o777, 0o700)
            again = self.keys[1].rsplit(' ', 1)[0] + ' different-comment'
            self.assertEqual(sshpub.add(again, 'pi'), [])
            self.assertEqual(authorized.read_text(), existing + self.keys[1] + '\n')

    def test_entire_batch_is_validated_before_existing_access_is_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = self.appliance(root)
            sshpub.add(self.keys[0], 'pi')
            authorized = home / '.ssh/authorized_keys'
            before = authorized.read_bytes()
            (root / 'bad.pub').write_text(self.keys[1] + '\nssh-ed25519 AAAA invalid\n')
            with self.assertRaises(ValueError): sshpub.add(str(root / 'bad.pub'), 'pi')
            self.assertEqual(authorized.read_bytes(), before)
            for source in [self.private_key, '<html>error</html>', 'command="id" ' + self.keys[0], '',
                           self.keys[0] + '\x1b[31m']:
                with self.subTest(source=source[:20]), self.assertRaises(ValueError):
                    sshpub.validated_keys(source)

    def test_symlinks_and_hardlinks_cannot_redirect_privileged_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = self.appliance(root)
            victim = root / 'victim'
            victim.write_text('leave intact\n')
            (home / '.ssh').symlink_to(root, target_is_directory=True)
            with self.assertRaises(OSError): sshpub.add(self.keys[0], 'pi')
            (home / '.ssh').unlink()
            (home / '.ssh').mkdir()
            authorized = home / '.ssh/authorized_keys'
            authorized.symlink_to(victim)
            with self.assertRaises(OSError): sshpub.add(self.keys[0], 'pi')
            authorized.unlink()
            os.link(victim, authorized)
            with self.assertRaises(ValueError): sshpub.add(self.keys[0], 'pi')
            self.assertEqual(victim.read_text(), 'leave intact\n')

    def test_failed_replace_preserves_existing_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self.appliance(Path(tmp))
            sshpub.add(self.keys[0], 'pi')
            authorized = home / '.ssh/authorized_keys'
            before = authorized.read_bytes()
            with patch.object(sshpub.os, 'replace', side_effect=OSError('simulated write failure')):
                with self.assertRaises(OSError): sshpub.add(self.keys[1], 'pi')
            self.assertEqual(authorized.read_bytes(), before)
            self.assertEqual([p.name for p in authorized.parent.iterdir()], ['authorized_keys'])

    def test_https_only_redirects_bounded_download_and_required_state(self):
        for url in ['http://example.test/admin.keys', 'https://user:password@example.test/keys',
                    'file:///tmp/keys', 'https://example.test/keys\nInjected:yes']:
            with self.subTest(url=url), self.assertRaises(ValueError): sshpub.https_url(url)
        with self.assertRaises(ValueError):
            sshpub.HTTPSRedirects().redirect_request(None, None, 302, '', {}, 'http://example.test/keys')
        opener = SimpleNamespace(open=Mock(return_value=RemoteKeys(b'x' * (sshpub.MAX_SOURCE + 1))))
        with patch.object(sshpub, 'build_opener', return_value=opener):
            with self.assertRaisesRegex(ValueError, '64 KiB'): sshpub.read_source('https://example.test/keys')
        with patch.object(sshpub.os, 'geteuid', return_value=0), \
                patch.object(sshpub.os.path, 'ismount', return_value=False), \
                patch.object(sshpub, 'read_source') as source:
            with self.assertRaisesRegex(ValueError, 'persistent STATE'): sshpub.add('https://example.test/keys', 'pi')
            source.assert_not_called()
