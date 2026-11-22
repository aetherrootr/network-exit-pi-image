#!/usr/bin/env python3
"""RAUC custom backend. No automatic installation or reboot."""
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bootstate import current_slot, load, update
import trial


PLATFORM = Path('/run/network-exit/platform.json')

def main():
    config = json.loads(PLATFORM.read_text())
    path = str(Path(config['boot_control']).resolve(strict=True))
    command, *args = sys.argv[1:]
    state = load(path)
    if command == 'get-current':
        print(current_slot())
    elif command == 'get-primary':
        print(state['ne_pending'] if state['ne_pending'] != 'none' and int(state['ne_left']) > 0
              else state['ne_confirmed'])
    elif command == 'get-state' and args[0] in ('A', 'B'):
        print('good' if state[f'ne_{args[0]}'] == '1' else 'bad')
    elif command == 'set-primary' and args[0] in ('A', 'B'):
        def activate(value):
            slot = args[0]
            if slot == current_slot():
                raise ValueError('Refusing to trial the running slot')
            # RAUC invalidates the target before writing and calls set-primary
            # after a successful install, without an intervening set-state good.
            # Re-enable bootability and arm the trial in the same durable record.
            # This does not confirm health or replace the confirmed fallback.
            value[f'ne_{slot}'] = '1'
            value['ne_pending'] = slot
            value['ne_left'] = '3'
            return value
        update(path, activate)
    elif command == 'set-state' and args[0] in ('A', 'B') and args[1] in ('good', 'bad'):
        def mark(value):
            slot, status = args
            if status == 'bad' and slot == current_slot():
                raise ValueError('Refusing to invalidate the running slot')
            value[f'ne_{slot}'] = '1' if status == 'good' else '0'
            # Marking an inactive slot good does not confirm an update. Only a
            # successful boot of the running pending slot may replace fallback.
            if status == 'good' and slot == current_slot() and value['ne_pending'] == slot:
                trial.allow_confirmation()
                value.update(ne_confirmed=slot, ne_pending='none', ne_left='0')
            return value
        with trial.decision_lock():
            update(path, mark)
    else:
        raise ValueError('Unsupported boot backend operation')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, IndexError, KeyError) as exc:
        sys.exit(f'Boot backend error: {exc}')
