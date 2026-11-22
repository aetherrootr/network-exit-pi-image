from _stage_support import source_path, source_library, assemble_layers
import importlib.util
import json
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
