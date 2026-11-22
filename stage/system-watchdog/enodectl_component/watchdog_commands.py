"""Watchdog status command owned by the system-watchdog stage."""
import subprocess


def configure_parser(parser): parser.add_argument('action', choices=['status'])
def run(args, parser): subprocess.run(['/usr/lib/network-exit/node-watchdog'], check=True)
