"""A trial deadline independent of STATE preparation, networking and wall time."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re

from bootstate import current_slot, load

DEADLINE_SECONDS = 300
CMDLINE = Path('/proc/cmdline')
UPTIME = Path('/proc/uptime')
DEVICES = Path('/dev/disk/by-partuuid')
LOCK = Path('/run/network-exit-trial.lock')


def is_trial(cmdline=None):
    words = (CMDLINE.read_text() if cmdline is None else cmdline).split()
    flags = [word.split('=', 1)[1] for word in words if word.startswith('network_exit.trial=')]
    if flags and flags != ['1']:
        raise ValueError('Invalid trial boot flag')
    return bool(flags)


def uptime():
    return float(UPTIME.read_text().split()[0])


@contextmanager
def decision_lock(blocking=True):
    fd = os.open(LOCK, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield
    finally:
        os.close(fd)


def allow_confirmation():
    # Caller holds decision_lock through the boot-record update/fsync.
    if is_trial() and uptime() >= DEADLINE_SECONDS:
        raise ValueError('Trial deadline expired; refusing late boot confirmation')


def check_deadline():
    cmdline = CMDLINE.read_text()
    if not is_trial(cmdline):
        return
    # A busy confirmation or unreadable record must not block this service
    # indefinitely. Its own short service timeout requests a forced reboot too.
    with decision_lock(blocking=False):
        slot = current_slot(cmdline)
        matches = re.findall(r'(?:^|\s)network_exit.state=([0-9a-fA-F]{8})-04(?=\s|$)', cmdline)
        if len(matches) != 1:
            raise ValueError('Cannot identify trial boot-control partition')
        control = (DEVICES / f'{matches[0].lower()}-03').resolve(strict=True)
        state = load(control)
        if state['ne_pending'] == slot:
            raise ValueError('Trial boot was not confirmed before its deadline')
        if state['ne_confirmed'] != slot:
            raise ValueError('Trial slot is not the confirmed system')
