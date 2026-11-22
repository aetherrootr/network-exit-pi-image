from _stage_support import source_path, source_library, assemble_layers
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('fetch_base', ROOT / 'image_builder/fetch_base.py')
fetch_base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch_base)


class FreshDownloadTests(unittest.TestCase):
    def test_repeated_fetch_downloads_again_and_replaces_old_file(self):
        # Use real curl with a local file URL; no upstream download or socket needed.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            upstream = root / 'upstream.img.xz'
            upstream.write_bytes(b'fixture image')
            config = {'filename': 'base.img.xz', 'url': upstream.as_uri(),
                      'sha256': hashlib.sha256(upstream.read_bytes()).hexdigest()}
            output = root / 'build-source'
            first = fetch_base.fetch_image(config, output)
            self.assertEqual(first.read_bytes(), upstream.read_bytes())
            upstream.write_bytes(b'new fixture image')
            config['sha256'] = hashlib.sha256(upstream.read_bytes()).hexdigest()
            second = fetch_base.fetch_image(config, output)
            self.assertEqual(first, second)
            self.assertEqual(second.read_bytes(), upstream.read_bytes())
            self.assertEqual(list(output.iterdir()), [second])

    def test_bad_digest_rejected_without_replacing_existing_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            upstream = root / 'upstream.img.xz'
            upstream.write_bytes(b'corrupt upstream')
            output = root / 'build-source'
            output.mkdir()
            target = output / 'base.img.xz'
            target.write_bytes(b'previous verified image')
            config = {'filename': target.name, 'url': upstream.as_uri(), 'sha256': '0' * 64}
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                fetch_base.fetch_image(config, output)
            self.assertEqual(target.read_bytes(), b'previous verified image')
            self.assertEqual(list(output.iterdir()), [target])

    def test_partial_download_removed_on_curl_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)

            def failed_curl(command, **kwargs):
                Path(command[-1]).write_bytes(b'partial download')
                raise subprocess.CalledProcessError(18, command)

            with patch.object(fetch_base.subprocess, 'run', side_effect=failed_curl):
                with self.assertRaises(subprocess.CalledProcessError):
                    fetch_base.fetch_image({'filename': 'base.img.xz', 'url': 'https://example.invalid/base',
                                            'sha256': '0' * 64}, output)
            self.assertEqual(list(output.iterdir()), [])

    def test_cli_requires_explicit_destination(self):
        result = subprocess.run(['python3', str(ROOT / 'image_builder/fetch_base.py')],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--output-dir', result.stderr)


if __name__ == '__main__':
    unittest.main()
