from _stage_support import source_path, source_library, assemble_layers
import importlib.util
import json
import lzma
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('factory', ROOT / 'image_builder/assemble_factory.py')
factory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(factory)


@unittest.skipUnless(shutil.which('sfdisk') and shutil.which('mkfs.ext4'), 'Factory tools are not installed')
class FactoryLayoutTests(unittest.TestCase):
    def test_real_component_build_stores_rootfs_once_and_preserves_boot_and_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = bytearray(4 * 1024 * 1024)
            prefix[8192:8209] = b'BOOTLOADER-BYTES!'
            prefix[440:444] = struct.pack('<I', 0x1234abcd)
            prefix[510:512] = b'\x55\xaa'
            (root / 'base').write_bytes(prefix)
            payload = b'system-slot'.ljust(1024 * 1024, b'\0')
            (root / 'rootfs.img').write_bytes(payload)
            directory = root / 'components'
            factory.prepare_components(root / 'base', root / 'rootfs.img', directory, state_mib=8)
            originals = {name: (directory / f'{name}.img').read_bytes()
                         for name in ('boot-prefix', 'boot-control', 'state')}
            manifest = factory.compress_components(directory, root / 'rootfs.img')
            self.assertEqual(len(list((directory / 'payload').iterdir())), 4)
            self.assertEqual(set(p.name for p in directory.iterdir()), {'payload', 'layout.json'})
            self.assertEqual(lzma.decompress((directory / 'payload/rootfs.img.xz').read_bytes()), payload)
            revised = lzma.decompress((directory / 'payload/boot-prefix.img.xz').read_bytes())
            self.assertEqual(revised[:446], prefix[:446])
            self.assertEqual(revised[510:], prefix[510:])
            self.assertEqual([w['offset'] for w in manifest['writes']],
                             [0, 4*1024*1024, 5*1024*1024, 6*1024*1024, 7*1024*1024])
            control = originals['boot-control']
            for bank in (0, 1):
                offset = bank * factory.bootstate.STRIDE
                self.assertEqual(factory.bootstate.decode(control[offset:offset+factory.bootstate.SIZE]), factory.bootstate.initial())
            for name, raw in originals.items():
                self.assertEqual(lzma.decompress((directory / f'payload/{name}.img.xz').read_bytes()), raw)
            state = root / 'state.ext4'
            state.write_bytes(originals['state'])
            subprocess.run(['e2fsck', '-fn', str(state)], check=True, capture_output=True)

    def test_real_partition_table_preserves_bootloader_and_seeds_both_slots(self):
        # Only regular temporary files; no loop device, mount or SD card is used.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = bytearray(b'boot-prefix-fixture' * (4 * 1024 * 1024 // 19 + 1))[:4 * 1024 * 1024]
            prefix[440:444] = struct.pack('<I', 0x1234abcd)
            prefix[444:446] = b'\0\0'  # Reserved MBR bytes are zero in the inspected base.
            prefix[510:512] = b'\x55\xaa'
            (root / 'base').write_bytes(prefix)
            payload = b'system-slot' * 100 + b'\0' * (1024 * 1024 - 1100)
            (root / 'root').write_bytes(payload)
            total = factory.assemble(root / 'base', root / 'root', root / 'disk', state_mib=8)
            table = json.loads(subprocess.check_output(['sfdisk', '--json', str(root / 'disk')]))['partitiontable']
            self.assertEqual(table['id'], '0x1234abcd')
            self.assertEqual([p['start'] for p in table['partitions']], [8192, 10240, 12288, 14336])
            disk = (root / 'disk').read_bytes()
            self.assertEqual(len(disk), total)
            self.assertEqual(disk[:446], prefix[:446])
            self.assertEqual(disk[510:4*1024*1024], prefix[510:])
            for offset in (4*1024*1024, 5*1024*1024):
                self.assertEqual(disk[offset:offset+len(payload)], payload)
            for bank in (0, 1):
                offset = 6*1024*1024 + bank*factory.bootstate.STRIDE
                self.assertEqual(factory.bootstate.decode(disk[offset:offset+factory.bootstate.SIZE]), factory.bootstate.initial())
            with self.assertRaises(ValueError):
                factory.assemble(root / 'base', root / 'root', root / 'disk')
