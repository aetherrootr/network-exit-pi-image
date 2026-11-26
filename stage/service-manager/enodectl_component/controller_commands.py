"""Configuration and status commands supplied by the service-manager stage."""
import subprocess
import sys
from enodectl_core import registry


def config_parser(parser):
    parser.add_argument('--first-boot', action='store_true', help=__import__('argparse').SUPPRESS)


def config_run(args, parser):
    import initialize
    initialize.main(first_boot=args.first_boot)


def status_run(args, parser):
    failed = False
    print('Embedded Node Controller', flush=True)
    print('Image stages: ' + ', '.join(stage['id'] for stage in registry.stages()), flush=True)
    for hook in registry.hooks('status'):
        try: hook()
        except (ValueError, OSError, KeyError) as exc:
            print(f'Local status unavailable: {exc}', file=sys.stderr)
            failed = True
    services = registry.services()
    packages = [p for stage in registry.stages() for p in stage.get('packages', [])]
    if packages:
        result = subprocess.run(['dpkg-query', '-W', *packages], check=False)
        failed = failed or result.returncode != 0
    print('Node services:', flush=True)
    subprocess.run(['systemctl', '--no-pager', '--full', 'status',
                    *[unit for service in services for unit in service['units']]], check=False)
    return int(failed)
