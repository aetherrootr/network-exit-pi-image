#!/usr/bin/env python3
"""Ordinary users elevate through sudo; root services retain vendor su."""
import os
import sys

if os.geteuid() != 0:
    sys.exit('Direct su is disabled. Use `sudo -i` to elevate from pi.')
os.execv('/usr/lib/network-exit/su.real', ['/usr/lib/network-exit/su.real', *sys.argv[1:]])
