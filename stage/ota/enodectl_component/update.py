#!/usr/bin/env python3
"""Operator-invoked image checks and signed A/B updates; never runs on a timer."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from bootchain import LOCK, platform, reboot_system, show_status

SOURCE = Path('/usr/share/network-exit/update-source.json')
RELEASE = Path('/usr/share/network-exit/release.json')
STATE = Path('/var/lib/network-exit')
TRUST = Path('/usr/share/network-exit/ota-ca.pem')
PROGRESS_INTERVAL = 5


def console_report(message):
    print(message, flush=True)


def safe_message(message):
    # External tool output may contain terminal control sequences or bundle
    # metadata. Keep progress/error text safe for both CLI and TUI rendering.
    return ''.join(c if 32 <= ord(c) < 127 else '?' for c in str(message))


class TransferProgress:
    def __init__(self, label, total, report):
        self.label, self.total, self.report = label, total, report
        self.last_percent, self.last_time = -5, time.monotonic()
        self.update(0)

    def update(self, received):
        percent = min(100, received * 100 // self.total)
        now = time.monotonic()
        if percent >= self.last_percent + 5 or now - self.last_time >= PROGRESS_INTERVAL or percent == 100:
            self.report(f'{self.label}: {percent}% ({received / 1048576:.1f} / {self.total / 1048576:.1f} MiB)')
            self.last_percent, self.last_time = percent, now


def rauc_progress():
    """Read the installer's real percentage; unavailable progress is nonfatal."""
    try:
        def property_value(name):
            result = subprocess.run(['busctl', '--json=short', 'get-property', 'de.pengutronix.rauc', '/',
                                     'de.pengutronix.rauc.Installer', name],
                                    check=True, capture_output=True, text=True, timeout=3)
            return json.loads(result.stdout)['data']
        if property_value('Operation') != 'installing': return None
        percent, message, _ = property_value('Progress')
        if type(percent) is int and 0 <= percent <= 100 and isinstance(message, str):
            return f'RAUC: {percent}% - {safe_message(message)}'
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        pass
    return None


def run_reported(command, phase, report, progress=None):
    """Wait for a real subprocess while reporting its progress and failures.

    communicate drains both output streams, including a verbose child. A
    timeout leaves the subprocess running; it is only a progress poll.
    """
    report(phase + '...')
    started = time.monotonic()
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace') as process:
        while True:
            try:
                output, _ = process.communicate(timeout=PROGRESS_INTERVAL)
                break
            except subprocess.TimeoutExpired:
                current = progress() if progress else None
                elapsed = int(time.monotonic() - started)
                report(current or f'{phase}: still running ({elapsed}s elapsed).')
        if process.returncode:
            # Include the actual diagnostic rather than only the exit status.
            lines = [safe_message(line.strip()) for line in (output or '').splitlines() if line.strip()]
            detail = '\n'.join(lines[-8:])
            raise ValueError(f'{phase} failed (exit {process.returncode}).' + ('\n' + detail if detail else ''))


def latest(source):
    repository = source['repository']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Invalid upstream repository')
    request = Request(f'https://api.github.com/repos/{repository}/releases/latest',
                      headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'enodectl'})
    with urlopen(request, timeout=30) as response:
        release = json.load(response)
    if release.get('draft') or release.get('prerelease'):
        raise ValueError('Upstream returned an unpublished or prerelease image')
    version = release['tag_name']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', version):
        raise ValueError('Invalid upstream version')
    name = source['asset_prefix'] + version + source['asset_suffix']
    matches = [asset for asset in release.get('assets', []) if asset['name'] == name]
    if len(matches) != 1:
        raise ValueError('This release does not contain exactly one compatible OTA bundle')
    asset = matches[0]
    url = asset['browser_download_url']
    if not url.startswith(f'https://github.com/{repository}/releases/download/'):
        raise ValueError('OTA asset is outside the configured upstream repository')
    return {'version': version, 'name': name, 'url': url, 'size': int(asset['size'])}


def download(asset, destination, report=console_report):
    if asset['size'] <= 0 or asset['size'] + 64 * 1024 * 1024 > shutil.disk_usage(destination.parent).free:
        raise ValueError('Insufficient STATE space for this OTA bundle')
    received = 0
    progress = TransferProgress('Downloading OTA', asset['size'], report)
    request = Request(asset['url'], headers={'User-Agent': 'enodectl'})
    with urlopen(request, timeout=60) as response, destination.open('xb') as stream:
        while chunk := response.read(4 * 1024 * 1024):
            received += len(chunk)
            if received > asset['size']:
                raise ValueError('OTA download exceeds the announced size')
            stream.write(chunk)
            progress.update(received)
        report('Synchronizing downloaded bundle to STATE...')
        stream.flush()
        os.fsync(stream.fileno())
    if received != asset['size']:
        raise ValueError('Incomplete OTA download')
    destination.chmod(0o400)
    report('OTA download complete.')


