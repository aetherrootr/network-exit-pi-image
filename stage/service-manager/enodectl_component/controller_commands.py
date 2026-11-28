"""Configuration and status commands supplied by the service-manager stage."""


def config_parser(parser):
    parser.add_argument('--first-boot', action='store_true', help=__import__('argparse').SUPPRESS)


def config_run(args, parser):
    import initialize
    initialize.main(first_boot=args.first_boot)


def status_run(args, parser):
    from status_summary import show
    return show()
