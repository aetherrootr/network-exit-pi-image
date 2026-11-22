#!/usr/bin/env python3
"""Offer first-boot setup on a local or serial console before normal login."""
from pathlib import Path
import subprocess
import sys

READY = Path('/etc/network-exit/initialized')


def main():
    if not sys.stdin.isatty() or READY.is_file():
        return 0
    print('Embedded Node first-boot setup', flush=True)
    while not READY.is_file():
        try:
            # Wait for a person so an unattended console cannot claim the
            # initialization lock ahead of an authorized SSH login.
            input('Press Enter to begin setup, or connect using an authorized pi SSH login. ')
            if READY.is_file(): break
            subprocess.run(['/usr/local/sbin/enodectl', 'config', '--first-boot'], check=True)
        except (EOFError, KeyboardInterrupt):
            return 0
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f'Setup did not finish: {exc}. Press Enter to retry or use an authorized pi SSH login.', file=sys.stderr)
    return 0


if __name__ == '__main__': sys.exit(main())
