"""Embedded Node Controller: commands and interactions are supplied by stages."""
import argparse
import os
import runpy
import subprocess
import sys
from enodectl_core import registry


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog='enodectl', description=__doc__)
    try:
        stages = registry.stages()
        if arguments and arguments[0] == '--internal':
            if len(arguments) < 2: raise ValueError('Missing private backend name')
            private = {name: value for stage in stages for name, value in stage.get('private', {}).items()}
            entry = private.get(arguments[1])
            if entry is None: raise ValueError('Unknown private backend')
            if entry.get('root', True) and os.geteuid() != 0: raise ValueError('This backend requires root')
            old = sys.argv
            sys.argv = ['enodectl ' + arguments[1], *arguments[2:]]
            try:
                if 'module' in entry: runpy.run_module(entry['module'], run_name='__main__')
                else: return registry.resolve(entry['call'])() or 0
            finally: sys.argv = old
            return 0
        blocked = {name: reason for stage in stages for name, reason in stage.get('blocked_commands', {}).items()}
        if arguments and arguments[0] in blocked: raise ValueError(blocked[arguments[0]])
        commands = parser.add_subparsers(dest='command')
        for stage in stages:
            for entry in stage.get('commands', []):
                sub = commands.add_parser(entry['name'], help=entry['help'])
                # Loading another capability must not break help or recovery.
                if 'register' in entry and arguments and arguments[0] == entry['name']:
                    registry.resolve(entry['register'])(sub)
                sub.set_defaults(_entry=entry, _parser=sub)
        args = parser.parse_args(arguments)
        if args.command is None:
            parser.print_help()
            return 0
        if args._entry.get('root', True) and os.geteuid() != 0:
            raise ValueError(f'Run `sudo enodectl {args.command}`')
        return registry.resolve(args._entry['handler'])(args, args._parser) or 0
    except (ValueError, OSError, KeyError, EOFError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'enodectl: {exc}\n')


if __name__ == '__main__': sys.exit(main())
