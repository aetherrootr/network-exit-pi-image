from _stage_support import source_path, source_library, assemble_layers
"""RAM overlay budget and kernel ENOSPC tests in a private mount namespace."""
import errno
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
POLICY = source_path('usr/lib/network-exit/overlay-limit.sh')


def run(*command, **kwargs):
    return subprocess.run(command, text=True, capture_output=True, **kwargs)


def isolated_checks(parent_namespace, directory):
    if os.readlink('/proc/self/ns/mnt') == parent_namespace or os.geteuid() != 0:
        raise RuntimeError('Refusing mounts outside the isolated root-mapped namespace')
    def mount(*args):
        result = run('mount', *map(str, args))
        if result.returncode: raise RuntimeError(result.stderr)
    base = Path(directory)
    lower, upper, merged, state = [base / name for name in ['lower', 'upper', 'root', 'state']]
    for path in [lower / 'media/root-rw', lower / 'var/lib/network-exit', upper, merged, state]:
        path.mkdir(parents=True, exist_ok=True)
    (lower / 'base-marker').write_text('read-only system')
    (state / 'identity').write_text('persistent device')
    meminfo = base / 'meminfo'
    meminfo.write_text('MemTotal: 65536 kB\n')  # An 8 MiB cap keeps the test small.
    def start():
        mount('-t', 'tmpfs', 'tmpfs-root', upper)
        (upper / 'overlay').mkdir()
        (upper / 'work').mkdir()
        mount('-t', 'overlay', 'overlayroot', '-o',
              f'lowerdir={lower},upperdir={upper}/overlay,workdir={upper}/work', merged)
        mount('--move', upper, merged / 'media/root-rw')
        result = run('sh', '-c', '. "$1"; limit_root_overlay "$2" "$3"',
                     'fixture', str(POLICY), str(merged), str(meminfo))
        if result.returncode: raise RuntimeError(result.stdout + result.stderr)
        mount('--bind', state, merged / 'var/lib/network-exit')
    def stop():
        for path in [merged / 'var/lib/network-exit']:
            subprocess.run(['umount', str(path)], check=True)
        mount('--move', merged / 'media/root-rw', upper)
        subprocess.run(['umount', str(merged)], check=True)
        subprocess.run(['umount', str(upper)], check=True)
    start()
    backing = merged / 'media/root-rw'
    stats = os.statvfs(backing)
    assert stats.f_blocks * stats.f_frsize == 8 * 1024 * 1024
    assert stats.f_files == 2048
    try:
        with (merged / 'filled').open('wb', buffering=0) as stream:
            while True: stream.write(b'x' * 65536)
    except OSError as exc:
        assert exc.errno == errno.ENOSPC, exc
    else: raise AssertionError('Overlay failed to enforce its byte limit')
    (merged / 'var/lib/network-exit/after-full').write_text('STATE still writable')
    (merged / 'filled').unlink()
    count = 0
    try:
        while count < 4096:
            (merged / f'empty-{count}').touch()
            count += 1
    except OSError as exc:
        assert exc.errno == errno.ENOSPC, exc
    else: raise AssertionError('Overlay failed to enforce its inode limit')
    assert 0 < count < 2048
    assert (lower / 'base-marker').read_text() == 'read-only system'
    assert not (lower / 'empty-0').exists()
    stop()
    start()
    assert not (merged / 'empty-0').exists()
    assert not (merged / 'filled').exists()
    assert (merged / 'var/lib/network-exit/identity').read_text() == 'persistent device'
    assert (merged / 'var/lib/network-exit/after-full').read_text() == 'STATE still writable'
    stop()

class OverlayBudgetTests(unittest.TestCase):
    def apply(self, memory, mounts=None, exit_code=0):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            merged = root / 'root'
            (root / 'meminfo').write_text(memory)
            (root / 'mounts').write_text(mounts if mounts is not None else
                f'overlayroot {merged} overlay rw 0 0\ntmpfs-root {merged}/media/root-rw tmpfs rw 0 0\n')
            script = '''. "$1"
mount() { printf '%s\\n' "$*" >> "$CAPTURE"; return "$MOUNT_EXIT"; }
limit_root_overlay "$2" "$3" "$4"
'''
            result = run('sh', '-c', script, 'fixture', str(POLICY), str(merged),
                         str(root / 'meminfo'), str(root / 'mounts'),
                         env={**os.environ, 'CAPTURE': str(root / 'calls'), 'MOUNT_EXIT': str(exit_code)})
            calls = (root / 'calls').read_text() if (root / 'calls').exists() else ''
            return result, calls

    def test_limits_scale_with_detected_memory_and_have_an_absolute_ceiling(self):
        for total, budget in [(262144, 32768), (524288, 65536), (1048576, 131072),
                              (2097152, 131072), (4194304, 131072), (500001, 62500)]:
            with self.subTest(total=total):
                result, calls = self.apply(f'MemTotal: {total} kB\nMemFree: 100 kB\n')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f'-o remount,size={budget}k,nr_inodes={budget // 4} ', calls)

    def test_invalid_memory_and_unexpected_mounts_fail_before_remount(self):
        for memory in ['', 'MemTotal: 0 kB\n', 'MemTotal: -1 kB\n', 'MemTotal: 1 MB\n',
                       'MemTotal: x kB\n', 'MemTotal: 1 kB\n',
                       'MemTotal: 524288 kB\nMemTotal: 524288 kB\n']:
            with self.subTest(memory=memory):
                result, calls = self.apply(memory)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, '')
        for mounts in ['', '/dev/state /root/media/root-rw ext4 rw 0 0\n']:
            result, calls = self.apply('MemTotal: 524288 kB\n', mounts)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(calls, '')

    def test_remount_failure_is_propagated(self):
        result, calls = self.apply('MemTotal: 524288 kB\n', exit_code=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('remount,size=65536k', calls)
        self.assertNotIn('Root overlay limits:', result.stdout)

    def test_kernel_limits_and_fresh_upper_preserve_separate_state(self):
        missing = [name for name in ['unshare', 'mount', 'umount', 'sh'] if not shutil.which(name)]
        probe = run('unshare', '--user', '--map-root-user', '--mount', 'true') if not missing else None
        if missing or probe.returncode:
            reason = f'Missing tools: {missing}' if missing else probe.stderr.strip()
            if os.environ.get('NETWORK_EXIT_OVERLAY_TESTS') == 'required': self.fail(reason)
            self.skipTest(reason)
        # The parent owns cleanup after the private namespace has been destroyed,
        # including when a failed assertion leaves child mounts in place.
        with tempfile.TemporaryDirectory(prefix='enodectl-overlay-kernel-') as directory:
            result = run('unshare', '--user', '--map-root-user', '--mount', sys.executable,
                         str(Path(__file__).resolve()), '--isolated', os.readlink('/proc/self/ns/mnt'),
                         directory, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--isolated': isolated_checks(sys.argv[2], sys.argv[3])
    else: unittest.main()
