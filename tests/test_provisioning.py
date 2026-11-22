from _stage_support import source_path, source_library, assemble_layers
import importlib.util
import struct
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('provision_burn',ROOT/'release/lib/burn.py')
burn=importlib.util.module_from_spec(spec);spec.loader.exec_module(burn)


class PiProvisioningTests(unittest.TestCase):
    def test_failed_provisioning_detaches_its_temporary_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            card=Path(tmp)/'card-fixture'
            mbr=bytearray(512);mbr[510:]=b'\x55\xaa';mbr[498]=0x83
            start=8192+2*4092*2048+2048;size=2048*2048
            struct.pack_into('<II',mbr,502,start,size)
            card.write_bytes(mbr)
            with patch.object(burn,'inspect_device'),patch.object(burn.subprocess,'check_output',return_value='/dev/loop999\n'), \
                    patch.object(burn,'personalize_state',side_effect=ValueError('simulated failure')),patch.object(burn.subprocess,'run') as command:
                with self.assertRaises(ValueError):burn.provision_ssh_key(str(card),{'layout':'ab-v2','image_bytes':(start+size)*512},'fixture')
                command.assert_called_once_with(['losetup','--detach','/dev/loop999'],check=True)

    def test_offline_key_provisioning_preserves_ext4_uuid_and_sets_pi_ownership(self):
        tools=['mkfs.ext4','debugfs','e2fsck','ssh-keygen','losetup','blkid']
        if not all(shutil.which(x) for x in tools):self.skipTest('Provisioning tools are unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);key=root/'login-key';image=root/'state.img'
            subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True)
            content=burn.read_ssh_keys(key.with_suffix('.pub'))
            with image.open('wb') as stream:stream.truncate(32*1024*1024)
            subprocess.run(['mkfs.ext4','-q','-F',str(image)],check=True)
            uuid=['blkid','-p','-s','UUID','-o','value',str(image)]
            before=subprocess.check_output(uuid)
            burn.personalize_state(image,content)
            self.assertEqual(subprocess.check_output(uuid),before)
            result=subprocess.run(['debugfs','-R','cat /homes/pi/.ssh/authorized_keys',str(image)],check=True,capture_output=True,text=True)
            self.assertEqual(result.stdout,content)
            with self.assertRaises(ValueError):burn.read_ssh_keys(key)
