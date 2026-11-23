"""Service state and lifecycle operations, independent of any terminal toolkit."""
import subprocess
import components
from enodectl_core import registry


class ServiceModel:
    def __init__(self):
        self.original = {}
        self.draft = {}
        self.refresh()

    def refresh(self, preserve=False):
        saved = components.choices()
        current = {s['name']: bool(s.get('mandatory') or saved.get(s['name'], False)) for s in registry.services()}
        changes = {name: value for name, value in self.draft.items() if self.original.get(name) != value} if preserve else {}
        self.original = current
        self.draft = {**current, **changes}

    def records(self):
        rows = []
        for service in registry.services():
            states = []
            for unit in service['units']:
                try:
                    result = subprocess.run(['systemctl', 'is-active', unit], capture_output=True, text=True, timeout=10, check=False)
                    states.append(result.stdout.strip() or 'unknown')
                except (OSError, subprocess.SubprocessError): states.append('unavailable')
            rows.append({**service, 'enabled': self.draft[service['name']],
                         'state': ', '.join(states) if states else 'built-in'})
        return rows

    def toggle(self, name):
        spec = components.service_spec(name)
        if spec.get('mandatory'): raise ValueError('This management/system service is required and cannot be disabled')
        self.draft[name] = not self.draft[name]

    def changes(self): return {name: value for name, value in self.draft.items() if self.original[name] != value}

    def apply(self):
        changes = self.changes()
        for name, enabled in changes.items():
            if enabled: components.validate(name)
        import setup_state
        completed = (components.CONFIG / 'initialized').exists()
        if changes: setup_state.invalidate(components.CONFIG)
        try:
            for name, enabled in changes.items(): components.set_enabled(name, enabled)
            if changes and completed:
                for hook in registry.hooks('before_complete'): hook()
                setup_state.complete(components.CONFIG)
        finally: self.refresh()


def ensure_management():
    for service in registry.services():
        if service.get('managed_startup'):
            subprocess.run(['systemctl', 'enable', '--now', *service['units']], check=True, capture_output=True)


def finish_setup():
    from accounts import check_account_policy, retain_admin_accounts
    import setup_state
    check_account_policy()
    for hook in registry.hooks('required_check'): hook()
    for hook in registry.hooks('before_complete'): hook()
    chosen = {**components.choices(), **{s['name']: True for s in registry.services() if s.get('mandatory')}}
    for name in components.UNITS:
        if chosen.get(name): components.validate(name)
    setup_state.invalidate(components.CONFIG)
    for hook in registry.hooks('before_services'): hook(chosen)
    ensure_management()
    for name in components.UNITS: components.set_enabled(name, bool(chosen.get(name)))
    retain_admin_accounts()
    setup_state.complete(components.CONFIG)


def open_services(first_boot=False):
    import os
    from accounts import initialization_lock
    if os.geteuid() != 0 or not os.path.ismount(components.STATE):
        raise ValueError('Run `sudo enodectl service` on a node with STATE mounted')
    from service_tui import ServiceApp
    import sys
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError('The service dashboard requires an interactive terminal; connect with `ssh -t`')
    with initialization_lock():
        ensure_management()
        return ServiceApp(first_boot=first_boot).run()
