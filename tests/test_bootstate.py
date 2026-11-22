from _stage_support import source_path, source_library, assemble_layers
import importlib.util
import importlib.machinery
import io
import json
import sys
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bootrecords', source_path('usr/lib/network-exit/bootstate.py'))
records = importlib.util.module_from_spec(spec)
spec.loader.exec_module(records)


class BootRecordTests(unittest.TestCase):
    def seed(self, path):
        with path.open('wb') as stream:
            stream.truncate(records.STRIDE * 2)
            for bank in (0, 1):
                stream.seek(bank * records.STRIDE)
                stream.write(records.encode(records.initial()))

    def test_crc_and_torn_alternate_record_preserve_confirmed_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'control'
            self.seed(path)
            actual = os.pwrite
            def interrupted(fd, data, offset):
                actual(fd, data[:10], offset)
                raise OSError('simulated power loss')
            with patch.object(records.os, 'pwrite', side_effect=interrupted):
                with self.assertRaises(OSError):
                    records.update(path, lambda state: {**state, 'ne_pending':'B', 'ne_left':'3'})
            self.assertEqual(records.load(path), records.initial())

    def test_short_writes_and_corrupt_newest_bank(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'control'
            self.seed(path)
            actual = os.pwrite
            with patch.object(records.os, 'pwrite', side_effect=lambda fd, data, offset: actual(fd, data[:37], offset)):
                pending = records.update(path, lambda state: {**state, 'ne_pending':'B', 'ne_left':'3'})
            self.assertEqual(records.load(path), pending)
            with path.open('r+b') as stream:
                stream.seek(records.STRIDE + 20)
                stream.write(b'corrupt')
            self.assertEqual(records.load(path)['ne_confirmed'], 'A')

    def test_three_failed_trials_return_to_confirmed_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'control'
            self.seed(path)
            records.update(path, lambda state: {**state, 'ne_pending':'B', 'ne_left':'3'})
            for expected in (2, 1, 0):
                slot, consumed = records.select_for_test(records.load(path))
                self.assertEqual(slot, 'B')
                self.assertEqual(int(consumed['ne_left']), expected)
                records.update(path, lambda old: consumed)
            slot, _ = records.select_for_test(records.load(path))
            self.assertEqual(slot, 'A')
            # Health confirmation preserves identities elsewhere and commits B.
            records.update(path, lambda state: {**state, 'ne_confirmed':'B', 'ne_pending':'none', 'ne_left':'0'})
            self.assertEqual(records.select_for_test(records.load(path))[0], 'B')

    def test_invalid_slot_and_missing_boot_context_are_rejected(self):
        with self.assertRaises(ValueError): records.encode({**records.initial(), 'ne_pending':'C'})
        for cmdline in ['', 'rauc.slot=C', 'rauc.slot=A rauc.slot=B']:
            with self.assertRaises(ValueError): records.current_slot(cmdline)


class BootBackendTests(unittest.TestCase):
    seed = BootRecordTests.seed
    def test_rauc_install_sequence_only_confirms_the_running_pending_slot(self):
        loader = importlib.machinery.SourceFileLoader('backend', str(source_path('usr/lib/network-exit/boot-backend')))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        backend = importlib.util.module_from_spec(spec)
        loader.exec_module(backend)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); self.seed(root/'control')
            (root/'platform').write_text(json.dumps({'boot_control':str(root/'control')}))
            with patch.object(backend,'PLATFORM',root/'platform'), patch.object(backend,'current_slot',return_value='A'), \
                    patch.object(backend.trial, 'LOCK', root/'trial.lock'), \
                    patch.object(backend.trial, 'is_trial', return_value=False):
                def call(*args):
                    with patch.object(sys,'argv',['backend',*args]): backend.main()
                call('set-state','B','bad')
                self.assertEqual(records.load(root/'control')['ne_B'],'0')
                # Match RAUC: invalidate, write payload, then activate directly.
                # No synthetic set-state good is inserted before activation.
                self.assertEqual(records.select_for_test(records.load(root/'control'))[0], 'A')
                call('set-primary','B')
                self.assertEqual(records.load(root/'control')['ne_B'],'1')
                self.assertEqual(records.load(root/'control')['ne_confirmed'],'A')
                self.assertEqual(records.load(root/'control')['ne_left'],'3')
                call('set-state','B','good')
                self.assertEqual(records.load(root/'control')['ne_confirmed'],'A')
                with self.assertRaises(ValueError): call('set-state','A','bad')
                with self.assertRaises(ValueError): call('set-primary','A')
                with patch.object(backend,'current_slot',return_value='B'):
                    call('set-state','B','good')
                state=records.load(root/'control')
                self.assertEqual((state['ne_confirmed'],state['ne_pending'],state['ne_left']),('B','none','0'))
