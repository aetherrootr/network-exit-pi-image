"""Reusable Textual presentation widgets; operations are supplied by backends."""
from dataclasses import dataclass
import time
from typing import Callable
from rich.text import Text
from textual import work
from textual.containers import Horizontal, VerticalScroll, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Input, Label, Select, Static, TextArea


def display(value, multiline=True):
    return ''.join(c if 32 <= ord(c) < 127 or (c == '\n' and multiline) else c.encode('ascii', 'backslashreplace').decode()
                   if ord(c) >= 128 else f'\\x{ord(c):02x}' for c in str(value))


@dataclass
class Field:
    name: str
    label: str
    kind: str = 'text'
    options: tuple = ()


class Confirm(ModalScreen):
    DEFAULT_CSS = '''
    Confirm { align: center middle; }
    Confirm > Vertical { width: 72; height: auto; max-height: 90%; padding: 1 2; border: ascii $accent; background: $surface; }
    Confirm Static { height: auto; margin-bottom: 1; }
    Confirm Horizontal { height: 3; }
    '''
    BINDINGS = [('escape', 'cancel', 'Cancel')]

    def __init__(self, message, seconds=None):
        super().__init__()
        self.message, self.seconds = message, seconds

    def compose(self):
        with Vertical():
            yield Static(Text(display(self.message)), id='confirm-message')
            yield Static('', id='countdown')
            with Horizontal():
                yield Button('Cancel', id='confirm-cancel')
                yield Button('Confirm', id='confirm-apply', variant='primary')

    def on_mount(self):
        self.query_one('#confirm-cancel').focus()
        if self.seconds:
            self.deadline = time.monotonic() + self.seconds
            self.set_interval(0.25, self.tick)
            self.tick()

    def tick(self):
        left = max(0, int(self.deadline - time.monotonic()))
        self.query_one('#countdown', Static).update(f'Confirmation expires in {left} seconds.')
        if not left: self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed): self.dismiss(event.button.id == 'confirm-apply')
    def action_cancel(self): self.dismiss(False)


class Details(ModalScreen):
    DEFAULT_CSS = '''
    Details { align: center middle; }
    Details > Vertical { width: 90%; height: 80%; border: ascii $accent; padding: 1; background: $surface; }
    Details VerticalScroll { height: 1fr; }
    Details Static { height: auto; }
    '''
    BINDINGS = [('escape', 'close', 'Back')]

    def __init__(self, text): super().__init__(); self.text = text
    def compose(self):
        with Vertical():
            with VerticalScroll(): yield Static(Text(display(self.text)))
            yield Button('Back', id='details-back')
    def on_button_pressed(self): self.dismiss(None)
    def action_close(self): self.dismiss(None)


