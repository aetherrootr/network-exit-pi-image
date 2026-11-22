"""Operational power-command guard; full root access is not a security boundary."""
from pathlib import Path

REAL_SYSTEMCTL = Path('/usr/lib/network-exit/systemctl.real')
POWER_COMMANDS = {'reboot', 'poweroff', 'halt', 'kexec', 'soft-reboot', 'exit',
                  'suspend', 'hibernate', 'hybrid-sleep', 'suspend-then-hibernate'}
POWER_UNITS = {'reboot.target', 'poweroff.target', 'halt.target', 'shutdown.target',
               'kexec.target', 'soft-reboot.target', 'suspend.target', 'hibernate.target',
               'hybrid-sleep.target', 'suspend-then-hibernate.target', 'ctrl-alt-del.target',
               'systemd-reboot.service', 'systemd-poweroff.service', 'systemd-halt.service',
               'systemd-kexec.service', 'systemd-soft-reboot.service'}
READ_ONLY = {'status', 'show', 'cat', 'help', 'list-units', 'list-unit-files',
             'list-jobs', 'list-dependencies', 'is-active', 'is-enabled', 'is-failed'}
VALUE_OPTIONS = {'--type', '--state', '--property', '--job-mode', '--kill-whom',
                 '--kill-who', '--signal', '--lines', '--output', '--host', '--machine',
                 '--root', '--image', '--image-policy', '--boot-loader-entry',
                 '--boot-loader-menu', '--reboot-argument', '--timestamp', '--when',
                 '--what', '--preset-mode', '--uid', '--drop-in'}
SHORT_VALUES = {'-t', '-p', '-P', '-s', '-n', '-o', '-H', '-M'}
FLAGS = {'--system', '--user', '--quiet', '--no-pager', '--full', '--all', '--plain',
         '--no-legend', '--no-ask-password', '--no-block', '--force', '--no-wall',
         '--dry-run', '--runtime', '--now', '--global', '--value', '--help', '--version',
         '-q', '-l', '-a', '-f', '-h'}


def command_arguments(arguments):
    index, uncertain = 0, False
    while index < len(arguments):
        item = arguments[index]
        if item == '--':
            index += 1
            break
        if not item.startswith('-'):
            break
        if item in VALUE_OPTIONS or item in SHORT_VALUES:
            index += 2
        elif item.startswith('--') and '=' in item:
            index += 1
        elif item[:2] in SHORT_VALUES and len(item) > 2:
            index += 1
        else:
            uncertain |= item not in FLAGS
            index += 1
    return (arguments[index] if index < len(arguments) else None), arguments[index + 1:], uncertain


def blocked_reason(arguments):
    command, operands, uncertain = command_arguments(arguments)
    forbidden = POWER_COMMANDS | POWER_UNITS
    if uncertain and any(item in forbidden for item in arguments):
        return 'Direct power control is disabled. Use sudo enodectl reboot; poweroff is not supported.'
    # Read-only inspection is allowed, including status of a masked power target.
    if command in READ_ONLY:
        return None
    if command in POWER_COMMANDS or any(item in forbidden for item in operands):
        return 'Direct power control is disabled. Poweroff is not supported. Use sudo enodectl reboot to restart this node.'
    return None
