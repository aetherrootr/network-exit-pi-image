"""Robust keyboard and SGR mouse input for the pinned Textual Linux driver."""
from codecs import getincrementaldecoder
import os
import selectors
from time import monotonic

from textual import constants, events
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
        self.decoder = getincrementaldecoder('utf-8')(errors='surrogateescape')
        self.parser = XTermParser(debug)
        self.paste_started = None
        self.paste_last = None
        self.paste_parts = []
        self.marker_tail = ''
        self.marker_at = None

    def discard_paste(self):
        self.paste_started = self.paste_last = None
        self.paste_parts.clear()
        self.marker_tail = ''
        self.marker_at = None
        self.decoder.reset()

    def expire_paste(self, now):
        if self.paste_started is not None and (
            now - self.paste_last >= self.PASTE_IDLE_SECONDS or
            now - self.paste_started >= self.PASTE_MAX_SECONDS
        ):
            # An incomplete paste is never replayed as keyboard commands.
            self.discard_paste()

    @staticmethod
    def marker_suffix(text, marker):
        for length in range(min(len(text), len(marker) - 1), 0, -1):
            if text.endswith(marker[:length]): return length
        return 0

    @staticmethod
    def filtered(messages):
        for message in messages:
            if isinstance(message, events.Key):
                if clean_input(message.key) != message.key: continue
                if message.character and clean_input(message.character) != message.character: continue
            yield message

    def feed(self, data):
        now = monotonic()
        self.expire_paste(now)
        if data and self.paste_started is not None: self.paste_last = now
        text = self.decoder.decode(data)
        if not text: return  # An incomplete UTF-8 character is not EOF.
        text = self.marker_tail + text
        self.marker_tail = ''
        self.marker_at = None
        # Frame bracketed paste *before* Textual's escape parser. Its short
        # escape timeout otherwise breaks a fragmented end marker and leaves
        # the parser swallowing keyboard/mouse events. The payload must never
        # be parsed as individual terminal commands (or logged by Textual).
        while text:
            in_paste = self.paste_started is not None
            marker = self.PASTE_END if in_paste else self.PASTE_START
            index = text.find(marker)
            if index < 0:
                tail_length = self.marker_suffix(text, marker)
                if tail_length:
                    self.marker_tail = text[-tail_length:]
                    self.marker_at = now
                    text = text[:-tail_length]
                if in_paste:
                    self.paste_parts.append(text)
                elif text:
                    yield from self.filtered(self.parser.feed(text))
                return
            before, text = text[:index], text[index + len(marker):]
            if in_paste:
                self.paste_parts.append(before)
                payload = clean_input(''.join(self.paste_parts)).replace('\x00', '')
                self.paste_parts.clear()
                self.paste_started = self.paste_last = None
                if payload: yield events.Paste(payload)
            else:
                if before: yield from self.filtered(self.parser.feed(before))
                self.paste_started = self.paste_last = now

    def tick(self):
        now = monotonic()
        self.expire_paste(now)
        if self.paste_started is not None: return
        if self.marker_tail:
            # A lone Escape is a normal navigation key, not necessarily a
            # paste prefix. Keep longer partial start markers across SSH reads.
            timeout = constants.ESCAPE_DELAY if self.marker_tail == '\x1b' else self.PASTE_IDLE_SECONDS
            if now - self.marker_at >= timeout:
                tail, self.marker_tail = self.marker_tail, ''
                self.marker_at = None
                # Do not feed an expired prefix back to Textual: a late
                # suffix could put it into its own untracked paste mode.
                if tail == '\x1b': yield events.Key('escape', tail)
        yield from self.filtered(self.parser.tick())

    def close(self):
        self.discard_paste()
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
