"""Validate and append operator-selected SSH public keys to persistent homes."""
import base64
from contextlib import ExitStack, contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import pwd
import secrets
import shlex
import stat
import subprocess
import tempfile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

STATE = Path('/var/lib/network-exit')
HOMES = Path('/home')
MAX_SOURCE = 64 * 1024
MAX_EXISTING = 1024 * 1024
KEY_TYPES = {'ssh-ed25519', 'ssh-rsa', 'ecdsa-sha2-nistp256',
             'ecdsa-sha2-nistp384', 'ecdsa-sha2-nistp521',
             'sk-ssh-ed25519@openssh.com', 'sk-ecdsa-sha2-nistp256@openssh.com'}


def default_user():
    return 'pi'


def https_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError('Use an HTTPS public-key URL without embedded credentials')
    if any(ord(char) < 33 or ord(char) == 127 for char in url):
        raise ValueError('Invalid public-key URL')
    return url


class HTTPSRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        https_url(newurl)
        return super().redirect_request(request, fp, code, message, headers, newurl)


def read_source(source):
    if source.startswith(('https:', 'http:')):
        request = Request(https_url(source), headers={'User-Agent': 'enodectl-ssh-access'})
        with build_opener(HTTPSRedirects()).open(request, timeout=20) as response:
            https_url(response.geturl())
            data = response.read(MAX_SOURCE + 1)
    elif source.split(maxsplit=1) and source.split(maxsplit=1)[0] in KEY_TYPES:
        data = source.encode('utf-8')
    else:
        fd = os.open(source, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('Public-key input must be a regular file')
            data = stream.read(MAX_SOURCE + 1)
    if len(data) > MAX_SOURCE:
        raise ValueError('Public-key input exceeds 64 KiB')
    return data.decode('utf-8')


def validated_keys(text):
    keys = {}
    with tempfile.TemporaryDirectory(prefix='sshpub-') as tmp:
        path = Path(tmp) / 'key.pub'
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if any(not char.isprintable() and char != '\t' for char in line):
                raise ValueError('Control characters are not allowed in public keys')
            fields = line.split(maxsplit=2)
            if len(fields) < 2 or fields[0] not in KEY_TYPES:
                raise ValueError('Expected plain OpenSSH public keys; options and private keys are not accepted')
            try:
                blob = base64.b64decode(fields[1] + '=' * (-len(fields[1]) % 4), validate=True)
                length = int.from_bytes(blob[:4], 'big')
                if blob[4:4 + length] != fields[0].encode('ascii'):
                    raise ValueError('Key type does not match the public-key blob')
            except ValueError as exc:
                raise ValueError('Invalid SSH public-key data') from exc
            identity = (fields[0], base64.b64encode(blob).decode('ascii'))
            normalized = ' '.join([*identity, *fields[2:]])
            path.write_text(normalized + '\n')
            result = subprocess.run(['ssh-keygen', '-l', '-E', 'sha256', '-f', str(path)],
                                    capture_output=True, timeout=10)
            if result.returncode:
                raise ValueError('OpenSSH rejected a public key')
            fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=')
            keys[identity] = (normalized, fingerprint)
    if not keys:
        raise ValueError('No SSH public keys found')
    return keys


def existing_identities(text):
    # Include keys with existing restrictions: never add an unrestricted duplicate.
    identities = set()
    for line in text.splitlines():
        if line.lstrip().startswith('#'):
            continue
        try:
            lexer = shlex.shlex(line, posix=True)
            lexer.whitespace_split = True
            lexer.commenters = ''
            kind = next(lexer)
            if kind not in KEY_TYPES:
                kind = next(lexer)  # Existing authorized_keys options.
            blob_text = next(lexer)
        except StopIteration:
            continue
        except ValueError as exc:
            raise ValueError('Cannot safely parse an existing authorized_keys line')
        if kind in KEY_TYPES:
            try:
                blob = base64.b64decode(blob_text + '=' * (-len(blob_text) % 4), validate=True)
                identities.add((kind, base64.b64encode(blob).decode('ascii')))
            except ValueError:
                pass
    return identities


def open_directory(parent, name):
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)


