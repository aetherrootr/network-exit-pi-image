"""Exercise raw terminal bytes rather than only Textual's headless key events."""
from _stage_support import ROOT
import os
import threading
import unittest
from unittest.mock import Mock

from textual import events
from enodectl_core.terminal import KeyboardDriver, TerminalInput


class TerminalInputTests(unittest.TestCase):
    def test_invalid_legacy_mouse_packet_does_not_break_next_arrow_or_enter(self):
        reader = TerminalInput()
        # X10 mouse report with a non-UTF-8 coordinate, as seen on the board.
        self.assertEqual(list(reader.feed(b'\x1b[M \x8c!')), [])
        messages = list(reader.feed(b'\x8c\x1b[B\r'))
        self.assertEqual([message.key for message in messages], ['down', 'enter'])

    def test_split_utf8_and_paste_keep_valid_characters_only(self):
        reader = TerminalInput()
        self.assertEqual(list(reader.feed(b'\xe5\xae')), [])
        messages = list(reader.feed(b'\xb6'))
        self.assertEqual([message.character for message in messages], ['\u5bb6'])
        messages = list(reader.feed(b'\x1b[200~A\x8cB\x1b[201~'))
        self.assertEqual([message.text for message in messages if isinstance(message, events.Paste)], ['AB'])

    def test_driver_explicitly_disables_mouse_reporting(self):
        driver = object.__new__(KeyboardDriver)
        driver.write, driver.flush = Mock(), Mock()
        driver._enable_mouse_support()
        control = driver.write.call_args.args[0]
        for mode in (9, 1000, 1002, 1003, 1005, 1006, 1015, 1016):
            self.assertIn(f'\x1b[?{mode}l', control)
        driver.flush.assert_called_once()

    def test_actual_reader_thread_survives_invalid_bytes(self):
        read_fd, write_fd = os.pipe()
        driver = object.__new__(KeyboardDriver)
        driver.fileno = read_fd
        driver._debug = False
        driver.exit_event = threading.Event()
        received = []
        finished = threading.Event()
        failures = []

        def receive(message):
            if isinstance(message, events.Key):
                received.append(message.key)
                if message.key == 'enter': finished.set()

        def read():
            try: driver.run_input_thread()
            except Exception as exc: failures.append(exc)

        driver.process_message = receive
        thread = threading.Thread(target=read)
        thread.start()
        try:
            os.write(write_fd, b'\x1b[M \x8c!\x8c\x1b[A\r')
            self.assertTrue(finished.wait(3), 'input thread stopped processing valid keys')
            self.assertEqual(received, ['up', 'enter'])
        finally:
            driver.exit_event.set()
            thread.join(timeout=3)
            os.close(read_fd)
            os.close(write_fd)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
