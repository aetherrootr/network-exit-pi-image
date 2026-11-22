from _stage_support import source_path, source_library, assemble_layers
from contextlib import ExitStack, redirect_stdout
import io
import json
import runpy
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT / 'tests'))
import accounts
import sshpub


class AccountPolicyTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.etc=self.root/'etc';self.config=self.root/'config'
        self.etc.mkdir();self.config.mkdir()
        (self.etc/'passwd').write_text('root:x:0:0:root:/root:/bin/bash\npi:x:1000:1000:pi:/home/pi:/bin/bash\nservice:x:998:998:service:/nonexistent:/usr/sbin/nologin\n')
        (self.etc/'shadow').write_text('root:!:1:0:99999:7:::\npi:!:1:0:99999:7:::\nservice:*:1:0:99999:7:::\n')
        (self.etc/'group').write_text('root:x:0:\npi:x:1000:\nsudo:x:27:pi\nservice:x:998:\n')
        (self.etc/'gshadow').write_text('root:*::\npi:*::\nsudo:*::pi\nservice:*::\n')
        (self.etc/'enodectl-sudoers.d').mkdir()
        (self.etc/'enodectl-sudoers.d/99-bootstrap').write_text('fixture')
        stack=ExitStack();self.addCleanup(stack.close)
        stack.enter_context(patch.object(accounts,'ETC',self.etc))
        stack.enter_context(patch.object(accounts,'CONFIG',self.config))
        stack.enter_context(patch.object(accounts,'INITIALIZATION_LOCK',self.root/'init.lock'))
        stack.enter_context(redirect_stdout(io.StringIO()))

    def password_command(self,command,**kwargs):
        path=self.etc/'shadow'
        if command == ['passwd', '--lock', 'pi']:
            fields=[line.split(':') for line in path.read_text().splitlines()]
            for row in fields:
                if row[0]=='pi': row[1]='!'+row[1]
            path.write_text('\n'.join(':'.join(row) for row in fields)+'\n')
        else:
            self.assertEqual(command,['passwd','root'])
            path.write_text(path.read_text().replace('root:!:', 'root:$6$per-device-test-hash:'))
        return subprocess.CompletedProcess(command,0)

    def test_only_root_password_is_requested_and_pi_remains_key_only(self):
        with patch.object(accounts.subprocess,'run',side_effect=self.password_command) as run:
            accounts.configure_accounts()
            self.assertEqual([x.args[0] for x in run.call_args_list],[['passwd','root']])
        stored=(self.config/'accounts/shadow').read_text()
        self.assertIn('pi:!:',stored);self.assertIn('root:$6$',stored)
        self.assertNotIn('service:',stored)
        self.assertEqual((self.config/'accounts/shadow').stat().st_mode&0o777,0o600)
        self.assertTrue((self.config/'accounts-ready').exists())
        self.assertFalse((self.etc/'enodectl-sudoers.d/99-bootstrap').exists())
        with patch.object(accounts.subprocess,'run') as run:
            accounts.configure_accounts();run.assert_not_called()

    def test_interrupted_root_password_setup_keeps_pi_locked_and_does_not_claim_ready(self):
        with patch.object(accounts.subprocess,'run',side_effect=subprocess.CalledProcessError(1,['passwd','root'])):
            with self.assertRaises(subprocess.CalledProcessError):accounts.configure_accounts()
        self.assertIn('pi:!:',(self.config/'accounts/shadow').read_text())
        self.assertFalse((self.config/'accounts-ready').exists())

    def test_boot_locks_restored_pi_password_and_persists_it_without_changing_root(self):
        shadow=self.etc/'shadow'
        shadow.write_text(shadow.read_text().replace('pi:!:', 'pi:$6$old-password:').replace('root:!:', 'root:$6$recovery:'))
        (self.config/'accounts-ready').write_text('1\n')
        with patch.object(accounts.subprocess,'run',side_effect=self.password_command) as run:
            accounts.apply_policy()
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0],['passwd','--lock','pi'])
        stored=(self.config/'accounts/shadow').read_text()
        self.assertIn('pi:!$6$old-password:',stored)
        self.assertIn('root:$6$recovery:',stored)
        self.assertTrue((self.config/'accounts-ready').exists())
        self.assertFalse((self.etc/'enodectl-sudoers.d/99-bootstrap').exists())
        with patch.object(accounts.subprocess,'run') as run:
            accounts.apply_policy();run.assert_not_called()

    def test_failed_pi_lock_is_not_accepted_as_a_passwordless_account(self):
        shadow=self.etc/'shadow'
        shadow.write_text(shadow.read_text().replace('pi:!:', 'pi::'))
        with patch.object(accounts.subprocess,'run',return_value=subprocess.CompletedProcess([],0)):
            with self.assertRaisesRegex(ValueError,'Cannot disable pi'):
                accounts.configure_accounts()
        self.assertFalse((self.config/'accounts-ready').exists())

    def test_initialization_lock_and_root_key_rejection(self):
        with accounts.initialization_lock():
            with self.assertRaisesRegex(ValueError,'another session'):
                with accounts.initialization_lock():pass
        self.assertEqual(sshpub.default_user(),'pi')
        with patch.object(sshpub.os,'geteuid',return_value=0),patch.object(sshpub.os.path,'ismount',return_value=True):
            with self.assertRaisesRegex(ValueError,'root SSH is disabled'):sshpub.append_keys({},'root')

    def test_sudoers_syntax_with_real_parser(self):
        if not shutil.which('visudo'):self.skipTest('visudo is unavailable')
        subprocess.run(['visudo','-cf',str(source_path('usr/share/network-exit/sudoers'))],check=True,capture_output=True)

    def test_real_sudo_parser_grants_pi_passwordless_administration(self):
        if not shutil.which('cvtsudoers'):self.skipTest('cvtsudoers is unavailable')
        parsed=json.loads(subprocess.check_output(['cvtsudoers','-f','JSON',str(source_path('usr/share/network-exit/sudoers'))],text=True))
        pi,=[entry for entry in parsed['User_Specs'] if {'username':'pi'} in entry['User_List']]
        specification,=pi['Cmnd_Specs']
        self.assertIn({'authenticate':False},specification['Options'])
        self.assertIn({'command':'ALL'},specification['Commands'])
        self.assertIn({'username':'ALL'},specification['runasusers'])

    def test_su_guard_requires_ordinary_users_to_elevate_through_sudo(self):
        path=str(source_path('usr/lib/network-exit/su-guard'))
        with patch('os.geteuid',return_value=1000),patch('os.execv') as execute:
            with self.assertRaisesRegex(SystemExit,'sudo -i'):runpy.run_path(path)
            execute.assert_not_called()
        with patch('os.geteuid',return_value=0),patch('os.execv') as execute,patch('sys.argv',['su','-s','/bin/sh','daemon']):
            runpy.run_path(path)
            execute.assert_called_once_with('/usr/lib/network-exit/su.real',['/usr/lib/network-exit/su.real','-s','/bin/sh','daemon'])

    def test_ssh_effective_policy_disables_root_and_allows_pi(self):
        if not shutil.which('sshd') or not shutil.which('ssh-keygen'):self.skipTest('SSH tools are unavailable')
        key=self.root/'host-key'
        subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True)
        for user in ['pi','root']:
            result=subprocess.run(['sshd','-T','-f',str(source_path('etc/ssh/sshd_config.d/00-enodectl-access.conf')),
                                   '-h',str(key),'-C',f'user={user},host=localhost,addr=127.0.0.1'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('permitrootlogin no\n',result.stdout)
            for expected in ['allowusers pi', 'denyusers root', 'pubkeyauthentication yes',
                             'passwordauthentication no', 'kbdinteractiveauthentication no',
                             'permitemptypasswords no', 'authenticationmethods publickey', 'usepam yes',
                             'trustedusercakeys /etc/network-exit/ssh-user-ca.pub',
                             'authorizedprincipalsfile none', 'authorizedprincipalscommand none']:
                self.assertIn(expected+'\n',result.stdout)
            effective=dict(line.split(' ',1) for line in result.stdout.splitlines())
            for algorithm in ['sk-ssh-ed25519@openssh.com', 'sk-ecdsa-sha2-nistp256@openssh.com']:
                self.assertIn(algorithm,effective['casignaturealgorithms'].split(','))
                self.assertIn(algorithm,effective['pubkeyacceptedalgorithms'].split(','))
