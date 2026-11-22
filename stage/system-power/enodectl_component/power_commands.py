"""Operating-system restart command and build-time policy verification."""
import power_control
from power_policy import blocked_reason


def configure_parser(parser):
    parser.add_argument('--force', action='store_true', help='Skip normal service shutdown for recovery')
    parser.add_argument('--reason', default='operator', help='Record a reboot reason')


def run(args, parser): power_control.reboot_system(args.force, args.reason)


def check_policy():
    for arguments in (['--force', 'poweroff'], ['reboot'], ['start', 'poweroff.target'], ['--output', 'cat', 'reboot']):
        assert blocked_reason(arguments)
    assert blocked_reason(['status', 'tailscaled.service']) is None
