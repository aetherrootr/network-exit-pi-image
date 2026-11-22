"""A/B bootchain and OTA command registration."""
import bootchain
import update


def update_parser(parser): update.configure_parser(parser)
def update_run(args, parser): return update.run(args, parser)


def bootchain_parser(parser):
    actions = parser.add_subparsers(dest='bootchain_action', required=True)
    actions.add_parser('status', help='Show bootchain state as JSON')
    switch = actions.add_parser('switch', help='Select the next trial slot')
    switch.add_argument('slot', choices=['A', 'B'])
    switch.add_argument('--reboot', action='store_true')
    switch.add_argument('--retry-failed', action='store_true')


def bootchain_run(args, parser):
    if args.bootchain_action == 'status': bootchain.show_status()
    else: bootchain.switch_slot(args.slot, args.reboot, args.retry_failed)
