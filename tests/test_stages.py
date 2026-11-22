"""Stage composition and real shiv packaging, without installing image software."""
from _stage_support import ROOT, source_path
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from image_builder.stage_catalog import catalogue, copy_layer, entries
import test_build


class StageCompositionTests(unittest.TestCase):
    def fixture(self, folder):
        root = Path(folder)
        shutil.copytree(ROOT / 'stage', root / 'stage', ignore=shutil.ignore_patterns('__pycache__'))
        (root/'image_builder').mkdir()
        shutil.copy2(ROOT/'image_builder/stages.json',root/'image_builder/stages.json')
        return root

    def test_shell_only_stage_needs_no_optional_directories_or_requirements(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);stage=root/'stage/example';stage.mkdir(parents=True)
            (stage/'stage.json').write_text(json.dumps({'id':'example','context':'host-shell','title':'Fixture','description':'Only an installer'}))
            (stage/'install.sh').write_text('build_stage() { :; }\n')
            (root/'image_builder').mkdir()
            (root/'image_builder/stages.json').write_text(json.dumps(['example']))
            _,stages,owners=catalogue(root)
            self.assertEqual([row['id'] for row in stages],['example']);self.assertEqual(owners,{})
            self.assertEqual(entries(root)[0][-1],'stage/example/install.sh')
            copy_layer(stage/'rootfs_layer',root/'target')
            self.assertFalse((root/'target').exists())
            self.assertEqual(sorted(p.name for p in stage.iterdir()),['install.sh','stage.json'])

    def test_optional_assets_reject_wrong_types_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory);stage=root/'stage/base-system'
            for name in ('rootfs_layer','enodectl_component','requirements.txt','config'):
                with self.subTest(asset=name):
                    path=stage/name
                    if name=='requirements.txt':path.mkdir()
                    else:path.write_text('invalid directory')
                    with self.assertRaisesRegex(ValueError,'invalid '+name):catalogue(root)
                    if path.is_dir():path.rmdir()
                    else:path.unlink()
                    path.symlink_to(root/'image_builder/stages.json')
                    with self.assertRaisesRegex(ValueError,'invalid '+name):catalogue(root)
                    path.unlink()

    def test_plan_rejects_out_of_order_missing_or_duplicate_stages_before_execution(self):
        for kind in ('order', 'missing', 'duplicate', 'empty', 'object', 'invalid-id'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = self.fixture(directory)
                plan = json.loads((root / 'image_builder/stages.json').read_text())
                if kind == 'order': plan.insert(0, plan.pop(plan.index('tailscale')))
                elif kind == 'missing': (root / 'stage/tailscale/install.sh').unlink()
                elif kind == 'duplicate': plan.append('tailscale')
                elif kind == 'empty': plan=[]
                elif kind == 'object': plan={'stages':plan}
                else: plan.insert(0,'../outside')
                (root / 'image_builder/stages.json').write_text(json.dumps(plan))
                with self.assertRaises(ValueError): catalogue(root)

    def test_asset_and_command_collisions_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            duplicate = root / 'stage/singbox/rootfs_layer/etc/systemd/system/network-exit-ddns.service'
            duplicate.write_text('not allowed')
            with self.assertRaisesRegex(ValueError, 'Duplicate image asset'): catalogue(root)
            duplicate.unlink()
            path = root / 'stage/singbox/stage.json'
            meta = json.loads(path.read_text())
            meta['commands'] = [{'name': 'config'}]
            path.write_text(json.dumps(meta))
            with self.assertRaisesRegex(ValueError, 'Duplicate commands'): catalogue(root)

    def test_layer_preserves_mode_and_refuses_symlink_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            (source / 'etc').mkdir(parents=True)
            file = source / 'etc/private.conf'
            file.write_text('fixture')
            file.chmod(0o600)
            target = root / 'target'
            copy_layer(source, target)
            self.assertEqual((target / 'etc/private.conf').stat().st_mode & 0o777, 0o600)
            shutil.rmtree(target / 'etc')
            outside = root / 'outside'
            outside.mkdir()
            (target / 'etc').symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError): copy_layer(source, target)
            self.assertFalse((outside / 'private.conf').exists())

    def test_module_runner_copies_own_layer_then_routes_installation_to_guest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'image_builder').mkdir()
            shutil.copy2(ROOT / 'image_builder/stage_catalog.py', root / 'image_builder')
            stage = root / 'stage/example'
            (stage / 'rootfs_layer/etc').mkdir(parents=True)
            (stage / 'rootfs_layer/etc/example.conf').write_text('stage-owned')
            (stage / 'install.sh').write_text('echo fixture-install\n')
            (stage / 'stage.json').write_text(json.dumps({'id': 'example', 'context': 'chroot', 'requires': [], 'title': 'Fixture', 'description': 'Fixture install'}))
            (root / 'image_builder/stages.json').write_text(json.dumps(['example']))
            tools = root / 'tools'
            tools.mkdir()
            command = tools / 'chroot'
            command.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$GUEST_ARGS"\n')
            command.chmod(0o755)
            result, workspace = test_build.BuildStagesTest().run_plan(directory, [('example', 'echo fixture-install\n')],
                'export GUEST_ARGS=$project/args', tools, contexts={'example': 'chroot'})
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((workspace / 'root/etc/example.conf').read_text(), 'stage-owned')
            self.assertTrue((workspace / 'root/run/enodectl-build/stage/example/install.sh').exists())
            self.assertIn('/run/enodectl-build/stage/example', (root / 'args').read_text())

    def test_launcher_scrubs_shiv_and_python_environment_controls(self):
        launcher = ROOT / 'stage/enodectl/rootfs_layer/usr/local/sbin/enodectl'
        with patch.dict(os.environ, {'SHIV_ENTRY_POINT': 'malicious:run', 'SHIV_ROOT': '/unsafe', 'PYTHONPATH': '/unsafe'}), \
                patch.object(os, 'geteuid', return_value=1000), patch.object(os, 'execve') as execute:
            exec(compile(launcher.read_text(), str(launcher), 'exec'), {})
        command, arguments, environment = execute.call_args.args
        self.assertEqual(command, '/usr/bin/python3')
        self.assertIn('-I', arguments)
        self.assertNotIn('SHIV_ENTRY_POINT', environment)
        self.assertNotIn('PYTHONPATH', environment)
        self.assertNotEqual(environment['SHIV_ROOT'], '/unsafe')

    def test_real_shiv_bundle_is_standalone_and_selected_plugins_control_the_cli(self):
        python = os.environ.get('ENODECTL_TEST_SHIV_PYTHON', sys.executable)
        available = subprocess.run([python, '-c', 'import shiv'], capture_output=True)
        if available.returncode:
            if os.environ.get('NETWORK_EXIT_SHIV_TESTS') == 'required': self.fail('shiv is required')
            self.skipTest('shiv is not installed')
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            plan_path = root / 'image_builder/stages.json'
            plan = json.loads(plan_path.read_text())
            plan = [s for s in plan if s not in ('cloudflared', 'singbox', 'ddns')]
            plan.insert(plan.index('enodectl'), 'example')
            plan_path.write_text(json.dumps(plan))
            example = root / 'stage/example'
            (example / 'enodectl_component').mkdir(parents=True)
            (example / 'install.sh').write_text('#!/bin/sh\nexit 0\n')
            wheel = root / 'fixture_dependency-1.0-py3-none-any.whl'
            with zipfile.ZipFile(wheel, 'w') as archive:
                files = {
                    'fixture_dependency.py': 'VALUE="DEPENDENCY_OK"\n',
                    'fixture_dependency-1.0.dist-info/METADATA': 'Metadata-Version: 2.1\nName: fixture-dependency\nVersion: 1.0\n',
                    'fixture_dependency-1.0.dist-info/WHEEL': 'Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n'}
                for name, value in files.items(): archive.writestr(name, value)
                archive.writestr('fixture_dependency-1.0.dist-info/RECORD', ''.join(name+',,\n' for name in files))
            (example / 'requirements.txt').write_text(str(wheel) + '\n')
            (example / 'stage.json').write_text(json.dumps({'id': 'example', 'context': 'chroot', 'requires': [], 'title': 'Fixture', 'description': 'Fixture plugin',
                'commands': [{'name': 'example', 'help': 'Fixture plugin', 'root': False, 'handler': 'example_plugin:run'},
                             {'name': 'broken', 'help': 'Unavailable fixture plugin', 'root': False,
                              'register': 'broken_plugin:configure_parser', 'handler': 'broken_plugin:run'}]}))
            (example / 'enodectl_component/broken_plugin.py').write_text('raise RuntimeError("Unavailable fixture capability")\n')
            (example / 'enodectl_component/example_plugin.py').write_text(
                'from enodectl_core import registry\nfrom fixture_dependency import VALUE\nfrom textual.app import App\nfrom service_tui import ServiceApp\ndef run(args, parser):\n [registry.resolve(s["view"]) for s in registry.services() if "view" in s]\n print("PLUGIN_OK", VALUE, App.__name__, ServiceApp.__name__, [s["name"] for s in registry.services()])\n')
            target = root / 'enodectl.pyz'
            subprocess.run([python, '-m', 'image_builder.package_enodectl', '--root', str(root), '--target', str(target)],
                           check=True, capture_output=True, text=True)
            with zipfile.ZipFile(target) as archive:
                self.assertFalse(any(name.endswith('/ddns.py') for name in archive.namelist()))
                self.assertIn('site-packages/textual/app.py', archive.namelist())
            # No source catalogue or network access is needed to run the bundle.
            shutil.rmtree(root / 'stage')
            shutil.rmtree(root / 'image_builder')
            environment = {**os.environ, 'SHIV_ROOT': str(root / 'cache')}
            help_result = subprocess.run([sys.executable, '-I', str(target), '--help'], env=environment,
                                        capture_output=True, text=True, check=True)
            self.assertIn('example', help_result.stdout)
            result = subprocess.run([sys.executable, '-I', str(target), 'example'], env=environment,
                                    capture_output=True, text=True, check=True)
            self.assertIn('PLUGIN_OK', result.stdout)
            self.assertIn('DEPENDENCY_OK', result.stdout)
            self.assertIn('App', result.stdout)
            self.assertNotIn('ddns', result.stdout)
            self.assertNotIn('cloudflared', result.stdout)
            cached, = list((root / 'cache').rglob('example_plugin.py'))
            cached.write_text('raise RuntimeError("CACHE_INJECTED")\n')
            modified = subprocess.run([sys.executable, '-I', str(target), 'example'], env=environment,
                                      capture_output=True, text=True)
            self.assertNotEqual(modified.returncode, 0)
            self.assertNotIn('PLUGIN_OK', modified.stdout)


if __name__ == '__main__': unittest.main()
