"""Exercise raw terminal bytes rather than only Textual's headless key events."""
from _stage_support import ROOT
import os
import threading
import unittest
from unittest.mock import Mock
from unittest.mock import patch

from textual import events
from enodectl_core.terminal import TerminalDriver, TerminalInput


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

    def test_missing_paste_end_recovers_ctrl_q_and_arrows_without_emitting_partial_text(self):
        reader=TerminalInput()
        with patch('enodectl_core.terminal.monotonic',side_effect=[0,0.1,0.2,2.3,2.4]):
            self.assertEqual(list(reader.feed(b'\x1b[20')),[])
            self.assertEqual(list(reader.feed(b'0~partial-private-input')),[])
            self.assertEqual(list(reader.feed(b'\x11')),[])
            self.assertEqual(list(reader.tick()),[])
            self.assertEqual([message.key for message in reader.feed(b'\x11\x1b[A')],['ctrl+q','up'])

    def test_complete_split_paste_is_not_discarded(self):
        reader=TerminalInput()
        with patch('enodectl_core.terminal.monotonic',side_effect=[0,0.5,1]):
            self.assertEqual(list(reader.feed(b'\x1b[200~hello')),[])
            self.assertEqual(list(reader.feed(b' world\x1b[20')),[])
            messages=list(reader.feed(b'1~\x11'))
        self.assertEqual([m.text for m in messages if isinstance(m,events.Paste)],['hello world'])
        self.assertEqual([m.key for m in messages if isinstance(m,events.Key)],['ctrl+q'])

    def test_driver_uses_sgr_mouse_without_hover_flooding(self):
        driver = object.__new__(TerminalDriver)
        driver.write, driver.flush = Mock(), Mock()
        driver._mouse = True
        driver._enable_mouse_support()
        control = ''.join(call.args[0] for call in driver.write.call_args_list)
        for mode in (9, 1000, 1002, 1003, 1005, 1006, 1015, 1016):
            self.assertIn(f'\x1b[?{mode}l', control)
        self.assertIn('\x1b[?1006h\x1b[?1002h', control)
        self.assertNotIn('\x1b[?1003h', control)
        driver.flush.assert_called_once()
        messages = list(TerminalInput().feed(b'\x1b[<0;4;5M\x1b[<0;4;5m'))
        self.assertEqual([type(message) for message in messages], [events.MouseDown, events.MouseUp])

    def test_actual_reader_thread_survives_invalid_bytes(self):
        read_fd, write_fd = os.pipe()
        driver = object.__new__(TerminalDriver)
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
