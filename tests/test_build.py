from _stage_support import source_path, source_library, assemble_layers
"""Exercise build ordering and failure cleanup without mounting or building an image."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'image_builder/runtime.sh'


class BuildStagesTest(unittest.TestCase):
    def run_plan(self, directory, stages, setup='', tools=None, contexts=None):
        project = Path(directory)
        scripts = project / 'stage'
        scripts.mkdir(parents=True, exist_ok=True)
        for name, body in stages:
            folder = scripts / name
            folder.mkdir(exist_ok=True)
            (folder / 'install.sh').write_text(body)
        commands = '\n'.join(
            f"run_build_stage {name} {(contexts or {}).get(name, ('chroot' if name == 'guest' else 'host-shell'))} 'Test stage' 'Fixture only' {i} {len(stages)}"
            for i, (name, _) in enumerate(stages, 1))
        harness = '''set -euo pipefail
project=$1
source "$2"
initialize_build
''' + setup + '\n' + commands
        env = os.environ.copy()
        env.pop('GITHUB_ACTIONS', None)
        if tools:
            env['PATH'] = str(tools) + os.pathsep + env['PATH']
        result = subprocess.run(['bash', '-c', harness, 'fixture', str(project), str(RUNTIME)],
                                env=env, text=True, capture_output=True, timeout=15)
        workspace, = (path for path in (project / 'work').glob('build.*') if path.is_dir())
        return result, workspace

    def test_plan_and_help_have_no_build_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / 'image_builder').mkdir()
            shutil.copy2(ROOT / 'build.sh', project)
            for name in ('build.sh', 'plan.sh', 'stage_catalog.py', 'stages.json'):
                shutil.copy2(ROOT / 'image_builder' / name, project / 'image_builder')
            shutil.copytree(ROOT / 'stage', project / 'stage', ignore=shutil.ignore_patterns('__pycache__'))
            before = sorted(str(path.relative_to(project)) for path in project.rglob('*'))
            for argument in ['--help', '--list-stages']:
                result = subprocess.run(['bash', str(project / 'build.sh'), argument],
                                        text=True, capture_output=True, check=True)
                self.assertNotIn('Missing build', result.stdout)
            self.assertEqual(result.stdout.count('[host-shell]'), 9)
            self.assertEqual(result.stdout.count('[chroot]'), 15)
            self.assertIn('image-manifests [host-shell]', result.stdout)
            self.assertEqual(before, sorted(str(path.relative_to(project)) for path in project.rglob('*')))

    def test_host_stages_share_state_and_write_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            result, work = self.run_plan(directory, [
                ('first', 'build_stage() { shared_value=retained; echo first-output; }'),
                ('second', 'build_stage() { [[ $shared_value == retained ]]; echo second-output; }')])
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn('first-output', (work / 'logs/first.log').read_text())
            self.assertIn('second-output', (work / 'logs/second.log').read_text())
            rows = (work / 'stages.tsv').read_text().splitlines()
            self.assertEqual([row.split('\t')[:2] for row in rows[1:]],
                             [['first', 'running'], ['first', 'completed'],
                              ['second', 'running'], ['second', 'completed']])

    def test_host_process_stage_receives_image_context_without_sharing_shell_state(self):
        with tempfile.TemporaryDirectory() as directory:
            body = '[[ $ENODECTL_PROJECT == "$PWD" && $ENODECTL_IMAGE_VERSION == fixture ]]; printf "%s" "$ENODECTL_TARGET_ROOT" > "$ENODECTL_PROJECT/received"\n'
            result, work = self.run_plan(directory, [('builder', body)],
                'version=fixture certificate=$project/cert; cd "$project"', contexts={'builder': 'host'})
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertEqual((Path(directory) / 'received').read_text(), str(work / 'root'))

    def test_failure_stops_inside_stage_and_skips_remaining_stages(self):
        with tempfile.TemporaryDirectory() as directory:
            result, work = self.run_plan(directory, [
                ('broken', 'build_stage() { echo before-error; false; touch "$project/unsafe"; }'),
                ('later', 'build_stage() { touch "$project/later"; }')])
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((Path(directory) / 'unsafe').exists())
            self.assertFalse((Path(directory) / 'later').exists())
            self.assertIn('before-error', (work / 'logs/broken.log').read_text())
            self.assertIn('broken\tfailed\t', (work / 'stages.tsv').read_text())
            self.assertIn('Failed stage: broken', result.stderr)
            self.assertNotIn('Build complete:', result.stdout)

    def mock_resources(self, directory, fail_unmount=False):
        tools = Path(directory) / 'tools'
        tools.mkdir()
        scripts = {
            'mountpoint': '[[ -f "${@: -1}/.mounted" ]]',
            'umount': ('exit 1' if fail_unmount else 'rm -- "${@: -1}/.mounted"'),
            'losetup': 'printf "%s\\n" "$*" >> "${RESOURCE_LOG:?}"',
        }
        for name, body in scripts.items():
            path = tools / name
            path.write_text('#!/bin/bash\nset -eu\n' + body + '\n')
            path.chmod(0o755)
        return tools

    def test_failure_restores_files_unmounts_and_detaches(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = self.mock_resources(directory)
            setup = '''export RESOURCE_LOG=$project/resources.log
mkdir -p "$rootfs/etc" "$rootfs/usr/sbin" "$rootfs/usr/bin" "$rootfs/dev" "$rootfs/proc"
touch "$rootfs/.mounted" "$rootfs/dev/.mounted" "$rootfs/proc/.mounted"
printf original-policy > "$build_dir/policy-rc.d"
ln -s /original/resolver "$build_dir/resolv.conf"
printf temporary > "$rootfs/etc/resolv.conf"
printf temporary > "$rootfs/usr/sbin/policy-rc.d"
touch "$rootfs/usr/bin/network-exit-build-qemu-aarch64"
resolver_saved=1 policy_saved=1 policy_written=1 qemu_added=1 loop=/dev/fixture-loop
'''
            result, work = self.run_plan(directory, [('broken', 'build_stage() { return 7; }')], setup, tools)
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertEqual(os.readlink(work / 'root/etc/resolv.conf'), '/original/resolver')
            self.assertEqual((work / 'root/usr/sbin/policy-rc.d').read_text(), 'original-policy')
            self.assertFalse((work / 'root/usr/bin/network-exit-build-qemu-aarch64').exists())
            self.assertFalse(list((work / 'root').rglob('.mounted')))
            self.assertEqual((Path(directory) / 'resources.log').read_text(), '-d /dev/fixture-loop\n')

    def test_cleanup_failure_is_reported_and_mounted_loop_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = self.mock_resources(directory, fail_unmount=True)
            setup = '''export RESOURCE_LOG=$project/resources.log
mkdir -p "$rootfs"
touch "$rootfs/.mounted"
loop=/dev/fixture-loop
'''
            result, _ = self.run_plan(directory, [('done', 'build_stage() { :; }')], setup, tools)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Resource cleanup failed', result.stderr)
            self.assertFalse((Path(directory) / 'resources.log').exists())
            self.assertNotIn('Build complete:', result.stdout)

    def test_chroot_stage_routes_script_to_guest_and_propagates_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = Path(directory) / 'tools'
            tools.mkdir()
            command = tools / 'chroot'
            command.write_text('''#!/bin/bash
set -eu
printf '%s\\n' "$@" > "${GUEST_ARGS:?}"
exit 9
''')
            command.chmod(0o755)
            body = 'echo never-run-on-host > "$project/unsafe"\n'
            result, work = self.run_plan(directory, [('guest', body)],
                'export GUEST_ARGS=$project/args', tools)
            self.assertEqual(result.returncode, 9)
            self.assertEqual((work / 'root/run/enodectl-build/stage/guest/install.sh').read_text(), body)
            self.assertIn('ENODECTL_BUILD_CHROOT=1', (Path(directory) / 'args').read_text())
            self.assertFalse((Path(directory) / 'unsafe').exists())
            self.assertIn('guest\tfailed\t', (work / 'stages.tsv').read_text())

    def test_guest_scripts_refuse_direct_execution(self):
        env = os.environ.copy()
        env.pop('ENODECTL_BUILD_CHROOT', None)
        for path in sorted((ROOT / 'stage').glob('*/install.sh')):
            with self.subTest(stage=path.name):
                result = subprocess.run(['bash', str(path)], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertIn('must run inside the image', result.stderr)


if __name__ == '__main__':
    unittest.main()