def require_appliance():
    if os.geteuid() != 0 or not os.path.ismount(STATE):
        raise ValueError('Run with `sudo` on an appliance with persistent STATE mounted')


@contextmanager
def key_lock():
    require_appliance()
    fd = os.open(STATE / 'sshpub.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, 'w') as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('Invalid public-key import lock')
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


@contextmanager
def authorized_directory(username='pi'):
    require_appliance()
    if username != 'pi':
        raise ValueError('Only pi is enabled for SSH login; root SSH is disabled')
    account = pwd.getpwnam(username)
    with ExitStack() as stack:
        def directory(parent, name):
            fd = open_directory(parent, name)
            stack.callback(os.close, fd)
            return fd
        state_fd = directory(None, STATE)
        home = Path(account.pw_dir)
        if not 1000 <= account.pw_uid < 65534 or home.parent != HOMES or home.name in ('.', '..'):
            raise ValueError('The pi account must have an ordinary UID and a persistent home under /home')
        home_fd = directory(directory(state_fd, 'homes'), home.name)
        if not os.path.samefile(home, STATE / 'homes' / home.name):
            raise ValueError('The pi home is not backed by persistent STATE')
        try:
            os.mkdir('.ssh', 0o700, dir_fd=home_fd)
        except FileExistsError:
            pass
        ssh_fd = directory(home_fd, '.ssh')
        os.fchown(ssh_fd, account.pw_uid, account.pw_gid)
        os.fchmod(ssh_fd, 0o700)
        yield ssh_fd, home_fd, account


def read_authorized(ssh_fd, account):
    try:
        fd = os.open('authorized_keys', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=ssh_fd)
    except FileNotFoundError:
        return ''
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid not in (0, account.pw_uid):
            raise ValueError('authorized_keys must be a regular file without hard links')
        os.fchown(stream.fileno(), account.pw_uid, account.pw_gid)
        os.fchmod(stream.fileno(), 0o600)
        data = stream.read(MAX_EXISTING + 1)
    if len(data) > MAX_EXISTING:
        raise ValueError('Existing authorized_keys exceeds 1 MiB')
    return data.decode('utf-8')


def write_authorized(ssh_fd, home_fd, account, content):
    if len(content.encode('utf-8')) > MAX_EXISTING:
        raise ValueError('authorized_keys would exceed 1 MiB')
    name = '.authorized_keys-' + secrets.token_hex(12)
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=ssh_fd)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            os.fchown(stream.fileno(), account.pw_uid, account.pw_gid)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, 'authorized_keys', src_dir_fd=ssh_fd, dst_dir_fd=ssh_fd)
        os.fsync(ssh_fd)
        os.fsync(home_fd)
    finally:
        try: os.unlink(name, dir_fd=ssh_fd)
        except FileNotFoundError: pass


def append_keys(keys, username):
    require_appliance()
    if username != 'pi':
        raise ValueError('Only pi is enabled for SSH login; root SSH is disabled')
    with key_lock(), authorized_directory(username) as (ssh_fd, home_fd, account):
        previous = read_authorized(ssh_fd, account)
        known = existing_identities(previous)
        additions = [entry for identity, entry in keys.items() if identity not in known]
        if not additions:
            return []
        content = previous + ('\n' if previous and not previous.endswith('\n') else '')
        content += ''.join(line + '\n' for line, _ in additions)
        write_authorized(ssh_fd, home_fd, account, content)
        return [fingerprint for _, fingerprint in additions]


def add(source, username):
    if os.geteuid() != 0 or not os.path.ismount(STATE):
        raise ValueError('Run with `sudo` on an appliance with persistent STATE mounted')
    keys = validated_keys(read_source(source))
    fingerprints = append_keys(keys, username)
    print(f'Added {len(fingerprints)} SSH public key(s) for {username}; existing keys were preserved.')
    for fingerprint in fingerprints:
        print(fingerprint)
    return fingerprints
