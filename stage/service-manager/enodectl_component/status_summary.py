"""Compact, local-only controller status for the CLI and dashboard."""
from pathlib import Path
import subprocess

from enodectl_core import registry


def safe_text(value):
    return ''.join(char if char.isprintable() else repr(char)[1:-1] for char in str(value))


def table(headers, rows):
    rows = [tuple(safe_text(value) for value in row) for row in rows]
    widths = [max(len(header), *(len(row[index]) for row in rows)) if rows else len(header)
              for index, header in enumerate(headers)]
    def line(row): return '  '.join(value.ljust(width) for value, width in zip(row, widths)).rstrip()
    return '\n'.join([line(headers), line(tuple('-' * width for width in widths)), *(line(row) for row in rows)])


def bootchain():
    try:
        hooks = registry.hooks('status')
        if not hooks: return None, 'A/B bootchain is not installed'
        return hooks[0](), None
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        return None, str(exc)


def bootchain_rows(info):
    rows = [('Version', info['version']), ('Layout', info['layout']), ('State', info['state']),
            ('Current slot', info['current_slot']), ('Confirmed slot', info['confirmed_slot']),
            ('Pending slot', info['pending_slot'] or '-'), ('Boot attempts left', info['remaining_boot_attempts'])]
    for slot, state in info['slots'].items(): rows.append((f'Slot {slot}', 'valid' if state['valid'] else 'invalid'))
    if info.get('trial_deadline_active'): rows.append(('Trial time left', f"{info['trial_seconds_remaining']} s"))
    return rows


def bootchain_banner():
    info, error = bootchain()
    if error: return 'Bootchain: unavailable (' + safe_text(error) + ')'
    return (f"Bootchain: {info['version']} | Slot {info['current_slot']} | Confirmed {info['confirmed_slot']} | "
            f"Pending {info['pending_slot'] or '-'} | {info['state']}")


def service_rows():
    from service_manager import ServiceModel
    rows = []
    failed = False
    for row in ServiceModel().records():
        name, runtime = row['name'], row['state']
        if not row['units']:
            if name != 'system-watchdog': continue
            try: runtime = Path('/sys/class/watchdog/watchdog0/state').read_text().strip()
            except OSError: runtime = 'unavailable'
        if name == 'tailscale':
            try:
                from tailscale_control import backend_status
                runtime += ' / ' + backend_status()['BackendState']
            except (ValueError, OSError, KeyError, subprocess.SubprocessError): runtime += ' / login unavailable'
        if 'unavailable' in runtime or 'unknown' in runtime: failed = True
        rows.append((row['label'], 'ON' if row['enabled'] else 'OFF', runtime,
                     'Required' if row.get('mandatory') else 'Optional'))
    return rows, failed


def show():
    info, error = bootchain()
    print('Bootchain')
    print(table(('Field', 'Status'), bootchain_rows(info) if info is not None else [('Unavailable', error)]))
    print('\nKey services')
    rows, failed = service_rows()
    print(table(('Service', 'Startup', 'Runtime', 'Policy'), rows))
    return int(bool(error) or failed)
