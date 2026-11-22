"""Optional operator-selected SSH user CA trust, persisted on STATE."""
import os
from pathlib import Path
import stat

from components import STATE, save
from sshpub import read_source, validated_keys

FILENAME = 'ssh-user-ca.pub'


def target(config):
    if os.geteuid() != 0 or not os.path.ismount(STATE):
        raise ValueError('SSH CA configuration requires root and mounted persistent STATE')
    config = Path(config)
    config.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not stat.S_ISDIR(config.lstat().st_mode):
        raise ValueError('SSH CA configuration directory must not be a symlink')
    path = config / FILENAME
    try:
        info = path.lstat()
    except FileNotFoundError:
        return path
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('SSH user CA trust must be a regular file without links')
    return path


def ensure(config):
    path = target(config)
    if not path.exists():
        save(path, '')  # Empty means no CA is trusted; raw authorized_keys still work.
    return path


def _store(config, keys):
    if not keys:
        raise ValueError('Remove CA authorizations on the SSH page in enodectl service')
    path = target(config)
    content = ''.join(line + '\n' for line, _ in keys.values())
    save(path, content)
    return [fingerprint for _, fingerprint in keys.values()]


def replace(source, config):
    # Validate the entire source before modifying existing trust. CA private
    # keys, user certificates and authorized_keys options are never accepted.
    keys = validated_keys(read_source(source))
    return _store(config, keys)
