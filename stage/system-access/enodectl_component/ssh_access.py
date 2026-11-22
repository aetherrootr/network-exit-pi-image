"""Stage SSH login/CA changes and preserve at least one validated authorization."""
from dataclasses import dataclass, field
import os
from pathlib import Path
import shlex
import stat

import sshpub
import sshca
from components import save


@dataclass
class Entry:
    role: str
    identity: tuple
    fingerprint: str
    comment: str
    restricted: bool
    lines: list = field(default_factory=list)


class Draft:
    def __init__(self, login, ca):
        self.original = {'login': login, 'ca': ca}
        self.text = dict(self.original)
        self.cache = {}

    def inventory(self):
        entries, unknown = [], 0
        for role, text in self.text.items():
            grouped = {}
            for number, line in enumerate(text.splitlines(keepends=True)):
                if not line.strip() or line.lstrip().startswith('#'):
                    continue
                try:
                    lexer = shlex.shlex(line, posix=True)
                    lexer.whitespace_split = True
                    lexer.commenters = ''
                    kind = next(lexer)
                    restricted = kind not in sshpub.KEY_TYPES
                    if restricted:
                        if role == 'ca': raise ValueError('CA options are unsupported')
                        kind = next(lexer)
                    blob = next(lexer)
                    comment = lexer.instream.read().strip()
                    key = (kind, blob)
                    if key not in self.cache:
                        parsed = sshpub.validated_keys(kind + ' ' + blob)
                        identity, (_, fingerprint) = next(iter(parsed.items()))
                        self.cache[key] = identity, fingerprint
                    identity, fingerprint = self.cache[key]
                except (ValueError, StopIteration):
                    unknown += 1
                    continue
                if identity not in grouped:
                    grouped[identity] = Entry(role, identity, fingerprint, comment, restricted)
                grouped[identity].restricted |= restricted
                grouped[identity].lines.append(number)
            entries.extend(grouped.values())
        return entries, unknown

    def counts(self):
        entries, _ = self.inventory()
        return {role: sum(entry.role == role for entry in entries) for role in self.text}

    def require_access(self):
        if not self.inventory()[0]:
            raise ValueError('Keep at least one validated login public key or trusted user CA key')

    def add(self, role, source):
        keys = sshpub.validated_keys(sshpub.read_source(source))
        entries, _ = self.inventory()
        known = {entry.identity for entry in entries if entry.role == role}
        additions = [line for identity, (line, _) in keys.items() if identity not in known]
        previous = self.text[role]
        self.text[role] = previous + ('\n' if previous and not previous.endswith('\n') and additions else '')
        self.text[role] += ''.join(line + '\n' for line in additions)
        return len(additions)

    def remove(self, role, identity):
        entries, _ = self.inventory()
        selected = next((entry for entry in entries if entry.role == role and entry.identity == identity), None)
        if selected is None: raise ValueError('The selected SSH authorization no longer exists')
        if len(entries) == 1:
            raise ValueError('Cannot remove the last SSH authorization; add a replacement first')
        # Remove all duplicate authorizations for this material in the chosen role.
        self.text[role] = ''.join(line for number, line in enumerate(self.text[role].splitlines(keepends=True))
                                  if number not in selected.lines)
        self.require_access()


class Store:
    def __init__(self, config):
        self.config = Path(config)

    def read_ca(self):
        path = sshca.ensure(self.config)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('SSH user CA trust must be a regular file without links')
            data = stream.read(sshpub.MAX_SOURCE + 1)
        if len(data) > sshpub.MAX_SOURCE: raise ValueError('SSH user CA trust exceeds 64 KiB')
        return data.decode('utf-8')

    def load(self):
        with sshpub.key_lock(), sshpub.authorized_directory() as (ssh_fd, _, account):
            return Draft(sshpub.read_authorized(ssh_fd, account), self.read_ca())

    def commit(self, draft):
        draft.require_access()
        if len(draft.text['login'].encode()) > sshpub.MAX_EXISTING:
            raise ValueError('authorized_keys would exceed 1 MiB')
        if len(draft.text['ca'].encode()) > sshpub.MAX_SOURCE:
            raise ValueError('SSH user CA trust would exceed 64 KiB')
        with sshpub.key_lock(), sshpub.authorized_directory() as (ssh_fd, home_fd, account):
            current = {'login': sshpub.read_authorized(ssh_fd, account), 'ca': self.read_ca()}
            if current != draft.original:
                raise ValueError('SSH authorizations changed in another session; reload before saving')
            # Write a nonempty replacement before clearing the other kind. Each
            # file is atomic; interruption may leave a mix, never an empty union.
            order = ['login', 'ca'] if draft.counts()['login'] else ['ca', 'login']
            for role in order:
                if current[role] == draft.text[role]: continue
                if role == 'login':
                    sshpub.write_authorized(ssh_fd, home_fd, account, draft.text[role])
                else:
                    save(sshca.target(self.config), draft.text[role])
        draft.original = dict(draft.text)


def require_access(config):
    Store(config).load().require_access()
