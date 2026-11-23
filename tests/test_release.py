from _stage_support import source_path, source_library, assemble_layers
import hashlib
import importlib.util
import json
import lzma
import fcntl
import os
import pty
import select
import sys
import termios
import time
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from _factory_support import component_fixture

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('burn', ROOT / 'release/lib/burn.py')
burn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(burn)


class FlashSafetyTests(unittest.TestCase):
    def test_reject_partition_small_readonly_and_system_disks(self):
        valid = {'type': 'disk', 'name': '/dev/fake-card', 'size': 10000, 'ro': False, 'mountpoints': [None]}
        burn.validate_disk(valid, 1000)
        cases = [{**valid, 'type': 'part'}, {**valid, 'size': 10}, {**valid, 'ro': True},
                 {**valid, 'children': [{'name': '/dev/fake-card1', 'mountpoints': ['/']}]},
                 {**valid, 'children': [{'name': '/dev/fake-card1', 'mountpoints': ['[SWAP]']}]},
                 {**valid, 'children': [{'name': '/dev/fake-card1', 'children': [
                     {'name': '/dev/mapper/root', 'mountpoints': ['/home']}]}]}]
        for node in cases:
            with self.subTest(node=node), self.assertRaises(ValueError):
                burn.validate_disk(node, 1000)

    def test_corrupt_or_changed_image_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / 'a.img.xz'
            raw = b'x' * 4096
            image.write_bytes(lzma.compress(raw))
            manifest = {'sha256': hashlib.sha256(image.read_bytes()).hexdigest(), 'image_bytes': len(raw),
                        'image_sha256': hashlib.sha256(raw).hexdigest()}
            burn.verify_image(image, manifest)
            with self.assertRaises(ValueError):
                burn.verify_image(image, {**manifest, 'image_bytes': len(raw) - 1})
            image.write_bytes(image.read_bytes() + b'corrupt')
            with self.assertRaises(ValueError):
                burn.verify_image(image, manifest)

    def test_cli_requires_exact_erase_confirmation_on_a_real_terminal(self):
        # Exercise /dev/tty, not stdin or an in-memory seekable stream. The child
        # has a private controlling PTY; every device operation is stubbed.
        child_code = r"""
import importlib.util
from pathlib import Path
import sys
spec = importlib.util.spec_from_file_location('burn', sys.argv[1])
burn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(burn)
burn.read_manifest = lambda directory: ({'image_bytes': 512}, None)
burn.inspect_device = lambda *args: {'size': 1024, 'model': 'fixture'}
burn.verify_image = lambda *args: None
burn.os.geteuid = lambda: 0
burn.write_image = lambda *args: print('FIXTURE_WRITE_CALLED', flush=True)
burn.subprocess.run = lambda *args, **kwargs: None
sys.argv = ['burn.py', '/dev/null']
burn.main()
"""
        for answer, accepted in [(b'ERASE /dev/null\n', True), (b'ERASE\n', False),
                                 (b'`ERASE /dev/null`\n', False), (b'/dev/null\n', False),
                                 (b'ERASE /dev/other\n', False), (b'\x04', False)]:
            with self.subTest(answer=answer):
                master, slave = pty.openpty()
                def controlling_terminal():
                    os.setsid()
                    fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
                process = None
                try:
                    process = subprocess.Popen([sys.executable, '-c', child_code,
                                                str(ROOT / 'release/lib/burn.py')],
                                               stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                               preexec_fn=controlling_terminal)
                    os.close(slave)
                    slave = None
                    prompt = b''
                    deadline = time.monotonic() + 5
                    while b'to confirm: ' not in prompt and time.monotonic() < deadline:
                        readable, _, _ = select.select([master], [], [], 0.1)
                        if readable:
                            try: chunk = os.read(master, 4096)
                            except OSError: break
                            if not chunk: break
                            prompt += chunk
                        if process.poll() is not None: break
                    self.assertIn(b'Type exactly `ERASE /dev/null` (without backticks) to confirm: ', prompt)
                    os.write(master, answer)
                    output, _ = process.communicate(timeout=5)
                    self.assertEqual(process.returncode, 0 if accepted else 1, output.decode())
                    self.assertEqual(b'FIXTURE_WRITE_CALLED' in output, accepted, output.decode())
                    if not accepted: self.assertIn(b'Cancelled. Nothing was written', output)
                finally:
                    if process is not None and process.poll() is None:
                        process.kill()
                        process.communicate()
                    os.close(master)
                    if slave is not None: os.close(slave)

    def test_writer_handles_short_writes_and_reads_back(self):
        # A disposable regular file tests the internal writer; CLI rejects regular files.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = b'write-test\0' * 1000
            image = root / 'image.img.xz'
            image.write_bytes(lzma.compress(raw))
            card = root / 'disposable-card-fixture'
            card.write_bytes(b'\0' * (len(raw) + 512))
            manifest = {'image_bytes': len(raw), 'image_sha256': hashlib.sha256(raw).hexdigest()}
            actual_write = burn.os.write
            with patch.object(burn.os, 'write', side_effect=lambda fd, data: actual_write(fd, data[:37])):
                burn.write_image(image, str(card), manifest)
            self.assertEqual(card.read_bytes()[:len(raw)], raw)


