"""Recovery bypass on private files only, including real SquashFS extraction."""
from _stage_support import ROOT
from contextlib import ExitStack
import fcntl
import hashlib
import json
import os
import pty
import select
import struct
import sys
import termios
import time
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import bootstate
import ota_recovery as recovery
import update


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.control=self.root/'control'
        with self.control.open('wb') as stream:
            stream.truncate(2*bootstate.STRIDE)
            for bank in (0,1):stream.seek(bank*bootstate.STRIDE);stream.write(bootstate.encode(bootstate.initial()))
        self.before=bootstate.load(self.control);self.info={'layout':'ab-v2','boot_control':str(self.control)}
        self.bundle=self.root/'bundle';self.bundle.write_bytes(b'fixture bundle')
        self.payload=self.root/'rootfs.img';self.payload.write_bytes(b'fixture root filesystem'*100)
        self.size=self.payload.stat().st_size;self.digest=hashlib.sha256(self.payload.read_bytes()).hexdigest()
        self.target=self.root/'inactive';self.target.write_bytes(b'x'*self.size)
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(recovery.trial,'LOCK',self.root/'decision.lock'))

    def run_recovery(self):
        def opened(path,capacity):return os.open(path,os.O_RDWR)
        with patch.object(recovery,'locate',return_value=(self.target,'B',self.size)), \
             patch.object(recovery,'extract',return_value=(self.payload,'v-test',self.digest)), \
             patch.object(recovery,'open_target',side_effect=opened):
            recovery.install(self.bundle,self.info,'A',self.before,self.root)

    def test_success_writes_and_reads_back_before_arming_three_trials(self):
        original=os.pwrite
        with patch.object(recovery.os,'pwrite',side_effect=lambda fd,data,offset:original(fd,data[:37],offset)):
            self.run_recovery()
        self.assertEqual(self.target.read_bytes(),self.payload.read_bytes())
        state=bootstate.load(self.control)
        self.assertEqual((state['ne_confirmed'],state['ne_pending'],state['ne_left'],state['ne_B']),('A','B','3','1'))
        audit=json.loads(next((self.root/'ota-recovery').glob('*.json')).read_text())
        self.assertFalse(audit['signature_verified']);self.assertEqual(audit['status'],'pending-trial')

    def test_failed_write_or_readback_leaves_inactive_slot_invalid(self):
        for kind in ('write','readback'):
            with self.subTest(kind=kind):
                before=bootstate.load(self.control)
                self.before=before
                def failed(payload,fd,size,digest):
                    state=bootstate.load(self.control)
                    self.assertEqual(state['ne_B'],'0')
                    raise OSError('injected '+kind+' failure')
                with patch.object(recovery,'write_verified',side_effect=failed):
                    with self.assertRaises(OSError):self.run_recovery()
                state=bootstate.load(self.control)
                self.assertEqual((state['ne_confirmed'],state['ne_pending'],state['ne_B']),('A','none','0'))

    def test_write_verified_rejects_corrupt_readback_and_zero_writes(self):
        fd=os.open(self.target,os.O_RDWR);self.addCleanup(os.close,fd)
        with patch.object(recovery.os,'pwrite',return_value=0),self.assertRaises(OSError):
            recovery.write_verified(self.payload,fd,self.size,self.digest)
        with patch.object(recovery.os,'pread',return_value=b'x'*self.size),self.assertRaises(ValueError):
            recovery.write_verified(self.payload,fd,self.size,self.digest)

    def test_recovery_refuses_active_trial_and_nonblock_target(self):
        with self.assertRaises(ValueError):recovery.locate(self.info,'B',self.before)
        with self.assertRaises(ValueError):recovery.open_target(self.target,self.size)

    def test_target_capacity_and_readonly_are_checked_before_writes(self):
        from types import SimpleNamespace
        import stat
        for capacity,readonly in ((self.size+1,0),(self.size,1)):
            with self.subTest(capacity=capacity,readonly=readonly), \
                 patch.object(recovery.os,'fstat',return_value=SimpleNamespace(st_mode=stat.S_IFBLK)), \
                 patch.object(recovery.fcntl,'ioctl',side_effect=[struct.pack('Q',capacity),struct.pack('I',readonly)]), \
                 self.assertRaises(ValueError):
                recovery.open_target(self.target,self.size)
        self.assertEqual(self.target.read_bytes(),b'x'*self.size)

    def test_boot_state_changes_prevent_activation(self):
        def changed(payload,fd,size,digest):
            bootstate.update(self.control,lambda value:{**value,'ne_left':'1'})
        with patch.object(recovery,'write_verified',side_effect=changed),self.assertRaisesRegex(ValueError,'Boot state changed'):
            self.run_recovery()
        state=bootstate.load(self.control)
        self.assertEqual((state['ne_confirmed'],state['ne_pending'],state['ne_B']),('A','none','0'))

    def test_unsigned_flag_requires_local_path_and_exact_confirmation_before_commands(self):
        with patch.object(update.os,'geteuid',return_value=0),patch.object(update.subprocess,'run') as run:
            for bundle,phrase in ((None,recovery.CONFIRMATION),(self.bundle,''),(self.bundle,'yes'),
                                  (self.bundle,f'`{recovery.CONFIRMATION}`')):
                with self.subTest(bundle=bundle,phrase=phrase),self.assertRaises(ValueError):
                    update.install(bundle,{},skip_signature_verification=True,confirmation=phrase)
            run.assert_not_called()

    def test_authorized_recovery_never_calls_normal_rauc_install(self):
        (self.root/'config').mkdir();(self.root/'config/initialized').write_text('ready')
        with patch.object(update,'STATE',self.root),patch.object(update,'LOCK',self.root/'op.lock'), \
             patch.object(update.os,'geteuid',return_value=0), \
             patch.object(update,'platform',side_effect=[(self.info,'A',self.before),(self.info,'A',{**self.before,'ne_pending':'B','ne_left':'3'})]), \
             patch.object(recovery,'install') as rescue,patch.object(update.subprocess,'run') as normal,patch.object(update,'reboot_system') as reboot:
            update.install(self.bundle,{},skip_signature_verification=True,confirmation=recovery.CONFIRMATION,report=lambda text:None)
            rescue.assert_called_once();normal.assert_not_called();reboot.assert_not_called()
            staged=rescue.call_args.args[0];self.assertNotEqual(staged,self.bundle)

    def test_cli_flag_and_cancellation(self):
        parser=__import__('argparse').ArgumentParser();update.configure_parser(parser)
        args=parser.parse_args(['install',str(self.bundle),'--skip-signature-verification'])
        self.assertTrue(args.skip_signature_verification)
        with patch.object(update,'SOURCE',self.root/'source'),patch.object(update,'console_confirmation',return_value='cancel'),patch.object(update.os,'geteuid',return_value=0):
            (self.root/'source').write_text('{}')
            with self.assertRaises(SystemExit):update.run(args,parser)
            self.assertFalse((self.root/'update-cache').exists())

    def test_console_confirmation_on_a_real_private_terminal(self):
        code='''import sys,os
sys.path.insert(0,sys.argv[1]+'/tests')
import _stage_support,update,ota_recovery
os.geteuid=lambda:0
answer=update.console_confirmation('/fixture/recovery.raucb')
print('ACCEPTED' if answer==ota_recovery.CONFIRMATION else 'CANCELLED',flush=True)
'''
        for answer,expected in ((recovery.CONFIRMATION.encode()+b'\n',b'ACCEPTED'),(b'yes\n',b'CANCELLED'),
                                (f'`{recovery.CONFIRMATION}`\n'.encode(),b'CANCELLED'),(b'\x04',b'CANCELLED')):
            with self.subTest(answer=answer):
                master,slave=pty.openpty();process=None
                def controlling_terminal():
                    os.setsid()
                    fcntl.ioctl(slave,termios.TIOCSCTTY,0)
                try:
                    process=subprocess.Popen([sys.executable,'-c',code,str(ROOT)],stdin=slave,stdout=subprocess.PIPE,
                                             stderr=subprocess.STDOUT,preexec_fn=controlling_terminal)
                    prompt=b'';deadline=time.monotonic()+5
                    while b'to continue: ' not in prompt and time.monotonic()<deadline:
                        if select.select([master],[],[],0.1)[0]:prompt+=os.read(master,4096)
                        if process.poll() is not None:break
                    self.assertIn(f'Type exactly `{recovery.CONFIRMATION}` (without backticks) to continue: '.encode(),prompt)
                    os.write(master,answer)
                    output,_=process.communicate(timeout=5)
                    self.assertEqual(process.returncode,0,output);self.assertIn(expected,output)
                finally:
                    if process is not None and process.poll() is None:process.kill();process.communicate()
                    os.close(master);os.close(slave)


