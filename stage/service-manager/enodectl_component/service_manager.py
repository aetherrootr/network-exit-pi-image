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

    def records(self):
        rows = []
        for service in registry.services():
            states = []
            for unit in service['units']:
                try:
                    result = subprocess.run(['systemctl', 'is-active', unit], capture_output=True, text=True, timeout=10, check=False)
                    state = result.stdout.strip() or 'unknown'
                    states.append('on-demand' if unit not in service.get('health_units', service['units']) and state == 'inactive' else state)
                except (OSError, subprocess.SubprocessError): states.append('unavailable')
            rows.append({**service, 'enabled': self.enabled[service['name']],
                         'state': ', '.join(states) if states else 'built-in'})
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
    from service_diagnostics import health
    health()
    retain_admin_accounts()
    setup_state.complete(components.CONFIG)
