from _stage_support import source_path, source_library, assemble_layers
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from _factory_support import component_fixture

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('packager', ROOT / 'image_builder/package_release.py')
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


class SignedBundleTests(unittest.TestCase):
    def test_real_signed_bundle_and_wrong_signer_rejection(self):
        missing = [name for name in ['rauc', 'mksquashfs', 'openssl'] if not shutil.which(name)]
        if missing:
            message = 'Missing OTA test tools: ' + ', '.join(missing)
            if os.environ.get('NETWORK_EXIT_OTA_TESTS') == 'required': self.fail(message)
            self.skipTest(message)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'output').mkdir();shutil.copytree(ROOT/'release',root/'release')
            helper = Path('stage/system-access/enodectl_component/sshpub.py')
            (root/helper).parent.mkdir(parents=True)
            shutil.copy2(ROOT/helper, root/helper)
            for name in ['README.md','LICENSE']: shutil.copy2(ROOT/name,root/name)
            raw = b'root-slot-fixture\0'.ljust(1024 * 1024, b'\0')
            component_fixture(root / 'output', raw)
            (root/'output/rootfs.img').write_bytes(raw)
            for name in ['package-manifest.tsv','source-image.sha256','enodectl-manifest.json']: (root/'output'/name).write_text('fixture\n')
            for identity in ['signer','untrusted']:
                subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes',
                                '-keyout',str(root/f'{identity}.key'),'-out',str(root/f'{identity}.pem'),
                                '-days','2','-subj',f'/CN={identity}', '-addext','basicConstraints=critical,CA:TRUE'],
                               check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            (root/'output/build.json').write_text(json.dumps({'version':'v1-test','layout':'ab-v2',
                'ota_certificate_sha256':hashlib.sha256((root/'signer.pem').read_bytes()).hexdigest()}))
            # An accidental signing identity change must fail before producing a package.
            with self.assertRaisesRegex(ValueError,'trust certificate'):
                packager.package(root,'v1-test',root/'untrusted.pem',root/'untrusted.key')
            products=packager.package(root,'v1-test',root/'signer.pem',root/'signer.key')
            self.assertEqual(len(products),2)
            bundle=products[1]
            valid=subprocess.run(['rauc','info',f'--keyring={root/"signer.pem"}',str(bundle)],capture_output=True,text=True)
            self.assertEqual(valid.returncode,0,valid.stdout+valid.stderr)
            wrong=subprocess.run(['rauc','info',f'--keyring={root/"untrusted.pem"}',str(bundle)],capture_output=True,text=True)
            self.assertNotEqual(wrong.returncode,0)
            self.assertTrue(bundle.with_name(bundle.name+'.sha256').exists())