class RealRecoveryBundleTests(unittest.TestCase):
    def test_unsigned_plain_bundle_extracts_but_bad_hash_board_hooks_and_size_fail(self):
        needed=('mksquashfs','unsquashfs','mkfs.ext4','e2fsck','openssl','rauc')
        if not all(shutil.which(tool) for tool in needed):
            if os.environ.get('NETWORK_EXIT_OTA_TESTS')=='required':self.fail('Recovery bundle tools missing')
            self.skipTest('Recovery bundle tools missing')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);inputs=root/'inputs';inputs.mkdir();payload=inputs/'rootfs.img'
            with payload.open('wb') as stream:stream.truncate(4*1024*1024)
            subprocess.run(['mkfs.ext4','-q','-F',str(payload)],check=True,capture_output=True)
            size=payload.stat().st_size;digest=hashlib.sha256(payload.read_bytes()).hexdigest()
            for name in ('new','old'):
                subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(root/(name+'.key')),
                                '-out',str(root/(name+'.pem')),'-days','2','-subj','/CN='+name,
                                '-addext','basicConstraints=critical,CA:TRUE'],check=True,capture_output=True)
            for kind in ('valid','signed','hash','board','hooks','size'):
                text=f'[update]\ncompatible={recovery.COMPATIBLE}\nversion=v-test\n\n[bundle]\nformat=plain\n\n[image.rootfs]\nfilename=rootfs.img\nsize={size}\nsha256={digest}\n'
                if kind=='hash':text=text.replace(digest,'0'*64)
                elif kind=='board':text=text.replace(recovery.COMPATIBLE,'other-board')
                elif kind=='hooks':text+='\n[hooks]\nfilename=run-me\n'
                elif kind=='size':text=text.replace(f'size={size}',f'size={size+1}')
                (inputs/'manifest.raucm').write_text(text)
                bundle=root/(kind+'.raucb')
                if kind=='signed':
                    (inputs/'manifest.raucm').write_text(text.replace(f'size={size}\n','').replace(f'sha256={digest}\n',''))
                    built=subprocess.run(['rauc','bundle','--mksquashfs-args=-processors 1',f'--cert={root/"new.pem"}',
                                          f'--key={root/"new.key"}',str(inputs),str(bundle)],capture_output=True,text=True)
                    self.assertEqual(built.returncode,0,built.stdout+built.stderr)
                    check=subprocess.run(['rauc','info',f'--keyring={root/"old.pem"}',str(bundle)],capture_output=True)
                    self.assertNotEqual(check.returncode,0)
                else:
                    subprocess.run(['mksquashfs',str(inputs),str(bundle),'-noappend','-processors','1','-quiet'],check=True,capture_output=True)
                output=root/kind;output.mkdir()
                with self.subTest(kind=kind):
                    if kind in ('valid','signed'):
                        actual,version,sha=recovery.extract(bundle,output,size)
                        self.assertEqual(actual.read_bytes(),payload.read_bytes());self.assertEqual(sha,digest)
                    else:
                        with self.assertRaises(ValueError):recovery.extract(bundle,output,size)
