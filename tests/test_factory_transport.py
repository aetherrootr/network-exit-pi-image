"""Shared-rootfs factory safety and actual regular-file reconstruction."""
import copy
import hashlib
import importlib.util
import json
import lzma
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from _factory_support import component_fixture
from _stage_support import ROOT
from image_builder import package_release
from release.lib import factory

spec = importlib.util.spec_from_file_location('component_burn', ROOT / 'release/lib/burn.py')
burn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(burn)


class FactoryTransportTests(unittest.TestCase):
    def test_malformed_or_incomplete_write_maps_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory, original, _ = component_fixture(tmp)
            changes = [
                lambda m: m.update(image_bytes=m['image_bytes'] + 512),
                lambda m: m['writes'].__setitem__(2, {'payload': 'rootfs', 'offset': 0}),
                lambda m: m['writes'].pop(),
                lambda m: m['writes'].append({'payload': 'state', 'offset': m['image_bytes']}),
                lambda m: m['writes'][2].update(payload='state'),
                lambda m: m['payloads']['rootfs'].update(file='../outside.img.xz'),
                lambda m: m['payloads']['rootfs'].update(bytes=True),
                lambda m: m['payloads']['rootfs'].update(bytes=4093 * factory.MIB),
                lambda m: m['payloads']['state'].update(bytes=2 ** 41),
                lambda m: m['payloads']['boot-control'].update(bytes=512),
                lambda m: m['payloads']['rootfs'].update(raw_sha256='invalid'),
                lambda m: m['payloads'].pop('state'),
            ]
            for index, change in enumerate(changes):
                manifest = copy.deepcopy(original)
                change(manifest)
                with self.subTest(case=index), self.assertRaises(ValueError):
                    factory.verify(directory, manifest)

    def test_corrupt_missing_and_symlinked_payloads_rejected_before_opening_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory, manifest, _ = component_fixture(tmp)
            path = directory / manifest['payloads']['rootfs']['file']
            original = path.read_bytes()
            path.write_bytes(original[:-5] + b'broken')
            with patch.object(burn.os, 'open') as device_open:
                with self.assertRaisesRegex(ValueError, 'SHA256'):
                    burn.verify_image(directory, manifest)
                device_open.assert_not_called()
            path.unlink()
            with self.assertRaises(FileNotFoundError):
                factory.verify(directory, manifest)
            outside = Path(tmp) / 'outside'
            outside.write_bytes(original)
            path.symlink_to(outside)
            with self.assertRaisesRegex(ValueError, 'regular file'):
                factory.verify(directory, manifest)

    def test_raw_hash_size_and_partition_table_are_independently_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory, manifest, raw = component_fixture(tmp)
            wrong = copy.deepcopy(manifest)
            wrong['payloads']['rootfs']['raw_sha256'] = '0' * 64
            with self.assertRaisesRegex(ValueError, 'decompressed size or SHA256'):
                factory.verify(directory, wrong)
            item = {**manifest['payloads']['rootfs'], 'bytes': len(raw['rootfs']) - 1}
            with self.assertRaisesRegex(ValueError, 'exceeds'):
                list(factory.chunks(directory, item))
            prefix = bytearray(raw['boot-prefix'])
            prefix[454] ^= 1  # First partition start LBA; both checksums remain valid.
            compressed = lzma.compress(prefix)
            item = manifest['payloads']['boot-prefix']
            (directory / item['file']).write_bytes(compressed)
            item.update(sha256=hashlib.sha256(compressed).hexdigest(), raw_sha256=hashlib.sha256(prefix).hexdigest())
            with self.assertRaisesRegex(ValueError, 'MBR differs'):
                factory.verify(directory, manifest)

    def test_short_writes_and_independent_second_slot_readback(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory, manifest, raw = component_fixture(tmp)
            target = Path(tmp) / 'card-fixture'
            target.write_bytes(b'\xaa' * manifest['image_bytes'])
            actual_write = burn.os.write
            with patch.object(burn.os, 'write', side_effect=lambda fd, data: actual_write(fd, data[:8191])):
                burn.write_image(directory, str(target), manifest)
            with target.open('rb') as stream:
                for write in manifest['writes']:
                    self.assertEqual(stream.read(len(raw[write['payload']])), raw[write['payload']])
            # Corrupt only B after fsync; A still verifies, B must fail readback.
            actual_fsync = burn.os.fsync
            def corrupt_b(descriptor):
                actual_fsync(descriptor)
                os.pwrite(descriptor, b'!', manifest['writes'][2]['offset'])
            with patch.object(burn.os, 'fsync', side_effect=corrupt_b):
                with self.assertRaisesRegex(ValueError, 'readback verification failed for rootfs'):
                    burn.write_image(directory, str(target), manifest)

    def test_changed_rootfs_between_a_and_b_stops_the_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory, manifest, raw = component_fixture(tmp)
            target = Path(tmp) / 'card-fixture'
            target.write_bytes(b'\xaa' * manifest['image_bytes'])
            original_chunks, root_passes = burn.factory.chunks, 0
            def change_second_root(directory, item):
                nonlocal root_passes
                if item is manifest['payloads']['rootfs']:
                    root_passes += 1
                    if root_passes == 2:
                        (directory / item['file']).write_bytes(lzma.compress(b'changed'))
                yield from original_chunks(directory, item)
            with patch.object(burn.factory, 'chunks', side_effect=change_second_root):
                with self.assertRaisesRegex(ValueError, 'Compressed factory payload SHA256'):
                    burn.write_image(directory, str(target), manifest)
            with target.open('rb') as stream:
                stream.seek(manifest['writes'][1]['offset'])
                self.assertEqual(stream.read(len(raw['rootfs'])), raw['rootfs'])
                stream.seek(manifest['writes'][2]['offset'])
                self.assertEqual(stream.read(512), b'\xaa' * 512)

    def test_cli_rejects_regular_file_even_with_valid_components(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'not-a-card'
            path.write_bytes(b'fixture')
            with self.assertRaisesRegex(ValueError, 'block device'):
                burn.inspect_device(str(path), 512)

    def test_release_asset_size_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'asset'
            for size in (31, 32, 33):
                path.write_bytes(b'x' * size)
                if size < 32:
                    package_release.validate_asset_size(path, limit=32)
                else:
                    with self.assertRaisesRegex(ValueError, 'must be smaller'):
                        package_release.validate_asset_size(path, limit=32)

    def test_factory_ota_mismatch_fails_before_staging_or_signing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            component_fixture(root / 'output')
            (root / 'output/rootfs.img').write_bytes(b'different OTA rootfs')
            certificate = root / 'certificate'
            certificate.write_bytes(b'fixture certificate')
            (root / 'output/build.json').write_text(json.dumps({
                'version': 'v1-test', 'layout': 'ab-v2',
                'ota_certificate_sha256': hashlib.sha256(certificate.read_bytes()).hexdigest()}))
            with self.assertRaisesRegex(ValueError, 'Factory and OTA rootfs payloads differ'):
                package_release.package(root, 'v1-test', certificate, root / 'key')
            self.assertFalse((root / 'output/bundle').exists())
