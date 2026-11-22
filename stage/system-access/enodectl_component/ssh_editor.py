"""Staged SSH authorizations and conflict recovery, independent of Textual."""
import components
from ssh_access import Store


class SSHEditor:
    def __init__(self, config=None):
        self.store = Store(config if config is not None else components.CONFIG)
        self.draft = self.store.load()

    def records(self):
        entries, unknown = self.draft.inventory()
        return [{'id': entry.role + ':' + entry.fingerprint, 'role': entry.role,
                 'identity': entry.identity, 'type': entry.identity[0], 'fingerprint': entry.fingerprint,
                 'comment': entry.comment, 'restricted': entry.restricted} for entry in entries]

    def changed(self): return self.draft.text != self.draft.original

    def add(self, role, source):
        count = self.draft.add(role, source)
        return f'{count} authorization(s) staged. Save authorizations to apply.'

    def remove(self, id):
        row = next(row for row in self.records() if row['id'] == id)
        self.draft.remove(row['role'], row['identity'])
        return 'Removal staged. Existing file contents are unchanged until Save.'

    def save(self):
        try: self.store.commit(self.draft)
        except (ValueError, OSError) as exc:
            self.draft = self.store.load()
            raise ValueError(str(exc) + '; current authorizations reloaded. Review before retrying.') from None
        return 'SSH authorizations saved. Test a NEW SSH connection before closing this one.'
