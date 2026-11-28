"""Exercise raw terminal bytes rather than only Textual's headless key events."""
from _stage_support import ROOT
import os
import fcntl
import json
import pty
import select
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
from pathlib import Path
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

    def test_late_split_paste_end_delivers_text_and_immediate_keys(self):
        reader=TerminalInput(); clock=[0.0]
        with patch('enodectl_core.terminal.monotonic',side_effect=lambda:clock[0]), \
                patch('textual._parser.get_time',side_effect=lambda:clock[0]):
            self.assertEqual(list(reader.feed(b'\x1b[200~fixture-text')),[])
            clock[0]=0.1; self.assertEqual(list(reader.feed(b'\x1b[20')),[])
            # The terminator arrives after Textual's escape timeout, but
            # before our enclosing paste idle deadline.
            clock[0]=1.0; self.assertEqual(list(reader.tick()),[])
            clock[0]=1.1; messages=list(reader.feed(b'1~\x1b[A\x11'))
            self.assertEqual([m.text for m in messages if isinstance(m,events.Paste)],['fixture-text'])
            self.assertEqual([m.key for m in messages if isinstance(m,events.Key)],['up','ctrl+q'])

    def test_every_byte_boundary_and_multiple_pastes_preserve_event_order(self):
        text='line one\nUnicode: 家\tline two'
        data=b'\x1b[200~'+text.encode()+b'\x1b[201~\x1b[B\x1b[200~next\x1b[201~\r'
        for split in range(1,len(data)):
            with self.subTest(split=split):
                reader=TerminalInput()
                messages=list(reader.feed(data[:split]))+list(reader.feed(data[split:]))
                self.assertEqual([(type(m),m.text if isinstance(m,events.Paste) else m.key) for m in messages],
                                 [(events.Paste,text),(events.Key,'down'),(events.Paste,'next'),(events.Key,'enter')])

    def test_escape_like_payload_is_not_interpreted_as_navigation(self):
        reader=TerminalInput()
        payload=b'a\x1b[A\x11\x1b[200~\x00b'
        messages=list(reader.feed(b'\x1b[200~'+payload+b'\x1b[201~\x1b[B'))
        self.assertEqual([m.text for m in messages if isinstance(m,events.Paste)],['a\x1b[A\x11\x1b[200~b'])
        self.assertEqual([m.key for m in messages if isinstance(m,events.Key)],['down'])

    def test_paste_maximum_duration_discards_buffer_even_with_continuous_input(self):
        reader=TerminalInput(); clock=[0.0]
        with patch('enodectl_core.terminal.monotonic',side_effect=lambda:clock[0]):
            self.assertEqual(list(reader.feed(b'\x1b[200~fixture')),[])
            for second in range(1,30):
                clock[0]=second; self.assertEqual(list(reader.feed(b'x')),[])
            clock[0]=30; self.assertEqual(list(reader.tick()),[])
            self.assertEqual([m.key for m in reader.feed(b'\x11\x1b[B')],['ctrl+q','down'])

    def test_standalone_escape_and_fragmented_arrow_still_work(self):
        reader=TerminalInput(); clock=[0.0]
        with patch('enodectl_core.terminal.monotonic',side_effect=lambda:clock[0]), \
                patch('textual._parser.get_time',side_effect=lambda:clock[0]):
            self.assertEqual(list(reader.feed(b'\x1b')),[])
            clock[0]=0.11; self.assertEqual([m.key for m in reader.tick()],['escape'])
            self.assertEqual(list(reader.feed(b'\x1b[')),[])
            self.assertEqual([m.key for m in reader.feed(b'A')],['up'])

    def test_late_start_suffix_cannot_enter_textuals_paste_mode(self):
        reader=TerminalInput(); clock=[0.0]
        with patch('enodectl_core.terminal.monotonic',side_effect=lambda:clock[0]), \
                patch('textual._parser.get_time',side_effect=lambda:clock[0]):
            self.assertEqual(list(reader.feed(b'\x1b')),[])
            clock[0]=0.11; self.assertEqual([m.key for m in reader.tick()],['escape'])
            clock[0]=0.12
            messages=list(reader.feed(b'[200~late\x1b[201~\x1b[A\x11'))
            self.assertEqual([m.key for m in messages if isinstance(m,events.Key)][-2:],['up','ctrl+q'])
            self.assertEqual([m for m in messages if isinstance(m,events.Paste)],[])

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

    def test_real_pty_delivers_single_line_and_large_multiline_pastes(self):
        # Both focused widget types must receive exactly one paste event from
        # the actual driver thread, and navigation must work immediately after.
        child = r"""
import json, sys
from pathlib import Path
from textual.app import App
from textual.binding import Binding
from textual.widgets import Input, TextArea
from enodectl_core.terminal import TerminalDriver
class FixtureApp(App):
    BINDINGS = [Binding('down', 'edit_area', priority=True), Binding('ctrl+q', 'finish', priority=True)]
    def __init__(self): super().__init__(driver_class=TerminalDriver)
    def compose(self):
        yield Input(id='single')
        yield TextArea(id='multiple')
    def on_mount(self):
        self.screen.set_focus(self.query_one(Input))
        Path(sys.argv[1], 'ready').touch()
    def action_edit_area(self):
        self.screen.set_focus(self.query_one(TextArea))
        Path(sys.argv[1], 'focused').touch()
    def action_finish(self): self.exit([self.query_one(Input).value, self.query_one(TextArea).text])
result = FixtureApp().run()
Path(sys.argv[1], 'result.json').write_text(json.dumps(result))
"""
        with tempfile.TemporaryDirectory() as directory:
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 32, 110, 0, 0))
            env = {**os.environ, 'TERM': 'xterm-256color', 'PYTHONPATH': os.pathsep.join(sys.path)}
            process = subprocess.Popen([sys.executable, '-c', child, directory], stdin=slave, stdout=slave,
                                       stderr=slave, env=env, start_new_session=True)
            os.close(slave)
            os.set_blocking(master, False)
            output = bytearray()

            def drain_until(condition, timeout):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline and not condition():
                    if select.select([master], [], [], 0.05)[0]:
                        try: output.extend(os.read(master, 16384))
                        except OSError: break
                return condition()

            def send(data):
                deadline = time.monotonic() + 10
                while data and time.monotonic() < deadline:
                    readable, writable, _ = select.select([master], [master], [], 0.05)
                    if readable: output.extend(os.read(master, 16384))
                    if writable: data = data[os.write(master, data[:1024]):]
                self.assertFalse(data, 'PTY stopped accepting input')

            def fragmented_paste(text, followup):
                send(b'\x1b[200~' + text.encode() + b'\x1b[20')
                drain_until(lambda: False, 0.4)
                send(b'1~' + followup)

            try:
                self.assertTrue(drain_until(lambda: b'\x1b[?2004h' in output and Path(directory, 'ready').exists(), 10),
                                'PTY app did not start')
                fragmented_paste('fixture-家\nignored', b'\x1b[B')
                self.assertTrue(drain_until(lambda: Path(directory, 'focused').exists(), 10), 'Down did not reach app')
                multiline = ('  {"value": "fixture-家"},\n' * 250).rstrip('\n')
                fragmented_paste(multiline, b'')
                drain_until(lambda: False, 0.5)
                send(b'\x11')
                self.assertTrue(drain_until(lambda: process.poll() is not None, 10), 'Ctrl+Q did not exit')
                self.assertEqual(process.returncode, 0)
                self.assertEqual(json.loads(Path(directory,'result.json').read_text()), ['fixture-家', multiline])
            finally:
                if process.poll() is None:
                    process.terminate()
                    try: process.wait(timeout=5)
                    except subprocess.TimeoutExpired: process.kill(); process.wait()
                os.close(master)

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
