"""Automatic first-boot account provisioning and the shared service dashboard."""
import os
import sys
import components
from components import CONFIG, STATE
from accounts import configure_accounts, retain_admin_accounts, initialization_lock
import setup_state


def initialize():
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError('Interactive setup requires a terminal; connect with `ssh -t`')
    from service_tui import ServiceApp
    import service_manager
    configure_accounts(retain_admin_accounts)
    service_manager.ensure_management()
    if not ServiceApp(first_boot=True).run():
        raise ValueError('Setup is incomplete. Reconnect to continue with saved settings.')


def main(first_boot=False):
    if os.geteuid() != 0: raise ValueError('Run `sudo enodectl config`')
    with initialization_lock():
        if not os.path.ismount(STATE):
            raise ValueError('Persistent STATE is not mounted; setup cannot continue')
        if first_boot and (CONFIG / 'initialized').is_file():
            setup_state.publish(CONFIG)
            return
        if first_boot or not (CONFIG / 'accounts-ready').exists():
            initialize()
        else:
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                raise ValueError('Interactive configuration requires a terminal; connect with `ssh -t`')
            import service_manager
            from service_tui import ServiceApp
            service_manager.ensure_management()
            if not ServiceApp().run():
                raise ValueError('Configuration exited without passing health checks.')
