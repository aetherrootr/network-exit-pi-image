#!/usr/bin/env python3
"""Automatically enter device setup from an interactive pi login."""
import os
from pathlib import Path
import subprocess
import sys

READY = Path('/run/network-exit/setup-complete')


def main():
    if os.getuid() != 1000 or not sys.stdin.isatty() or not sys.stdout.isatty():
        return 0
    if READY.is_file():
        return 0
    print('Embedded Node first-boot setup', flush=True)
    print('Complete the setup wizard to prepare this device. No packages will be installed.', flush=True)
    try:
        # The key-authorized pi administrator uses passwordless sudo. The root
        # entry rechecks the private setup state under the configuration lock.
        result = subprocess.run(['sudo', '--', '/usr/local/sbin/enodectl', 'config', '--first-boot'], check=False)
    except (OSError, KeyboardInterrupt) as exc:
        print(f'Setup interrupted: {exc}. Reconnect to try again.', file=sys.stderr)
        return 1
    if result.returncode:
        print('Setup is incomplete. Reconnect to continue; saved settings are retained.', file=sys.stderr)
        return 1
    print('Setup complete. This session now continues to the normal shell.', flush=True)
    return 0


if __name__ == '__main__': sys.exit(main())
