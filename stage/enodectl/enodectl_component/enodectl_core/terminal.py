"""Robust keyboard and SGR mouse input for the pinned Textual Linux driver."""
from codecs import getincrementaldecoder
import os
import selectors
from time import monotonic

from textual import events
from textual._parser import ParseError
from textual._xterm_parser import XTermParser
from textual.drivers.linux_driver import LinuxDriver


def clean_input(text):
    # surrogateescape preserves the byte count of a malformed legacy mouse
    # packet for the terminal parser. Never put those invalid bytes in a field.
    return ''.join(char for char in text if not 0xDC80 <= ord(char) <= 0xDCFF)


class TerminalInput:
    PASTE_IDLE_SECONDS = 2.0
    PASTE_MAX_SECONDS = 30.0
    PASTE_START = '\x1b[200~'
    PASTE_END = '\x1b[201~'

    def __init__(self, debug=False):
        self.debug = debug
        self.decoder = getincrementaldecoder('utf-8')(errors='surrogateescape')
        self.parser = XTermParser(debug)
        self.paste_started = None
        self.paste_last = None
        self.marker_tail = ''

    def expire_paste(self, now):
        if self.paste_started is None: return
        if now - self.paste_last < self.PASTE_IDLE_SECONDS and now - self.paste_started < self.PASTE_MAX_SECONDS: return
        # Textual waits indefinitely for a missing bracketed-paste terminator,
        # swallowing later keyboard/mouse input. Discard the incomplete paste
        # without emitting its contents and continue parsing fresh input.
        previous = self.parser
        self.close()
        self.parser = XTermParser(self.debug)
        for attribute in ('terminal_size', 'terminal_pixel_size', 'mouse_pixels', 'last_x', 'last_y'):
            if hasattr(previous, attribute): setattr(self.parser, attribute, getattr(previous, attribute))
        self.decoder.reset()
        self.paste_started = self.paste_last = None
        self.marker_tail = ''

    def observe_paste(self, text, now):
        # Keep only the short suffix needed for a marker split across reads.
        # Do not log input or retain the full pasted configuration here.
        combined = self.marker_tail + text
        start, end = combined.rfind(self.PASTE_START), combined.rfind(self.PASTE_END)
        if start > end and self.paste_started is None: self.paste_started = now
        elif end > start: self.paste_started = self.paste_last = None
        if self.paste_started is not None: self.paste_last = now
        self.marker_tail = combined[-(len(self.PASTE_START) - 1):]

    @staticmethod
    def filtered(messages):
        for message in messages:
            if isinstance(message, events.Key):
                if clean_input(message.key) != message.key: continue
                if message.character and clean_input(message.character) != message.character: continue
            if isinstance(message, events.Paste):
                message = events.Paste(clean_input(message.text))
            yield message

    def feed(self, data):
        now = monotonic()
        self.expire_paste(now)
        text = self.decoder.decode(data)
        # Empty text can be an incomplete valid UTF-8 character, not EOF.
        if text:
            self.observe_paste(text, now)
            yield from self.filtered(self.parser.feed(text))

    def tick(self):
        self.expire_paste(monotonic())
        yield from self.filtered(self.parser.tick())

    def close(self):
        try:
            for _ in self.parser.feed(''): pass
        except (EOFError, ParseError): pass


class TerminalDriver(LinuxDriver):
    """Use SGR mouse reports and tolerate malformed terminal input."""
    def __init__(self, app, *, debug=False, mouse=True, size=None):
        super().__init__(app, debug=debug, mouse=mouse, size=size)

    def _reset_mouse_modes(self):
        # Explicitly reset reporting modes: a previous crashed application may
        # have left X10, VT200, UTF-8 or extended mouse tracking enabled.
        self.write(''.join(f'\x1b[?{mode}l' for mode in (9, 1000, 1002, 1003, 1005, 1006, 1015, 1016)))

    def _enable_mouse_support(self):
        self._reset_mouse_modes()
        if self._mouse:
            # Select SGR encoding before enabling button-event tracking. Click,
            # wheel and drag work without flooding the SSH stream on hover.
            self.write('\x1b[?1006h\x1b[?1002h')
        self.flush()

    def _disable_mouse_support(self):
        self._reset_mouse_modes()
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