class FormScreen(Screen):
    """Render fields and confirmation; backend callables own every side effect."""
    BINDINGS = [('escape', 'back', 'Back')]
    DEFAULT_CSS = '''
    FormScreen { padding: 1 2; }
    FormScreen #form-title { height: 2; text-style: bold; }
    FormScreen #form-help { height: auto; max-height: 4; }
    FormScreen #fields { height: 1fr; border: ascii $accent; padding: 0 1; }
    FormScreen Label { height: auto; margin-top: 1; }
    FormScreen Input, FormScreen Select { border: ascii $accent; }
    FormScreen TextArea { height: 14; border: ascii $accent; }
    FormScreen #form-result { height: auto; max-height: 5; }
    FormScreen Horizontal { height: 3; }
    '''

    def __init__(self, title, fields, load, save, details=None, help_text=''):
        super().__init__()
        self.title, self.fields = title, fields
        self.loader, self.saver, self.detailer = load, save, details
        self.help_text = help_text
        self.busy = False
        self.loaded = {}

    def compose(self):
        yield Static(Text(self.title), id='form-title')
        yield Static(Text(self.help_text), id='form-help')
        with VerticalScroll(id='fields'):
            for field in self.fields:
                yield Label(Text(field.label))
                if field.kind == 'multiline': yield TextArea('', id='field-' + field.name, tab_behavior='focus')
                elif field.kind in ('choice', 'bool'):
                    options = field.options if field.kind == 'choice' else (('No', False), ('Yes', True))
                    yield Select(options, value=options[0][1], allow_blank=False, id='field-' + field.name)
                else: yield Input(password=field.kind == 'secret', id='field-' + field.name)
        yield Static('Loading saved configuration...', id='form-result', markup=False)
        with Horizontal():
            yield Button('Apply configuration', id='form-save', variant='primary')
            if self.detailer: yield Button('Check status', id='form-details')
            yield Button('Back', id='form-back')

    def on_mount(self):
        self.set_busy(True)
        self.load_values()

    def set_busy(self, value):
        self.busy = value
        for button in self.query(Button): button.disabled = value

    def values(self):
        result = {}
        for field in self.fields:
            widget = self.query_one('#field-' + field.name)
            result[field.name] = widget.text if isinstance(widget, TextArea) else widget.value
        return result

    def has_unsaved(self): return self.values() != self.loaded

    def fill(self, values):
        for field in self.fields:
            widget = self.query_one('#field-' + field.name)
            value = values.get(field.name, False if field.kind == 'bool' else '')
            if isinstance(widget, TextArea): widget.load_text(str(value))
            elif isinstance(widget, Select): widget.value = value
            else: widget.value = str(value)
        self.loaded = self.values()
        self.query_one('#form-result', Static).update('Saved settings loaded. Changes apply only after confirmation.')
        self.set_busy(False)

    @work(thread=True, exclusive=True, group='form-load')
    def load_values(self):
        self.app.call_from_thread(self.set_busy, True)
        try: self.app.call_from_thread(self.fill, self.loader())
        except Exception as exc: self.app.call_from_thread(self.failed, exc)

    def failed(self, exc):
        self.set_busy(False)
        self.query_one('#form-result', Static).update(Text(display(str(exc))))

    def on_button_pressed(self, event: Button.Pressed):
        if self.busy: return
        if event.button.id == 'form-save':
            values = self.values()
            self.app.push_screen(Confirm('Apply ' + self.title + '?\nThis may restart the enabled service or change connectivity.'),
                                 lambda yes: self.start_apply(values) if yes else None)
        elif event.button.id == 'form-details':
            self.set_busy(True)
            self.show_details()
        elif event.button.id == 'form-back': self.action_back()

    def start_apply(self, values):
        if self.busy: return
        self.set_busy(True)
        self.apply_values(values)

    @work(thread=True, exclusive=True, group='form-save')
    def apply_values(self, values):
        self.app.call_from_thread(self.set_busy, True)
        try: self.app.call_from_thread(self.applied, self.saver(values))
        except Exception as exc: self.app.call_from_thread(self.failed, exc)

    def applied(self, result):
        from service_config import TrialChange
        self.set_busy(False)
        if isinstance(result, TrialChange):
            self.app.push_screen(Confirm(result.message, seconds=result.seconds),
                                 lambda yes: self.finish_trial(result, yes))
        else:
            self.query_one('#form-result', Static).update(Text(display(result or 'Configuration applied.')))
            # Secrets are never retained in the form after successful saving.
            for field in self.fields:
                if field.kind == 'secret': self.query_one('#field-' + field.name, Input).value = ''
            self.loaded = self.values()

    @work(thread=True, exclusive=True, group='form-save')
    def finish_trial(self, result, keep):
        self.app.call_from_thread(self.set_busy, True)
        try:
            (result.confirm if keep else result.rollback)()
            self.app.call_from_thread(self.fill, self.loader())
            self.app.call_from_thread(self.applied, 'Network settings kept.' if keep else 'Previous network settings restored.')
        except Exception as exc:
            self.app.call_from_thread(self.failed, exc)
            self.app.call_from_thread(self.load_values)

    @work(thread=True, exclusive=True, group='form-details')
    def show_details(self):
        try: self.app.call_from_thread(self.open_details, self.detailer())
        except Exception as exc: self.app.call_from_thread(self.failed, exc)

    def open_details(self, text):
        self.set_busy(False)
        self.app.push_screen(Details(text))

    def action_back(self):
        if self.busy: return
        if self.values() != self.loaded:
            self.app.push_screen(Confirm('Discard unsaved form changes?'), lambda yes: self.app.pop_screen() if yes else None)
        else: self.app.pop_screen()


class ReadOnlyScreen(Screen):
    """Present fixed system policy/status without a mutation or shutdown path."""
    BINDINGS = [('escape', 'back', 'Back')]
    DEFAULT_CSS = '''
    ReadOnlyScreen { padding: 1 2; }
    ReadOnlyScreen VerticalScroll { height: 1fr; border: ascii $accent; }
    ReadOnlyScreen #policy-body { height: auto; }
    ReadOnlyScreen Horizontal { height: 3; }
    '''

    def __init__(self, title, loader):
        super().__init__();self.title=title;self.loader=loader;self.busy=False
    def compose(self):
        yield Static(Text(self.title))
        yield Static('Required system component. Policy changes are delivered through signed image updates.')
        with VerticalScroll(): yield Static('Loading...', id='policy-body')
        with Horizontal():
            yield Button('Refresh', id='policy-refresh')
            yield Button('Back', id='policy-back')
    def on_mount(self): self.refresh_status()
    def refresh_status(self):
        self.busy=True
        for button in self.query(Button):button.disabled=True
        self.load_status()
    @work(thread=True, exclusive=True)
    def load_status(self):
        try: text=self.loader()
        except Exception as exc: text=str(exc)
        self.app.call_from_thread(self.update_body,text)
    def update_body(self, text):
        self.query_one('#policy-body', Static).update(Text(display(text)))
        self.busy=False
        for button in self.query(Button):button.disabled=False
    def on_button_pressed(self, event):
        if self.busy:return
        if event.button.id=='policy-refresh':self.refresh_status()
        else:self.action_back()
    def action_back(self):
        if not self.busy:self.app.pop_screen()
