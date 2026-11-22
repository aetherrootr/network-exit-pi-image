from _stage_support import source_path, source_library, assemble_layers
"""Persistent CA trust and real certificate authentication over local pipes."""
from contextlib import ExitStack
import base64
import json
import os
from pathlib import Path
import pwd
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
import sshca
import sshpub


def generate(path, kind='ed25519'):
    subprocess.run(['ssh-keygen', '-q', '-t', kind, '-N', '', '-f', str(path)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


class SSHCATrustTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.config = self.root / 'config'
        self.config.mkdir()
        self.trust = self.config / sshca.FILENAME
        generate(self.root / 'ca')
        self.public = (self.root / 'ca.pub').read_text()
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(sshca.os, 'geteuid', return_value=0))
        stack.enter_context(patch.object(sshca.os.path, 'ismount', return_value=True))

    def test_initially_disabled_then_persistent_trust_and_multiple_ca_rotation(self):
        self.assertEqual(sshca.ensure(self.config).read_text(), '')
        first = sshca.replace(str(self.root / 'ca.pub'), self.config)
        self.assertEqual(len(first), 1)
        self.assertEqual(self.trust.read_text(), self.public)
        self.assertEqual(self.trust.stat().st_mode & 0o777, 0o600)
        # Boot/slot changes only ensure the existing STATE-backed file is present.
        sshca.ensure(self.config)
        self.assertEqual(self.trust.read_text(), self.public)
        generate(self.root / 'next-ca')
        combined = self.root / 'rotation.pub'
        combined.write_text(self.public + (self.root / 'next-ca.pub').read_text())
        self.assertEqual(len(sshca.replace(str(combined), self.config)), 2)
        self.assertEqual(self.trust.read_text(), combined.read_text())

    def test_invalid_private_or_certificate_input_preserves_previous_trust(self):
        sshca.replace(str(self.root / 'ca.pub'), self.config)
        before = self.trust.read_bytes()
        generate(self.root / 'user')
        subprocess.run(['ssh-keygen', '-q', '-s', str(self.root / 'ca'), '-I', 'fixture', '-n', 'pi',
                        '-V', '-5m:+1h', str(self.root / 'user.pub')], check=True)
        for file in ['ca', 'user-cert.pub']:
            with self.subTest(file=file), self.assertRaises(ValueError):
                sshca.replace(str(self.root / file), self.config)
            self.assertEqual(self.trust.read_bytes(), before)
        invalid = self.root / 'invalid.pub'
        invalid.write_text(self.public + 'ssh-ed25519 AAAA invalid\n')
        with self.assertRaises(ValueError): sshca.replace(str(invalid), self.config)
        self.assertEqual(self.trust.read_bytes(), before)

    def test_atomic_failure_and_link_rejection_do_not_change_trust(self):
        sshca.replace(str(self.root / 'ca.pub'), self.config)
        with patch('components.os.replace', side_effect=OSError('fixture write failure')):
            with self.assertRaises(OSError): sshca.replace(self.public, self.config)
        self.assertEqual(self.trust.read_text(), self.public)
        self.trust.unlink()
        self.trust.symlink_to(self.root / 'ca.pub')
        with self.assertRaises(ValueError): sshca.replace(self.public, self.config)
        self.trust.unlink()
        os.link(self.root / 'ca.pub', self.trust)
        with self.assertRaises(ValueError): sshca.replace(self.public, self.config)
        self.assertEqual((self.root / 'ca.pub').read_text(), self.public)


    def test_ca_configuration_refuses_unmounted_or_unprivileged_state(self):
        with patch.object(sshca.os.path, 'ismount', return_value=False):
            with self.assertRaises(ValueError): sshca.replace(self.public, self.config)
        with patch.object(sshca.os, 'geteuid', return_value=1000):
            with self.assertRaises(ValueError): sshca.ensure(self.config)
        self.assertFalse(self.trust.exists())

    def test_security_key_public_encodings_are_accepted_without_hardware_access(self):
        # Build public-only SK fixtures from real software key points. This tests
        # OpenSSH encoding support, not authenticator generation or hardware signing.
        def string(data): return struct.pack('>I', len(data)) + data
        for kind, sk in [('ed25519', 'sk-ssh-ed25519@openssh.com'),
                         ('ecdsa', 'sk-ecdsa-sha2-nistp256@openssh.com')]:
            path = self.root / ('software-' + kind)
            generate(path, kind)
            fields = path.with_suffix('.pub').read_text().split()
            blob = base64.b64decode(fields[1])
            first = int.from_bytes(blob[:4], 'big')
            sk_blob = string(sk.encode()) + blob[4 + first:] + string(b'ssh:')
            line = sk + ' ' + base64.b64encode(sk_blob).decode() + ' hardware-format-fixture'
            fingerprints = sshca.replace(line, self.config)
            self.assertEqual(len(fingerprints), 1)
            self.assertEqual(self.trust.read_text(), line + '\n')
            self.assertEqual(next(iter(sshpub.validated_keys(line)))[0], sk)


class RealSSHCertificateTests(unittest.TestCase):
    def setUp(self):
        self.sshd = os.environ.get('ENODECTL_TEST_SSHD') or shutil.which('sshd')
        missing = [name for name in ['ssh', 'ssh-keygen'] if not shutil.which(name)]
        if not self.sshd or missing:
            reason = f'Missing SSH certificate test tools: {missing}, sshd={self.sshd}'
            if os.environ.get('NETWORK_EXIT_SSH_CA_TESTS') == 'required': self.fail(reason)
            self.skipTest(reason)
        tmp = tempfile.TemporaryDirectory(prefix='enodectl-ssh-certificate-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        # Never use host PAM, a network listener, a real login shell or user rc.
        # Root-run CI drops the fixture sshd to the original user or nobody.
        username = os.environ.get('SUDO_USER', 'nobody') if os.geteuid() == 0 else pwd.getpwuid(os.getuid()).pw_name
        account = pwd.getpwnam(username)
        if account.pw_uid == 0: self.fail('Certificate fixture sshd must run unprivileged')
        self.user = username
        self.root.chmod(0o755)
        for key in ['ca', 'other-ca', 'host', 'client']:
            generate(self.root / key)
        if os.geteuid() == 0: os.chown(self.root / 'host', account.pw_uid, account.pw_gid)
        (self.root / 'trusted.pub').write_text((self.root / 'ca.pub').read_text())
        (self.root / 'principals').write_text('pi\n')
        (self.root / 'authorized_keys').write_text('')
        for name in ['trusted.pub', 'principals', 'authorized_keys']:
            (self.root / name).chmod(0o644)
        (self.root / 'known_hosts').write_text('fixture.invalid ' + (self.root / 'host.pub').read_text())
        policy = (source_path('etc/ssh/sshd_config.d/00-enodectl-access.conf')).read_text()
        policy = policy.replace('AllowUsers pi', f'AllowUsers {username}').replace('UsePAM yes', 'UsePAM no')
        policy = policy.replace('/etc/network-exit/ssh-user-ca.pub', str(self.root / 'trusted.pub'))
        # The host user substitutes for pi in this fixture. Its accepted certificate
        # principal stays pi; the production username policy is tested with sshd -T.
        policy = policy.replace('AuthorizedPrincipalsFile none', f'AuthorizedPrincipalsFile {self.root}/principals')
        (self.root / 'server.conf').write_text(policy + f'''
HostKey {self.root}/host
AuthorizedKeysFile {self.root}/authorized_keys
StrictModes no
PermitUserRC no
UseDNS no
LogLevel VERBOSE
''')
        (self.root / 'server.conf').chmod(0o644)
        proxy = self.root / 'proxy.py'
        proxy.write_text(f'''import os
if os.geteuid() == 0:
    os.setgroups([])
    os.setgid({account.pw_gid})
    os.setuid({account.pw_uid})
os.execv({self.sshd!r}, [{self.sshd!r}, '-i', '-e', '-f', {str(self.root / 'server.conf')!r}])
''')
        self.proxy = shlex.join([sys.executable, str(proxy)])

    def sign(self, name, ca='ca', principal='pi', validity='-5m:+2h', host=False, agent=None):
        public = self.root / (name + '.pub')
        shutil.copyfile(self.root / 'client.pub', public)
        command = ['ssh-keygen', '-q', '-s', str(self.root / (ca + '.pub' if agent else ca)), '-I', name,
                   '-V', validity]
        if principal is not None: command += ['-n', principal]
        if host: command += ['-h']
        if agent: command += ['-U']
        command.append(str(public))
        subprocess.run(command, check=True, capture_output=True,
                       env={**os.environ, **({'SSH_AUTH_SOCK': str(agent)} if agent else {})})
        return public.with_name(name + '-cert.pub')

    def authenticate(self, certificate=None, identity='client'):
        command = ['ssh', '-v', '-F', '/dev/null', '-o', 'ProxyCommand=' + self.proxy,
                   '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'IdentityAgent=none',
                   '-o', 'PreferredAuthentications=publickey', '-o', 'GlobalKnownHostsFile=/dev/null',
                   '-o', 'UserKnownHostsFile=' + str(self.root / 'known_hosts'), '-o', 'StrictHostKeyChecking=yes',
                   '-i', str(self.root / identity)]
        if certificate: command += ['-o', 'CertificateFile=' + str(certificate)]
        # A deliberately undefined subsystem closes immediately after authentication
        # without running the host account shell or creating an interactive session.
        command += ['-s', self.user + '@fixture.invalid', 'enodectl-undefined-fixture']
        result = subprocess.run(command, capture_output=True, text=True, timeout=10,
                                env={**os.environ, 'LC_ALL': 'C'})
        return 'Authenticated to fixture.invalid ' in result.stderr, result.stderr

    def test_valid_certificate_and_invalid_signer_principal_time_type_or_signature(self):
        good = self.sign('valid')
        accepted, log = self.authenticate(good)
        self.assertTrue(accepted, log)
        cases = [self.sign('wrong-ca', ca='other-ca'), self.sign('wrong-principal', principal='root'),
                 self.sign('no-principals', principal=None), self.sign('expired', validity='-2h:-1h'),
                 self.sign('future', validity='+1h:+2h'), self.sign('host-certificate', host=True)]
        fields = good.read_text().split()
        blob = bytearray(base64.b64decode(fields[1])); blob[-1] ^= 1
        forged = self.root / 'forged-cert.pub'
        forged.write_text(fields[0] + ' ' + base64.b64encode(blob).decode() + '\n')
        cases.append(forged)
        for certificate in cases:
            with self.subTest(certificate=certificate.name):
                accepted, log = self.authenticate(certificate)
                self.assertFalse(accepted, log)

    def test_same_public_key_can_be_ca_and_direct_login_and_ca_disable_keeps_raw_access(self):
        (self.root / 'authorized_keys').write_text((self.root / 'ca.pub').read_text())
        good = self.sign('signed-by-login-key')
        accepted, log = self.authenticate(good)
        self.assertTrue(accepted, log)
        accepted, log = self.authenticate(identity='ca')
        self.assertTrue(accepted, log)
        (self.root / 'trusted.pub').write_text('')
        accepted, log = self.authenticate(good)
        self.assertFalse(accepted, log)
        accepted, log = self.authenticate(identity='ca')
        self.assertTrue(accepted, log)

    def test_signing_through_an_agent_uses_only_the_ca_public_reference(self):
        if not shutil.which('ssh-agent') or not shutil.which('ssh-add'):
            self.fail('ssh-agent and ssh-add are required for the CA agent test')
        socket = self.root / 'agent.sock'
        agent = subprocess.Popen(['ssh-agent', '-D', '-a', str(socket)], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 3
            while not socket.exists() and agent.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(socket.exists(), 'Temporary ssh-agent did not start')
            subprocess.run(['ssh-add', str(self.root / 'ca')], env={**os.environ, 'SSH_AUTH_SOCK': str(socket)},
                           stdin=subprocess.DEVNULL, check=True, capture_output=True)
            accepted, log = self.authenticate(self.sign('agent-signed', agent=socket))
            self.assertTrue(accepted, log)
        finally:
            agent.terminate()
            agent.communicate(timeout=5)


if __name__ == '__main__': unittest.main()
