#!/usr/bin/env python3
"""Private systemd network backend. Operator entry point is enodectl."""
from pathlib import Path
import os
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import network_config

try:
    if os.geteuid() != 0: raise ValueError('Run through `enodectl` or systemd')
    if len(sys.argv) != 2 or sys.argv[1] not in ('restore', 'sync', 'sync-wg', 'rollback', 'refresh-uplink'):
        raise ValueError('Private network backend: restore | sync | sync-wg | rollback | refresh-uplink')
    if sys.argv[1] == 'restore': network_config.restore()
    elif sys.argv[1] == 'rollback': network_config.rollback()
    elif sys.argv[1] == 'refresh-uplink': network_config.refresh_uplink()
    else:
        with network_config.lock():
            if sys.argv[1] == 'sync-wg':
                import components
                network_config.apply(selected={**components.choices(), 'wireguard': True})
            else: network_config.apply()
except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
    sys.exit(f'Network operation failed: {exc}')
