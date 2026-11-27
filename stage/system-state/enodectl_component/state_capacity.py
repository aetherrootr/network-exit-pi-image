"""Grow only the final STATE partition to the detected card capacity."""
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

STATE = Path('/var/lib/network-exit')
SECTOR = 512
MIB_SECTORS = 2048
ROOT_SECTORS = 4092 * MIB_SECTORS
MIN_STATE_SECTORS = 2048 * MIB_SECTORS


def table(disk):
    result = subprocess.run(['sfdisk', '--json', str(disk)], check=True,
                            capture_output=True, text=True, timeout=15)
    return json.loads(result.stdout)['partitiontable']


def validate_layout(layout, disk_bytes, state_uuid):
    if not re.fullmatch(r'[0-9a-f]{8}-04', state_uuid):
        raise ValueError('Invalid STATE PARTUUID')
    if layout.get('label') != 'dos' or layout.get('sectorsize') != SECTOR:
        raise ValueError('STATE expansion requires the reviewed 512-byte-sector MBR layout')
    if layout.get('id', '').lower() != '0x' + state_uuid[:8]:
        raise ValueError('STATE PARTUUID does not match the selected card')
    parts = layout.get('partitions', [])
    if len(parts) != 4 or any(part.get('type') != '83' for part in parts):
        raise ValueError('Unexpected partition count or type; refusing expansion')
    expected = [(8192, ROOT_SECTORS), (8192 + ROOT_SECTORS, ROOT_SECTORS),
                (8192 + 2 * ROOT_SECTORS, MIB_SECTORS)]
    for part, (start, size) in zip(parts[:3], expected):
        if part['start'] != start or part['size'] != size:
            raise ValueError('System or boot-control partition differs from the factory layout')
    start = expected[-1][0] + expected[-1][1]
    if parts[3]['start'] != start or parts[3]['size'] < MIN_STATE_SECTORS:
        raise ValueError('STATE is not the final factory partition or is below its minimum size')
    sectors = disk_bytes // SECTOR
    if disk_bytes % SECTOR or sectors > 2**32:
        raise ValueError('Card capacity is outside the supported MBR sector range')
    end = start + parts[3]['size']
    if end > sectors:
        raise ValueError('Card is smaller than its partition layout; STATE will never be shrunk')
    return sectors - end


def validate_mount(mounted, device_number):
    # initramfs can leave a mount source such as /root/dev/mmcblk0p4 in
    # mountinfo after switch_root. Compare kernel device numbers instead of
    # requiring that historical pathname to exist in the current root.
    expected = f'{os.major(device_number)}:{os.minor(device_number)}'
    if len(mounted) != 1 or mounted[0].get('fstype') != 'ext4' or mounted[0].get('maj:min') != expected:
        raise ValueError('STATE mount does not match the selected ext4 partition')


def validate_boot_prefix(before, after):
    # sfdisk may normalize obsolete CHS coordinates when a card is expanded.
    # U-Boot uses LBA; preserve all boot bytes, partition flags/types, starts
    # and the first three LBA sizes. Only the final partition may grow.
    if len(before) != len(after) or before[:446] != after[:446] or before[510:] != after[510:]:
        raise ValueError('Boot bytes or partition records changed during expansion')
    for index in range(4):
        offset = 446 + 16 * index
        start, end = before[offset:offset+16], after[offset:offset+16]
        if (start[0] != end[0] or start[4] != end[4] or start[8:12] != end[8:12]
                or (index < 3 and start[12:16] != end[12:16])):
            raise ValueError('Boot bytes or partition records changed during expansion')


