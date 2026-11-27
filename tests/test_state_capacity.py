from _stage_support import source_path, source_library, assemble_layers
import copy
import importlib.util
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('capacity', source_path('usr/lib/network-exit/state_capacity.py'))
capacity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capacity)
UUID = '1234abcd-04'


def factory_table():
    root = capacity.ROOT_SECTORS
    control = 8192 + 2 * root
    return {'label': 'dos', 'id': '0x1234abcd', 'sectorsize': 512, 'partitions': [
        {'start': 8192, 'size': root, 'type': '83'},
        {'start': 8192 + root, 'size': root, 'type': '83'},
        {'start': control, 'size': 2048, 'type': '83'},
        {'start': control + 2048, 'size': capacity.MIN_STATE_SECTORS, 'type': '83'}]}


class StateCapacityTests(unittest.TestCase):
    def test_state_mount_matches_kernel_device_even_with_stale_initramfs_source(self):
        device = os.makedev(179, 4)
        mount = {'source': '/root/dev/mmcblk0p4', 'fstype': 'ext4', 'maj:min': '179:4'}
        capacity.validate_mount([mount], device)
        for invalid in ([], [mount, mount], [{**mount, 'maj:min': '179:3'}],
                        [{**mount, 'fstype': 'tmpfs'}], [{'fstype': 'ext4'}]):
            with self.assertRaises(ValueError): capacity.validate_mount(invalid, device)

    def test_boot_prefix_allows_chs_normalization_but_only_state_lba_size_changes(self):
        before = bytearray(1024)
        before[510:512] = b'\x55\xaa'
        after = before.copy()
        for index in range(4):
            offset = 446 + 16 * index
            after[offset+1:offset+4] = b'\x03\xd0\xff'
            after[offset+5:offset+8] = b'\x03\xd0\xff'
        after[506:510] = b'\x01\x02\x03\x04'
        capacity.validate_boot_prefix(before, after)
        for offset in (0, 440, 446, 450, 454, 458, 502, 510, 600):
            invalid = after.copy()
            invalid[offset] ^= 1
            with self.assertRaises(ValueError): capacity.validate_boot_prefix(before, invalid)

    def test_rejects_small_cards_or_unexpected_layout_without_writing(self):
        original = factory_table()
        minimum = (original['partitions'][3]['start'] + capacity.MIN_STATE_SECTORS) * 512
        self.assertEqual(capacity.validate_layout(original, minimum, UUID), 0)
        for card in [16_000_000_000, 32_000_000_000, 128_000_000_000, 256_000_000_000]:
            self.assertEqual(capacity.validate_layout(original, card, UUID), (card - minimum) // 512)
        with self.assertRaises(ValueError): capacity.validate_layout(original, minimum - 512, UUID)
        with self.assertRaises(ValueError): capacity.validate_layout(original, (2**32 + 1) * 512, UUID)
        for mutate in [lambda x: x.update(id='0x00000000'), lambda x: x.update(label='gpt'),
                       lambda x: x['partitions'][0].update(size=1),
                       lambda x: x['partitions'][3].update(size=1),
                       lambda x: x['partitions'].reverse(), lambda x: x['partitions'].pop()]:
            malformed = copy.deepcopy(original)
            mutate(malformed)
            with self.assertRaises(ValueError): capacity.validate_layout(malformed, minimum, UUID)

    def test_filesystem_resize_retries_after_partition_was_already_expanded(self):
        layout = factory_table()
        size = layout['partitions'][3]['size'] * 512
        commands = []
        def run(command, **kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, stdout=str(size))
        with patch.object(capacity.os, 'geteuid', return_value=0), \
                patch.object(capacity, 'discover', return_value=(Path('/dev/fake-card'), Path('/dev/fake-state'), 16_000_000_000)), \
                patch.object(capacity, 'grow_partition', return_value=False), \
                patch.object(capacity, 'table', return_value=layout), \
                patch.object(capacity.subprocess, 'run', side_effect=run), \
                patch.object(capacity, 'resize_filesystem') as resize:
            capacity.grow(UUID)
        resize.assert_called_once_with(Path('/dev/fake-state'))
        self.assertFalse(any(command[0] in ('mkfs', 'mkfs.ext4') for command in commands))

    def test_online_resize_uses_a_private_mount_namespace(self):
        from types import SimpleNamespace
        device = Path('/dev/fake-state')
        with patch.object(Path, 'stat', return_value=SimpleNamespace(st_rdev=os.makedev(179, 4))), \
                patch.object(capacity.tempfile, 'TemporaryDirectory') as directory, \
                patch.object(capacity.subprocess, 'run') as run:
            directory.return_value.__enter__.return_value = '/run/test-state'
            capacity.resize_filesystem(device)
        args = run.call_args.args[0]
        self.assertEqual(args[:5], ['unshare', '--mount', '--propagation', 'private', '--'])
        self.assertIn('resize2fs "$1"', args[7])
        self.assertEqual(args[-3:], ['/dev/fake-state', '/run/test-state', '179:4'])

    def test_real_ext4_growth_preserves_identity_and_file_contents(self):
        # Offline regular-file test. Online resizing remains a board acceptance test.
        tools = ['mkfs.ext4', 'debugfs', 'e2fsck', 'resize2fs', 'blkid']
        if not all(shutil.which(name) for name in tools):
            self.skipTest('Ext4 test tools are not installed')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image, identity = root / 'state.img', root / 'key.pub'
            identity.write_text('persistent device identity fixture\n')
            with image.open('wb') as stream: stream.truncate(32 * 1024 * 1024)
            subprocess.run(['mkfs.ext4', '-q', '-F', str(image)], check=True)
            subprocess.run(['debugfs', '-w', '-R', f'write {identity} /authorized_keys', str(image)],
                           check=True, capture_output=True)
            uuid_command = ['blkid', '-p', '-s', 'UUID', '-o', 'value', str(image)]
            before_uuid = subprocess.check_output(uuid_command)
            with image.open('r+b') as stream: stream.truncate(128 * 1024 * 1024)
            fsck = subprocess.run(['e2fsck', '-pf', str(image)], capture_output=True)
            self.assertIn(fsck.returncode, [0, 1], fsck.stderr)
            subprocess.run(['resize2fs', str(image)], check=True, capture_output=True)
            self.assertEqual(subprocess.check_output(uuid_command), before_uuid)
            contents = subprocess.run(['debugfs', '-R', 'cat /authorized_keys', str(image)],
                                      check=True, capture_output=True, text=True).stdout
            self.assertEqual(contents, identity.read_text())

    def test_real_growth_uses_actual_capacity_and_preserves_boot_and_data_bytes(self):
        missing = [name for name in ['growpart', 'sfdisk'] if not shutil.which(name)]
        if missing:
            message = 'Missing STATE expansion test tools: ' + ', '.join(missing)
            if os.environ.get('NETWORK_EXIT_GROW_TESTS') == 'required': self.fail(message)
            self.skipTest(message)
        # Sparse regular files only. No loop devices, mounts or real card access.
        for card_bytes in [16_000_000_000, 32_000_000_000, 128_000_000_000, 256_000_000_000]:
            with self.subTest(capacity=card_bytes), tempfile.TemporaryDirectory() as tmp:
                disk = Path(tmp) / 'card.img'
                layout = factory_table()
                prefix = bytearray(4 * 1024 * 1024)
                struct.pack_into('<I', prefix, 440, 0x1234abcd)
                prefix[510:512] = b'\x55\xaa'
                prefix[8192:8209] = b'BOOTLOADER-BYTES!'
                with disk.open('xb') as stream:
                    stream.write(prefix)
                    stream.truncate(card_bytes)
                description = 'label: dos\nlabel-id: 0x1234abcd\nunit: sectors\n'
                for part in layout['partitions']:
                    description += f'start={part["start"]}, size={part["size"]}, type=83\n'
                subprocess.run(['sfdisk', '--no-reread', '--force', str(disk)], input=description,
                               text=True, check=True, stdout=subprocess.DEVNULL)
                control_offset = layout['partitions'][2]['start'] * 512
                state_offset = layout['partitions'][3]['start'] * 512
                marker = b'PERSISTENT-IDENTITY-AND-CONFIGURATION'
                with disk.open('r+b') as stream:
                    stream.seek(control_offset); stream.write(marker)
                    stream.seek(state_offset); stream.write(marker)
                before = capacity.table(disk)
                self.assertTrue(capacity.grow_partition(disk, card_bytes, UUID))
                after = capacity.table(disk)
                self.assertLess(capacity.validate_layout(after, card_bytes, UUID), 2048)
                self.assertEqual(after['partitions'][:3], before['partitions'][:3])
                with disk.open('rb') as stream:
                    stream.seek(8192); self.assertEqual(stream.read(17), b'BOOTLOADER-BYTES!')
                    for offset in [control_offset, state_offset]:
                        stream.seek(offset); self.assertEqual(stream.read(len(marker)), marker)
                with disk.open('rb') as stream: mbr = stream.read(512)
                self.assertFalse(capacity.grow_partition(disk, card_bytes, UUID))
                with disk.open('rb') as stream: self.assertEqual(stream.read(512), mbr)
