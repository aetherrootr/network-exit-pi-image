"""Bootchain state, explicit slot activation and controller-owned reboot requests."""
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from bootstate import current_slot, load
from components import save
from power_policy import REAL_SYSTEMCTL
from power_control import reboot_system
from trial import DEADLINE_SECONDS, is_trial, uptime

PLATFORM = Path('/run/network-exit/platform.json')
RELEASE = Path('/usr/share/network-exit/release.json')
CMDLINE = Path('/proc/cmdline')
STATE = Path('/var/lib/network-exit')
LOCK = Path('/run/enodectl-operation.lock')
RUNTIME = Path('/run/enodectl')


def platform():
    info = json.loads(PLATFORM.read_text())
    if info.get('layout') != 'ab-v2':
        raise ValueError('This device does not have a supported A/B factory layout')
    slot = current_slot(CMDLINE.read_text())
    control = str(Path(info['boot_control']).resolve(strict=True))
    return info, slot, load(control)


def classify(slot, record, trial_boot=False, fallback=False):
    confirmed, pending = record['ne_confirmed'], record['ne_pending']
    if record[f'ne_{confirmed}'] != '1' or record[f'ne_{slot}'] != '1':
        return 'inconsistent'
    if pending == slot:
        return 'trial' if trial_boot else 'inconsistent'
    if pending == confirmed and slot != confirmed and int(record['ne_left']) > 0:
        return 'recovery-pending'
    if slot != confirmed:
        return 'inconsistent'
    if pending == 'none':
        return 'confirmed'
    if int(record['ne_left']) == 0:
        return 'rolled-back'
    return 'fallback' if fallback else 'pending-reboot'


def status():
    info, slot, record = platform()
    cmdline = CMDLINE.read_text()
    state = classify(slot, record, is_trial(cmdline), 'network_exit.fallback=1' in cmdline.split())
    age = uptime()
    deadline_active = is_trial(cmdline) and slot != record['ne_confirmed']
    result = {'state': state, 'version': json.loads(RELEASE.read_text())['version'],
              'layout': info['layout'], 'current_slot': slot, 'confirmed_slot': record['ne_confirmed'],
              'pending_slot': None if record['ne_pending'] == 'none' else record['ne_pending'],
              'remaining_boot_attempts': int(record['ne_left']),
              'slots': {name: {'valid': record[f'ne_{name}'] == '1'} for name in ('A', 'B')},
              'trial_deadline_active': deadline_active,
              'trial_seconds_remaining': max(0, round(DEADLINE_SECONDS - age, 1)) if deadline_active else None}
    return result


def show_status():
    result = status()
    print(json.dumps(result, indent=2))
    return result



def switch_slot(target, reboot=False, retry_failed=False):
    if os.geteuid() != 0:
        raise ValueError('Run sudo enodectl bootchain switch')
    if target not in ('A', 'B'):
        raise ValueError('Choose slot A or B')
    with LOCK.open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _, current, before = platform()
        if target == current:
            raise ValueError('The selected slot is already running')
        if before[f'ne_{target}'] != '1':
            raise ValueError('The selected slot is invalid or has an incomplete installation')
        if before['ne_pending'] == target and before['ne_left'] == '0' and not retry_failed:
            raise ValueError('This slot exhausted its trial attempts; explicitly use --retry-failed to try again')
        if before['ne_pending'] != target or before['ne_left'] == '0':
            name = 'rootfs.0' if target == 'A' else 'rootfs.1'
            subprocess.run(['rauc', 'status', 'mark-active', name], check=True)
            _, running, after = platform()
            if running != current or after['ne_pending'] != target or after['ne_left'] != '3' or after['ne_confirmed'] != before['ne_confirmed']:
                raise ValueError('Slot activation verification failed; refusing reboot')
        print(f'Slot {target} selected for the next boot. It must pass trial health confirmation.')
    if reboot:
        reboot_system(reason='slot-switch')
    else:
        print('Run sudo enodectl reboot when ready. The running system has not changed.')
