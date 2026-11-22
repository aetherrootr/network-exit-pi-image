"""Controller-owned operating-system restart requests."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from components import save
from power_policy import REAL_SYSTEMCTL

STATE=Path('/var/lib/network-exit')
RUNTIME=Path('/run/enodectl')

def uptime(): return float(Path('/proc/uptime').read_text().split()[0])

def reboot_system(force=False, reason='operator'):
    if os.geteuid() != 0:
        raise ValueError('Run sudo enodectl reboot')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', reason):
        raise ValueError('Invalid reboot reason')
    try: age = uptime()
    except (OSError, ValueError, IndexError): age = None
    record = json.dumps({'reason': reason, 'force': force, 'uptime_seconds': age}) + '\n'
    # Logging is best effort: recovery cannot depend on writable/available STATE.
    for path in [RUNTIME / 'reboot-request.json', STATE / 'last-reboot.json' if os.path.ismount(STATE) else None]:
        if path is not None:
            try: save(path, record)
            except OSError as exc: print(f'Reboot request log unavailable: {exc}', file=sys.stderr)
    command = [str(REAL_SYSTEMCTL), 'reboot']
    if force: command.append('--force')
    subprocess.run(command, check=True, timeout=15)
