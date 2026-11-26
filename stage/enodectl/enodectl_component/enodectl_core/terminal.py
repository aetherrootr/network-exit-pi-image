"""Keyboard-first terminal input for the pinned Textual Linux driver."""
from codecs import getincrementaldecoder
import os
import selectors

from textual import events
from textual._parser import ParseError
from textual._xterm_parser import XTermParser
from textual.drivers.linux_driver import LinuxDriver


def clean_input(text):
    # surrogateescape preserves the byte count of a malformed legacy mouse
    # packet for the terminal parser. Never put those invalid bytes in a field.
    return ''.join(char for char in text if not 0xDC80 <= ord(char) <= 0xDCFF)


class TerminalInput:
    def __init__(self, debug=False):
        self.decoder = getincrementaldecoder('utf-8')(errors='surrogateescape')
        self.parser = XTermParser(debug)

    @staticmethod
    def filtered(messages):
        for message in messages:
            if isinstance(message, events.MouseEvent): continue
            if isinstance(message, events.Key):
                if clean_input(message.key) != message.key: continue
                if message.character and clean_input(message.character) != message.character: continue
            if isinstance(message, events.Paste):
                message = events.Paste(clean_input(message.text))
            yield message

    def feed(self, data):
        text = self.decoder.decode(data)
        # Empty text can be an incomplete valid UTF-8 character, not EOF.
        if text: yield from self.filtered(self.parser.feed(text))

    def tick(self): yield from self.filtered(self.parser.tick())

    def close(self):
        try:
            for _ in self.parser.feed(''): pass
        except (EOFError, ParseError): pass


class KeyboardDriver(LinuxDriver):
    """Avoid legacy mouse reports and tolerate malformed terminal input."""
    def __init__(self, app, *, debug=False, mouse=True, size=None):
        super().__init__(app, debug=debug, mouse=False, size=size)

    def _enable_mouse_support(self):
        # Explicitly reset reporting modes: a previous crashed application may
        # have left X10, VT200, UTF-8 or extended mouse tracking enabled.
        self.write(''.join(f'\x1b[?{mode}l' for mode in (9, 1000, 1002, 1003, 1005, 1006, 1015, 1016)))
        self.flush()

    def run_input_thread(self):
        reader = TerminalInput(self._debug)
        try:
            with selectors.SelectSelector() as selector:
                selector.register(self.fileno, selectors.EVENT_READ)
                while not self.exit_event.is_set():
                    for _, mask in selector.select(0.1):
                        if mask & selectors.EVENT_READ:
                            data = os.read(self.fileno, 4096)
                            if not data: return
                            for message in reader.feed(data): self.process_message(message)
                    for message in reader.tick(): self.process_message(message)
        finally:
            reader.close()
