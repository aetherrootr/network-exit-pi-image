"""Redundant, CRC-protected boot records shared with the U-Boot script.

Records use U-Boot's non-redundant environment encoding in two separate banks.
The newest valid sequence wins; updates write only the other bank and fsync.
"""
import fcntl
import os
from pathlib import Path
import struct
import zlib

SIZE = 16384
STRIDE = 65536
FIELDS = ('ne_magic', 'ne_seq', 'ne_confirmed', 'ne_pending', 'ne_left', 'ne_A', 'ne_B')


def initial():
    return dict(ne_magic='NEAB1', ne_seq='0x0', ne_confirmed='A', ne_pending='none',
                ne_left='0', ne_A='1', ne_B='1')


def validate(state):
    if set(state) != set(FIELDS) or state['ne_magic'] != 'NEAB1':
        raise ValueError('Invalid A/B boot record')
    if state['ne_confirmed'] not in ('A', 'B') or state['ne_pending'] not in ('A', 'B', 'none'):
        raise ValueError('Invalid slot name')
    if state['ne_left'] not in ('0', '1', '2', '3') or any(state[f'ne_{s}'] not in ('0', '1') for s in ('A', 'B')):
        raise ValueError('Invalid slot state')
    if not state['ne_seq'].startswith('0x') or not 0 <= int(state['ne_seq'], 16) < 2**31:
        raise ValueError('Invalid boot sequence')
    return state


def encode(state):
    validate(state)
    data = b'\0'.join(f'{key}={state[key]}'.encode('ascii') for key in FIELDS) + b'\0\0'
    data = data.ljust(SIZE - 4, b'\0')
    return struct.pack('<I', zlib.crc32(data)) + data


def decode(record):
    if len(record) != SIZE or struct.unpack('<I', record[:4])[0] != zlib.crc32(record[4:]):
        raise ValueError('Boot record CRC mismatch')
    state = {}
    for entry in record[4:].split(b'\0'):
        if not entry:
            break
        key, value = entry.decode('ascii').split('=', 1)
        if key in state:
            raise ValueError('Duplicate boot field')
        state[key] = value
    return validate(state)


def read(fd):
    valid = []
    for bank in (0, 1):
        try:
            state = decode(os.pread(fd, SIZE, bank * STRIDE))
            valid.append((int(state['ne_seq'], 16), bank, state))
        except (ValueError, UnicodeError, struct.error):
            pass
    if not valid:
        raise ValueError('Both boot records are invalid; reflash the factory image')
    _, bank, state = max(valid, key=lambda item: (item[0], -item[1]))
    return bank, state


def update(path, mutate):
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        bank, old = read(fd)
        new = mutate(dict(old))
        new['ne_seq'] = hex(int(old['ne_seq'], 16) + 1)
        record = encode(new)
        offset = (1 - bank) * STRIDE
        remaining = memoryview(record)
        while remaining:
            count = os.pwrite(fd, remaining, offset)
            if count <= 0:
                raise OSError('Short boot record write')
            remaining = remaining[count:]
            offset += count
        os.fsync(fd)
        if os.pread(fd, SIZE, (1 - bank) * STRIDE) != record:
            raise OSError('Boot record readback failed')
        return new
    finally:
        os.close(fd)


def load(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        return read(fd)[1]
    finally:
        os.close(fd)


def current_slot(cmdline=None):
    words = (Path('/proc/cmdline').read_text() if cmdline is None else cmdline).split()
    slots = [word.split('=', 1)[1] for word in words if word.startswith('rauc.slot=')]
    if len(slots) != 1 or slots[0] not in ('A', 'B'):
        raise ValueError('Not booted through the A/B boot selector')
    return slots[0]


def select_for_test(state):
    """Boot-selection specification used for fault/rollback tests."""
    state = dict(validate(state))
    pending = state['ne_pending']
    if pending != 'none' and int(state['ne_left']) > 0 and state[f'ne_{pending}'] == '1':
        state['ne_left'] = str(int(state['ne_left']) - 1)
        return pending, state
    return state['ne_confirmed'], state
