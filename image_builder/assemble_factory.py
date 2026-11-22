#!/usr/bin/env python3
"""Assemble a regular A/B disk-image file; never accepts a block-device target."""
import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bootstate', ROOT / 'stage/ota/enodectl_component/bootstate.py')
bootstate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstate)


def assemble(base, rootfs, output, state_mib=2048):
    base, rootfs, output = map(Path, [base, rootfs, output])
    if not base.is_file() or not rootfs.is_file() or output.exists():
        raise ValueError('Inputs must be regular files and the output must not exist')
    prefix_size = 8192 * 512
    size = rootfs.stat().st_size
    if size % (1024 * 1024):
        raise ValueError('Root filesystem must be MiB-aligned')
    state_bytes = state_mib * 1024 * 1024
    starts = [prefix_size, prefix_size + size, prefix_size + 2 * size,
              prefix_size + 2 * size + 1024 * 1024]
    total = starts[3] + state_bytes
    with base.open('rb') as src:
        prefix = src.read(prefix_size)
    if len(prefix) != prefix_size or prefix[510:512] != b'\x55\xaa':
        raise ValueError('Invalid base boot prefix')
    with output.open('xb') as target:
        target.write(prefix)
        target.truncate(total)
    disk_id = struct.unpack_from('<I', prefix, 440)[0]
    table = f'label: dos\nlabel-id: 0x{disk_id:08x}\nunit: sectors\n'
    for index, (start, length) in enumerate(zip(starts, [size, size, 1024 * 1024, state_bytes])):
        table += f'start={start // 512}, size={length // 512}, type=83' + (', bootable' if index < 2 else '') + '\n'
    subprocess.run(['sfdisk', '--no-reread', '--force', str(output)], input=table,
                   text=True, check=True, stdout=subprocess.DEVNULL)
    with output.open('rb') as target:
        revised = target.read(prefix_size)
    if revised[:446] != prefix[:446] or revised[510:] != prefix[510:]:
        raise ValueError('Assembler changed boot bytes outside the partition records')
    with output.open('r+b') as target:
        for start in starts[:2]:
            target.seek(start)
            with rootfs.open('rb') as src:
                shutil.copyfileobj(src, target, 4 * 1024 * 1024)
        import tempfile
        with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
            state = Path(tmp) / 'state.img'
            with state.open('wb') as stream: stream.truncate(state_bytes)
            subprocess.run(['mkfs.ext4', '-q', '-F', '-m', '0', '-L', 'NETWORK_STATE', str(state)], check=True)
            target.seek(starts[3])
            with state.open('rb') as src: shutil.copyfileobj(src, target, 4 * 1024 * 1024)
        record = bootstate.encode(bootstate.initial())
        for bank in (0, 1):
            target.seek(starts[2] + bank * bootstate.STRIDE)
            target.write(record)
        target.flush()
        os.fsync(target.fileno())
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True)
    parser.add_argument('--rootfs', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(assemble(args.base, args.rootfs, args.output))


if __name__ == '__main__': main()
