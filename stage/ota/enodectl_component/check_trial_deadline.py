#!/usr/bin/env python3
"""A failed check lets systemd request reboot; this helper never reboots itself."""
import sys
from trial import check_deadline

try:
    check_deadline()
except (OSError, ValueError, KeyError, IndexError) as exc:
    sys.exit(f'Trial deadline check failed: {exc}')
