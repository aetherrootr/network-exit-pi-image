"""Persist key-only pi access and a per-device root recovery password."""
from pathlib import Path
from contextlib import contextmanager
import fcntl
import os
import subprocess

from components import CONFIG, save

ETC = Path('/etc')
INITIALIZATION_LOCK = Path('/run/enodectl-initialization.lock')


@contextmanager
def initialization_lock():
    fd = os.open(INITIALIZATION_LOCK, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('Initialization is already running in another session') from exc
        yield
    finally:
        os.close(fd)


def apply_policy():
    # Also disable a pi password restored from an older STATE snapshot.
    if lock_pi_password():
        retain_admin_accounts()
    if (CONFIG/'accounts-ready').exists():
        check_account_policy()
    # All authorized pi sessions now have passwordless administrative sudo.
    (ETC/'enodectl-sudoers.d/99-bootstrap').unlink(missing_ok=True)


def retain_admin_accounts():
    rows = [line.split(':') for line in (ETC/'passwd').read_text().splitlines()]
    admins = {row[0] for row in rows if int(row[2]) == 0 or 1000 <= int(row[2]) < 65534}
    gids = {row[3] for row in rows if row[0] in admins}
    groups = set()
    for name in ('passwd', 'shadow', 'group', 'gshadow'):
        output = []
        for line in (ETC/name).read_text().splitlines():
            fields = line.split(':')
            if name in ('passwd', 'shadow'):
                selected = fields[0] in admins
            elif name == 'group':
                selected = fields[2] in gids or bool(set(fields[3].split(',')) & admins)
                if selected: groups.add(fields[0])
            else:
                selected = fields[0] in groups
            if selected: output.append(line)
        save(CONFIG/'accounts'/name, '\n'.join(output) + '\n')


def check_pi():
    accounts = {line.split(':')[0]: line.split(':') for line in (ETC/'passwd').read_text().splitlines()}
    if accounts.get('pi', ['', '', '0'])[2] == '0' or 'pi' not in accounts:
        raise ValueError('The ordinary pi login account is missing or has UID 0')


def shadow_passwords():
    return {line.split(':')[0]: line.split(':')[1] for line in (ETC/'shadow').read_text().splitlines()}


def lock_pi_password():
    check_pi()
    password = shadow_passwords().get('pi')
    if password is None:
        raise ValueError('The pi shadow account is missing')
    if password.startswith(('!', '*')):
        return False
    # Lock the password only, not the account expiry or SSH public-key access.
    # Never delete the password field or enable empty-password authentication.
    subprocess.run(['passwd', '--lock', 'pi'], check=True, env={**os.environ, 'LC_ALL': 'C'})
    if not shadow_passwords().get('pi', '').startswith(('!', '*')):
        raise ValueError('Cannot disable pi password authentication')
    return True


def check_account_policy():
    check_pi()
    shadows = shadow_passwords()
    if not shadows.get('pi', '').startswith(('!', '*')):
        raise ValueError('The pi password must remain locked; use SSH public keys')
    password = shadows.get('root', '')
    if not password or password.startswith(('!', '*')):
        raise ValueError('Set a nonempty root recovery password before completing configuration')


def configure_accounts(snapshot=None):
    lock_pi_password()
    snapshot = retain_admin_accounts if snapshot is None else snapshot
    snapshot()
    if (CONFIG/'accounts-ready').exists():
        check_account_policy()
        apply_policy()
        return
    print('SSH login for pi uses public keys only; no pi password is required.')
    print('Set this device\'s root recovery password. Direct root SSH remains disabled.', flush=True)
    # passwd owns the TTY interaction/PAM policy. Passwords never enter argv,
    # environment variables or our Python configuration/log files.
    subprocess.run(['passwd', 'root'], check=True, env={**os.environ, 'LC_ALL': 'C'})
    snapshot()
    check_account_policy()
    save(CONFIG/'accounts-ready', '1\n')
    apply_policy()
