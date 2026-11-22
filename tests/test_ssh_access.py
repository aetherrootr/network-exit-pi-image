from _stage_support import source_path, source_library, assemble_layers
"""SSH menu invariants and failure-safe updates using only temporary homes."""
from contextlib import ExitStack
import fcntl
import io
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
import ssh_access
from enodectl_core.textual_ui import display
import sshpub
import sshca




class SSHAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys_dir = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.keys_dir.cleanup)
        cls.keys = []
        for index in range(3):
            path = Path(cls.keys_dir.name) / f'key-{index}'
            subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(path)], check=True)
            cls.keys.append(path.with_suffix('.pub').read_text())

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='enodectl-ssh-menu-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = self.root / 'state'
        self.home = self.state / 'homes/pi'
        self.home.mkdir(parents=True)
        self.config = self.state / 'config'
        self.config.mkdir()
        self.ca = self.config / sshca.FILENAME
        self.raw = self.home / '.ssh/authorized_keys'
        self.uid = os.getuid() if os.getuid() >= 1000 else 1000
        account = SimpleNamespace(pw_name='pi', pw_uid=self.uid, pw_gid=os.getgid(), pw_dir=str(self.home))
        stack = ExitStack()
        self.addCleanup(stack.close)
        for target, name, value in [(sshpub, 'STATE', self.state), (sshpub, 'HOMES', self.state / 'homes')]:
            stack.enter_context(patch.object(target, name, value))
        stack.enter_context(patch.object(sshpub.os, 'geteuid', return_value=0))
        stack.enter_context(patch.object(sshpub.os.path, 'ismount', return_value=True))
        stack.enter_context(patch.object(sshpub.pwd, 'getpwnam', return_value=account))
        self.store = ssh_access.Store(self.config)

    def seed(self, login='', ca=''):
        self.raw.parent.mkdir(exist_ok=True)
        self.raw.write_text(login)
        self.ca.write_text(ca)

    def test_empty_or_invalid_configuration_cannot_be_saved(self):
        self.seed('# no login keys\n', '# no CA keys\nnot-a-public-key\n')
        draft = self.store.load()
        self.assertEqual(draft.counts(), {'login': 0, 'ca': 0})
        self.assertEqual(draft.inventory()[1], 1)
        with self.assertRaisesRegex(ValueError, 'at least one'): self.store.commit(draft)
        self.assertEqual(self.raw.read_text(), '# no login keys\n')

    def test_last_key_or_ca_is_protected_and_same_key_roles_are_separate(self):
        for role in ['login', 'ca']:
            self.seed(self.keys[0] if role == 'login' else '', self.keys[0] if role == 'ca' else '')
            draft = self.store.load()
            entry, = draft.inventory()[0]
            before = dict(draft.text)
            with self.assertRaisesRegex(ValueError, 'last SSH'): draft.remove(role, entry.identity)
            self.assertEqual(draft.text, before)
        self.seed(self.keys[0], self.keys[0])
        draft = self.store.load()
        first, second = draft.inventory()[0]
        self.assertEqual(first.fingerprint, second.fingerprint)
        draft.remove('ca', second.identity)
        self.store.commit(draft)
        self.assertEqual(self.raw.read_text(), self.keys[0])
        self.assertEqual(self.ca.read_text(), '')
        with self.assertRaises(ValueError): draft.remove('login', first.identity)

    def test_options_comments_duplicates_and_unrecognized_lines_are_preserved(self):
        restricted = 'from="192.0.2.1",no-agent-forwarding ' + self.keys[0].strip() + " Alice's laptop\n"
        original = '# administrator note\n' + restricted + restricted + 'unrecognized legacy line\n'
        self.seed(original)
        draft = self.store.load()
        entries, unknown = draft.inventory()
        self.assertEqual((len(entries), unknown), (1, 1))
        self.assertEqual(len(entries[0].lines), 2)
        self.assertTrue(entries[0].restricted)
        self.assertIn("Alice's laptop", entries[0].comment)
        self.assertEqual(draft.add('login', self.keys[0]), 0)
        draft.add('login', self.keys[1])
        self.store.commit(draft)
        self.assertEqual(self.raw.read_text(), original + self.keys[1])
        draft.remove('login', entries[0].identity)
        self.store.commit(draft)
        self.assertEqual(self.raw.read_text(), '# administrator note\nunrecognized legacy line\n' + self.keys[1])
        self.assertEqual(self.raw.stat().st_uid, self.uid)
        self.assertEqual(self.raw.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.raw.parent.stat().st_mode & 0o777, 0o700)

    def test_staged_edits_do_not_touch_files_and_invalid_batch_is_rejected(self):
        self.seed(self.keys[0])
        draft = self.store.load()
        draft.add('ca', self.keys[1])
        self.assertEqual(self.ca.read_text(), '')
        before = dict(draft.text)
        path = self.root / 'bad.pub'
        path.write_text(self.keys[2] + 'ssh-ed25519 AAAA invalid\n')
        with self.assertRaises(ValueError): draft.add('ca', str(path))
        self.assertEqual(draft.text, before)
        self.assertEqual(self.raw.read_text(), self.keys[0])

    def test_stale_menu_cannot_overwrite_another_menu_save(self):
        self.seed(self.keys[0])
        draft = self.store.load()
        draft.add('ca', self.keys[1])
        other = self.store.load()
        other.add('login', self.keys[2])
        self.store.commit(other)
        with self.assertRaisesRegex(ValueError, 'another session'): self.store.commit(draft)
        self.assertEqual(self.raw.read_text(), self.keys[0] + self.keys[2])
        self.assertEqual(self.ca.read_text(), '')

    def test_commit_rechecks_the_invariant_even_if_a_caller_bypasses_menu_removal(self):
        self.seed(self.keys[0])
        draft = self.store.load()
        draft.text = {'login': '', 'ca': ''}
        with self.assertRaisesRegex(ValueError, 'at least one'): self.store.commit(draft)
        self.assertEqual(self.raw.read_text(), self.keys[0])

    def test_replacement_is_written_before_old_authorization_is_removed(self):
        # Failure on the second write must leave both the old and the new access.
        self.seed(self.keys[0])
        draft = self.store.load()
        draft.add('ca', self.keys[1])
        draft.remove('login', draft.inventory()[0][0].identity)
        with patch.object(sshpub, 'write_authorized', side_effect=OSError('fixture failure')):
            with self.assertRaises(OSError): self.store.commit(draft)
        self.assertEqual(self.ca.read_text(), self.keys[1])
        self.assertEqual(self.raw.read_text(), self.keys[0])
        self.seed('', self.keys[0])
        draft = self.store.load()
        draft.add('login', self.keys[1])
        ca_entry = next(entry for entry in draft.inventory()[0] if entry.role == 'ca')
        draft.remove('ca', ca_entry.identity)
        with patch.object(ssh_access, 'save', side_effect=OSError('fixture failure')):
            with self.assertRaises(OSError): self.store.commit(draft)
        self.assertEqual(self.raw.read_text(), self.keys[1])
        self.assertEqual(self.ca.read_text(), self.keys[0])

    def test_symlinked_trust_is_rejected_without_following_it(self):
        self.seed(self.keys[0])
        self.ca.unlink()
        victim = self.root / 'victim'
        victim.write_text('unchanged')
        self.ca.symlink_to(victim)
        with self.assertRaises(ValueError): self.store.load()
        self.assertEqual(victim.read_text(), 'unchanged')




    def test_display_never_emits_terminal_controls_or_non_ascii_comments(self):
        shown = display('label\x1b[31m\n\u65e5', multiline=False)
        shown.encode('ascii')
        self.assertNotIn('\x1b', shown)
        self.assertNotIn('\n', shown)
        self.assertIn('\\x1b', shown)



if __name__ == '__main__': unittest.main()
