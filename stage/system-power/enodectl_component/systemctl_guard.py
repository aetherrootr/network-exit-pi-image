#!/usr/bin/env python3
"""Keep normal systemctl service management; route power operations to enodectl."""
import os
from pathlib import Path
import sys

LIB = Path('/usr/lib/network-exit')
if not LIB.exists(): LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))
from power_policy import REAL_SYSTEMCTL, blocked_reason


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    reason = blocked_reason(arguments)
    if reason:
        print(reason, file=sys.stderr)
        return 1
    os.execv(str(REAL_SYSTEMCTL), [str(REAL_SYSTEMCTL), *arguments])


if __name__ == '__main__': sys.exit(main())
