"""Service state and lifecycle operations, independent of any terminal toolkit."""
import subprocess
import components
from enodectl_core import registry


class ServiceModel:
    def __init__(self):
        self.refresh()

    def refresh(self):
        saved = components.choices()
        self.enabled = {s['name']: bool(s.get('mandatory') or saved.get(s['name'], False)) for s in registry.services()}

    def records(self, query_runtime=True):
        services = registry.services()
        units = list(dict.fromkeys(unit for service in services for unit in service['units']))
        states = dict.fromkeys(units, 'checking...')
        if query_runtime and units:
            # One systemctl invocation avoids repeated process/DBus startup on
            # small boards. is-active prints one state per requested unit.
            try:
                result = subprocess.run(['systemctl', 'is-active', *units], capture_output=True, text=True, timeout=10, check=False)
                output = result.stdout.splitlines()
                states = dict(zip(units, output)) if len(output) == len(units) else dict.fromkeys(units, 'unknown')
            except (OSError, subprocess.SubprocessError): states = dict.fromkeys(units, 'unavailable')
        rows = []
        for service in services:
            runtime = []
            for unit in service['units']:
                state = states[unit].strip() or 'unknown'
                runtime.append('on-demand' if unit not in service.get('health_units', service['units']) and state == 'inactive' else state)
            rows.append({**service, 'enabled': self.enabled[service['name']],
                         'state': ', '.join(runtime) if runtime else 'built-in'})
        return rows

def change_startup(name, enabled):
    """Apply one confirmed service-local startup change; never toggle required units."""
    if name not in components.UNITS:
        raise ValueError('Required services cannot be enabled or disabled here')
    if bool(components.choices().get(name)) == bool(enabled): return
    if enabled: components.validate(name)
    import setup_state
    completed = (components.CONFIG / 'initialized').exists()
    setup_state.invalidate(components.CONFIG)
    components.set_enabled(name, bool(enabled))
    if completed:
        for hook in registry.hooks('before_complete'): hook()
        setup_state.complete(components.CONFIG)


def apply_service_settings(name, enabled, config_changed, saver):
    """One confirmed form submission, with configuration applied in the desired state."""
    if name not in components.UNITS: raise ValueError('Required services cannot be toggled')
    previous = bool(components.choices().get(name))
    enabled = bool(enabled)
    # Stop first when turning OFF, so saving a profile cannot restart it.
    if previous and not enabled: change_startup(name, False)
    try:
        # ON also reapplies unchanged profiles (e.g. sing-box's default JSON),
        # while an unchanged OFF form can stop an invalid/unconfigured service.
        result = saver() if config_changed or enabled else None
    except Exception:
        if previous and not enabled:
            try: change_startup(name, True)
            except Exception as exc:
                raise ValueError('Configuration failed; previous startup state could not be restored. Check status and logs.') from exc
        raise
    # Save first when turning ON, so startup validates the newly saved profile.
    if enabled and not previous:
        try: change_startup(name, True)
        except Exception as exc:
            # Enabling may have installed a systemd link before startup failed.
            # Return the actual/persisted service to OFF if possible.
            try: components.set_enabled(name, False)
            except Exception: pass
            raise ValueError('Configuration retained; service could not be enabled. Check configuration, status and logs.') from exc
    if enabled != previous or not config_changed:
        return 'Settings saved. Startup ON; verify service status and logs.' if enabled else 'Settings saved. Startup OFF; service is not running.'
    return result


def ensure_management():
    units = [unit for service in registry.services() if service.get('managed_startup') for unit in service['units']]
    if not units: return
    # Avoid repeatedly enabling already working mandatory services on every
    # interactive login (enable can trigger a relatively expensive daemon reload).
    try:
        active = subprocess.run(['systemctl', 'is-active', *units], capture_output=True, text=True, timeout=5, check=False).stdout.splitlines()
        enabled = subprocess.run(['systemctl', 'is-enabled', *units], capture_output=True, text=True, timeout=5, check=False).stdout.splitlines()
        if active == ['active'] * len(units) and enabled == ['enabled'] * len(units): return
    except (OSError, subprocess.SubprocessError): pass
    subprocess.run(['systemctl', 'enable', '--now', *units], check=True, capture_output=True)


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
    from service_diagnostics import health
    health()
    retain_admin_accounts()
    setup_state.complete(components.CONFIG)