def install(bundle, source, reboot=False, retry_failed=False, skip_signature_verification=False, confirmation='', report=console_report):
    if os.geteuid() != 0:
        raise ValueError('Run `sudo enodectl update install`')
    if skip_signature_verification:
        from ota_recovery import CONFIRMATION
        if not bundle or confirmation != CONFIRMATION:
            raise ValueError(f'Unsigned recovery requires a local bundle and the exact confirmation phrase: `{CONFIRMATION}` (without backticks)')
    cache = STATE / 'update-cache'
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    with LOCK.open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Re-read boot state under the operator lock, never from a stale preflight.
        info, active, state = platform()
        if not (STATE / 'config/initialized').exists():
            raise ValueError('Complete `sudo enodectl config` before installing OTA updates')
        if state['ne_pending'] != 'none' and (int(state['ne_left']) > 0 or state['ne_pending'] == active):
            raise ValueError('A trial update is already pending; boot and confirm it before another update')
        if not skip_signature_verification and not TRUST.is_file():
            raise ValueError('OTA trust certificate is missing')
        if state['ne_pending'] != 'none' and not retry_failed:
            raise ValueError('The previous trial was not confirmed; inspect it or explicitly use `--retry-failed`')
        with tempfile.TemporaryDirectory(prefix='ota-', dir=cache) as tmp:
            local = Path(tmp) / 'update.raucb'
            if bundle:
                origin = Path(bundle).resolve(strict=True)
                expected_size = origin.stat().st_size
                if not origin.is_file() or expected_size <= 0 or expected_size + 64 * 1024 * 1024 > shutil.disk_usage(cache).free:
                    raise ValueError('Local bundle must be a regular file that fits in STATE')
                # Always verify/install our private copy, not an externally mutable path.
                progress = TransferProgress('Staging local OTA', expected_size, report)
                with origin.open('rb') as src, local.open('xb') as dst:
                    remaining = expected_size
                    while remaining:
                        chunk = src.read(min(remaining, 4 * 1024 * 1024))
                        if not chunk:
                            raise ValueError('Local bundle changed while staging')
                        dst.write(chunk)
                        remaining -= len(chunk)
                        progress.update(expected_size - remaining)
                    if src.read(1):
                        raise ValueError('Local bundle changed while staging')
                    report('Synchronizing staged bundle to STATE...')
                    dst.flush()
                    os.fsync(dst.fileno())
                local.chmod(0o400)
                report('Local OTA staging complete.')
            else:
                report('Checking latest published release...')
                asset = latest(source)
                report('Selected OTA version: ' + asset['version'])
                download(asset, local, report)
            # RAUC authenticates the bundle and enforces the compatible board and
            # inactive-slot selection. A checksum alone never authorizes installation.
            if skip_signature_verification:
                from ota_recovery import install as recovery_install
                report('Preparing local recovery; signature verification explicitly skipped.')
                recovery_install(local, info, active, state, STATE)
            else:
                run_reported(['rauc', 'info', f'--keyring={TRUST}', str(local)], 'Verifying OTA signature and metadata', report)
                report('OTA signature verification passed.')
                expected = 'B' if active == 'A' else 'A'
                run_reported(['rauc', 'install', str(local)], f'Installing OTA to inactive slot {expected}', report, rauc_progress)
                report('RAUC installation complete. Checking bootchain activation...')
            _, running, after = platform()
            expected = 'B' if active == 'A' else 'A'
            if running != active or after['ne_pending'] != expected or after['ne_left'] != '3':
                raise ValueError('Update activation did not produce the expected three-attempt trial; refusing reboot')
            report(f'Installed to slot {expected}. Current slot {active} is unchanged. Three trial boots are available.')
    if reboot:
        reboot_system(reason='update')
    else:
        report('Run `sudo enodectl reboot` when ready. This command did not reboot the device.')


def console_confirmation(bundle):
    from ota_recovery import CONFIRMATION
    if os.geteuid() != 0:
        raise ValueError('Run `sudo enodectl update install`')
    if not bundle:
        raise ValueError('Signature bypass requires an explicit local OTA bundle')
    try:
        with open('/dev/tty', 'w') as output, open('/dev/tty', 'r') as input_stream:
            output.write('WARNING: OTA signature verification will be skipped for this install.\n'
                         'The supplied image can execute arbitrary privileged code after boot.\n'
                         f'Local bundle: {bundle}\nType exactly `{CONFIRMATION}` (without backticks) to continue: ')
            output.flush()
            return input_stream.readline().strip()
    except OSError as exc:
        raise ValueError('Signature bypass requires an interactive terminal; connect with `ssh -t` or use the service TUI') from exc


def configure_parser(parser):
    sub = parser.add_subparsers(dest='update_action')
    sub.add_parser('check', help='Check the configured image repository without installing')
    sub.add_parser('status', help='Show local version and bootchain status')
    install_parser = sub.add_parser('install', help='Explicitly install a signed local or latest upstream OTA bundle')
    install_parser.add_argument('bundle', nargs='?')
    install_parser.add_argument('--reboot', action='store_true', help='Reboot through enodectl after successful installation')
    install_parser.add_argument('--retry-failed', action='store_true', help='Allow a new install after an unconfirmed trial')
    install_parser.add_argument('--skip-signature-verification', action='store_true',
                                help='Recover from a local plain OTA bundle without authenticating its signature; requires typed confirmation')


def run(args, parser):
    try:
        if args.update_action == 'status':
            show_status()
        else:
            source = json.loads(SOURCE.read_text())
            if args.update_action == 'install':
                confirmation = console_confirmation(args.bundle) if args.skip_signature_verification else ''
                install(args.bundle, source, args.reboot, args.retry_failed, args.skip_signature_verification, confirmation)
            else:
                current = json.loads(RELEASE.read_text())['version']
                available = latest(source)
                print(f'Installed: {current}\nUpstream: {available["version"]}')
                print('Already current.' if current == available['version'] else 'Update available. Run `sudo enodectl update install` to install explicitly.')
    except HTTPError as exc:
        parser.exit(1, 'No published release is available.\n' if exc.code == 404 else f'Update check failed: HTTP {exc.code}\n')
    except (ValueError, OSError, KeyError, URLError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'enodectl update: {exc}\n')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='enodectl update', description=__doc__)
    configure_parser(parser)
    return run(parser.parse_args(argv), parser)


if __name__ == '__main__': sys.exit(main())