class ReleasePackageTests(unittest.TestCase):
    def test_real_archive_extract_verify_and_executable_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            for folder in ['image_builder', 'release', 'stage']:
                shutil.copytree(ROOT / folder, project / folder, ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copy2(ROOT/'package-release.py',project/'package-release.py')
            (project / 'output').mkdir()
            _, expected, raw = component_fixture(project / 'output')
            (project / 'README.md').write_text('Fixture instructions\n')
            (project / 'output/build.json').write_text(json.dumps({'version':'v0-test', 'layout':'ab-v2'}))
            shutil.copy2(ROOT / 'LICENSE', project / 'LICENSE')
            for name in ['package-manifest.tsv', 'source-image.sha256', 'enodectl-manifest.json']:
                (project / 'output' / name).write_text('fixture\n')
            subprocess.run(['python3', str(project / 'package-release.py'), '--version', 'v0-test', '--factory-only'], check=True, capture_output=True)
            archive = project / 'output/network-exit-factory-v0-test-orangepizero3.tar.gz'
            unpack = project / 'unpack'
            with tarfile.open(archive) as stream:
                stream.extractall(unpack, filter='data')
            bundle = unpack / 'network-exit-factory-v0-test-orangepizero3'
            self.assertTrue((bundle / 'burn.sh').stat().st_mode & 0o111)
            subprocess.run([str(bundle / 'burn.sh'), '--verify-only'], check=True, capture_output=True)
            subprocess.run(['sha256sum', '-c', 'SHA256SUMS'], cwd=bundle, check=True, capture_output=True)
            data = json.loads((bundle / 'release.json').read_text())
            self.assertEqual(data['format'], 2)
            self.assertEqual(data['image_bytes'], expected['image_bytes'])
            self.assertEqual(len(list((bundle / 'payload').glob('*rootfs*'))), 1)
            # Exercise the actual writer on a disposable prefilled regular file.
            # Its CLI continues to reject regular files and never sees a card.
            target = project / 'card-fixture'
            target.write_bytes(b'\xa5' * (expected['image_bytes'] + 512))
            burn.write_image(bundle, str(target), data)
            with target.open('rb') as stream:
                for write in data['writes']:
                    self.assertEqual(stream.tell(), write['offset'])
                    self.assertEqual(stream.read(len(raw[write['payload']])), raw[write['payload']])
                self.assertEqual(stream.read(), b'\xa5' * 512)
            with archive.open('rb') as stream:
                self.assertTrue(archive.with_suffix('.gz.sha256').read_text().startswith(hashlib.file_digest(stream, 'sha256').hexdigest()))