def discover(state_uuid):
    device = Path('/dev/disk/by-partuuid', state_uuid).resolve(strict=True)
    info = device.stat()
    if not stat.S_ISBLK(info.st_mode) or not os.path.ismount(STATE):
        raise ValueError('Persistent STATE must be mounted from a block device')
    sysfs = Path(f'/sys/dev/block/{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}').resolve(strict=True)
    if (sysfs / 'partition').read_text().strip() != '4':
        raise ValueError('STATE is not partition 4')
    parent = sysfs.parent
    if (parent / 'queue/logical_block_size').read_text().strip() != '512':
        raise ValueError('Unsupported card logical sector size')
    disk = Path('/dev', parent.name).resolve(strict=True)
    disk_info = disk.stat()
    major, minor = map(int, (parent / 'dev').read_text().strip().split(':'))
    if not stat.S_ISBLK(disk_info.st_mode) or disk_info.st_rdev != os.makedev(major, minor):
        raise ValueError('Cannot identify the parent card safely')
    mounted = json.loads(subprocess.run(
        ['findmnt', '--json', '--mountpoint', str(STATE), '--output', 'FSTYPE,MAJ:MIN'],
        check=True, capture_output=True, text=True, timeout=15).stdout)['filesystems']
    validate_mount(mounted, info.st_rdev)
    return disk, device, int((parent / 'size').read_text()) * SECTOR


def grow_partition(disk, disk_bytes, state_uuid):
    """Use the distribution's growpart; regular-image fixtures exercise this too."""
    before = table(disk)
    free = validate_layout(before, disk_bytes, state_uuid)
    if free < MIB_SECTORS:
        return False
    with Path(disk).open('rb') as stream:
        prefix = stream.read(4 * 1024 * 1024)
    if len(prefix) != 4 * 1024 * 1024 or prefix[510:512] != b'\x55\xaa':
        raise ValueError('Invalid factory boot prefix')
    result = subprocess.run(['growpart', '--fudge', '0', '--update=on', str(disk), '4'],
                            capture_output=True, text=True, timeout=120)
    after = table(disk)
    remaining = validate_layout(after, disk_bytes, state_uuid)
    with Path(disk).open('rb') as stream:
        revised = stream.read(len(prefix))
    validate_boot_prefix(prefix, revised)
    if result.returncode != 0 or remaining >= MIB_SECTORS:
        raise ValueError('STATE partition expansion did not complete; retry on the next boot')
    return True


def resize_filesystem(device):
    # resize2fs relies on resolvable mount sources to enter online mode.
    # Provide a canonical mount in a private namespace, leaving the live STATE
    # and its bind mounts untouched. Namespace teardown releases the extra
    # mount automatically even on failure or timeout.
    number = device.stat().st_rdev
    expected = f'{os.major(number)}:{os.minor(number)}'
    with tempfile.TemporaryDirectory(prefix='network-exit-grow-', dir='/run') as mountpoint:
        subprocess.run(['unshare', '--mount', '--propagation', 'private', '--',
                        'bash', '-euc',
                        'mount -t ext4 -o rw,noatime,nodev,nosuid "$1" "$2"\n'
                        'test "$(findmnt -rn --mountpoint "$2" -o MAJ:MIN)" = "$3"\n'
                        'resize2fs "$1"',
                        'state-resize', str(device), mountpoint, expected], check=True, timeout=300)


def grow(state_uuid):
    if os.geteuid() != 0:
        raise ValueError('STATE expansion requires root')
    disk, device, capacity = discover(state_uuid)
    changed = grow_partition(disk, capacity, state_uuid)
    # Also recover if a previous boot updated the MBR but did not finish growing
    # the filesystem. Never shrink, reformat or use an estimated card capacity.
    expected = table(disk)['partitions'][3]['size'] * SECTOR
    actual = int(subprocess.run(['blockdev', '--getsize64', str(device)], check=True,
                                capture_output=True, text=True, timeout=15).stdout)
    if actual != expected:
        subprocess.run(['partx', '--update', '--nr', '4', str(disk)], check=True, timeout=30)
        actual = int(subprocess.run(['blockdev', '--getsize64', str(device)], check=True,
                                   capture_output=True, text=True, timeout=15).stdout)
        if actual != expected:
            raise ValueError('Kernel STATE capacity has not updated; retry on the next boot')
    resize_filesystem(device)
    print(f'STATE capacity checked against the detected card: {capacity} bytes; partition: {actual} bytes.')
    return changed
