"""Reusable Textual presentation widgets; operations are supplied by backends."""
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import time
from typing import Callable
from rich.text import Text
from textual import work
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Input, Label, Select, Static, TextArea
from textual.worker import get_current_worker


def display(value, multiline=True):
    return ''.join(c if 32 <= ord(c) < 127 or (c == '\n' and multiline) else c.encode('ascii', 'backslashreplace').decode()
                   if ord(c) >= 128 else f'\\x{ord(c):02x}' for c in str(value))


def error_message(exc):
    # Commands and captured output can contain credentials. Backends can raise
    # a meaningful ValueError once they have classified/sanitized the failure.
    if isinstance(exc, (subprocess.CalledProcessError, subprocess.TimeoutExpired)):
        tool = 'External command'
        if isinstance(exc.cmd, (list, tuple)) and exc.cmd and isinstance(exc.cmd[0], str):
            candidate = Path(exc.cmd[0]).name
            if re.fullmatch(r'[A-Za-z0-9_.-]{1,40}', candidate): tool = candidate
        if isinstance(exc, subprocess.CalledProcessError):
            return f'{tool} failed (exit status {exc.returncode}). Check service status and configuration.'
        return f'{tool} timed out after {exc.timeout} seconds. Check connectivity or service status.'
    return str(exc) or type(exc).__name__


NAVIGATION_HELP = 'Up/Down: move | Left/Right: actions, choices or text cursor | Enter: select | Esc: back'
NAVIGATION_BINDINGS = [Binding(key, f'navigate({key!r})', show=False, priority=True)
                       for key in ('up', 'down', 'left', 'right', 'enter', 'escape')]


def navigation_enabled(screen, key):
    """Leave widget-local editing, expanded menus and table row movement intact."""
    focused = screen.focused
    if focused is None:
        return key in ('up', 'down', 'left', 'right')
    if any(isinstance(node, Select) and node.expanded for node in focused.ancestors_with_self):
        return key in ('left', 'right')
    if isinstance(focused, TextArea) and not focused.read_only:
        return key == 'escape'
    if key == 'escape': return False
    if key == 'enter': return isinstance(focused, (Input, TextArea))
    if isinstance(focused, Input) and key in ('left', 'right'): return False
    if isinstance(focused, DataTable):
        if key == 'up' and focused.cursor_row > 0: return False
        if key == 'down' and focused.cursor_row < focused.row_count - 1: return False
    if isinstance(focused, VerticalScroll): return key in ('left', 'right')
    return isinstance(focused, (Button, DataTable, Input, Select, TextArea))


def navigate(screen, key):
    focused = screen.focused
    if key in ('left', 'right') and focused is not None:
        if any(isinstance(node, Select) and node.expanded for node in focused.ancestors_with_self):
            action = getattr(focused, 'action_cursor_up' if key == 'left' else 'action_cursor_down', None)
            if action: action()
            return
        if isinstance(focused, FormSelect):
            focused.cycle(-1 if key == 'left' else 1)
            return
    if isinstance(focused, TextArea) and key in ('enter', 'escape'):
        focused.read_only = key == 'escape'
        return
    if isinstance(screen, (Details, ReadOnlyScreen)) and key in ('up', 'down'):
        body = screen.query_one(VerticalScroll)
        (body.scroll_up if key == 'up' else body.scroll_down)()
        return
    widgets = [widget for widget in screen.focus_chain
               if isinstance(widget, (Button, DataTable, Input, Select, TextArea))]
    if not widgets: return
    if focused not in widgets:
        screen.set_focus(widgets[0])
        return
    direction = -1 if key in ('left', 'up') else 1
    # On table screens, right opens the action row immediately; down reaches
    # it after the last record. Moving up from its first action returns to the table.
    index = widgets.index(focused)
    if key in ('up', 'down') and isinstance(focused, Button) and isinstance(focused.parent, Horizontal):
        # Vertical movement leaves horizontal action rows, while left/right
        # moves along them. A two-button confirmation has no other row.
        for step in range(1, len(widgets)):
            candidate = widgets[(index + direction * step) % len(widgets)]
            if candidate.parent is not focused.parent:
                screen.set_focus(candidate)
                return
    # Widget.focus() queues an asynchronous callback; fast repeated arrows can
    # otherwise see the same old focus and lose movements. Update synchronously.
    screen.set_focus(widgets[(index + direction) % len(widgets)])


class NonFocusableScroll(VerticalScroll, can_focus=False):
    """Scroll with mouse or the screen's bindings, not a stray focus stop."""


class FormSelect(Select):
    def __init__(self, options, **kwargs):
        options = tuple(options)
        self.choices = tuple(value for _, value in options)
        super().__init__(options, **kwargs)

    def cycle(self, direction):
        index = self.choices.index(self.value) if self.value in self.choices else 0
        self.value = self.choices[(index + direction) % len(self.choices)]


class EditTextArea(TextArea):
    """Enter edits multiline content; Escape resumes arrow-key navigation."""
    def on_blur(self): self.read_only = True


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
            yield Static('Left/Right: choose | Enter: select | Esc: cancel')

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
    Details > Vertical { width: 90%; max-width: 110; height: 80%; max-height: 32; border: ascii $accent; padding: 1; background: $surface; }
    Details VerticalScroll { height: 1fr; }
    Details Static { height: auto; }
    Details Horizontal { height: 3; }
    '''
    BINDINGS = [('escape', 'close', 'Back')]

    STATUS_TIMEOUT = 15

    def __init__(self, text='', title='Status / result', loader=None):
        super().__init__()
        self.text, self.title, self.loader = text, title, loader
        self.request = 0
        self.status_timer = None
        self.status_worker = None

    def compose(self):
        with Vertical():
            yield Static(Text(self.title, style='bold'), id='details-title')
            with NonFocusableScroll(id='details-scroll'): yield Static(Text(display(self.text)), id='details-body')
            yield Static('Up/Down: scroll | Left/Right: actions | Enter: select | Esc: back')
            with Horizontal():
                if self.loader: yield Button('Refresh', id='details-refresh')
                yield Button('Back', id='details-back')

    def on_mount(self):
        self.set_focus(self.query_one('#details-back', Button))
        if self.loader: self.refresh_status()

    def on_button_pressed(self, event):
        if event.button.id == 'details-refresh': self.refresh_status()
        else: self.dismiss(None)

    def action_close(self): self.dismiss(None)

    def refresh_status(self):
        if self.status_worker is not None and not self.status_worker.is_finished: return
        self.request += 1
        request = self.request
        self.text = 'Checking service status...\nYou can press Esc or Back at any time.'
        self.query_one('#details-body', Static).update(self.text)
        self.query_one('#details-title', Static).update(Text(self.title, style='bold'))
        self.set_focus(self.query_one('#details-back', Button))
        self.query_one('#details-refresh', Button).disabled = True
        self.status_timer = self.set_timer(self.STATUS_TIMEOUT, lambda: self.status_timeout(request))
        self.status_worker = self.read_status(request)

    @work(thread=True, exclusive=True, group='read-status')
    def read_status(self, request):
        worker = get_current_worker()
        try: text, failed = self.loader(), False
        except Exception as exc: text, failed = error_message(exc), True
        if not worker.is_cancelled:
            try: self.app.call_from_thread(self.accept_status, request, text, failed)
            except RuntimeError:
                if not worker.is_cancelled: raise

    def accept_status(self, request, text, failed=False):
        if request != self.request or not self.is_mounted: return
        if self.status_timer: self.status_timer.stop()
        self.status_worker = None
        self.text = str(text)
        self.query_one('#details-title', Static).update(Text('Status query failed' if failed else self.title, style='bold'))
        self.query_one('#details-body', Static).update(Text(display(text)))
        self.query_one('#details-refresh', Button).disabled = False

    def status_timeout(self, request):
        if request != self.request or self.status_worker is None: return
        self.status_worker.cancel()
        self.accept_status(request, 'Status query timed out. Check connectivity or service health, then retry.', True)
        self.request += 1  # Ignore any result from the cancelled worker.

    def on_unmount(self):
        if self.status_timer: self.status_timer.stop()
        if self.status_worker: self.status_worker.cancel()


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

    def __init__(self, title, fields, load, save, details=None, help_text='', result_dialog=False, logs=None, service_name=None, reload_after_apply=False):
        super().__init__()
        self.title, self.fields = title, fields
        self.loader, self.saver, self.detailer = load, save, details
        self.help_text = help_text
        self.result_dialog = result_dialog
        self.log_loader = logs
        self.service_name = service_name
        self.reload_after_apply = reload_after_apply
        self.startup_enabled = None
        self.busy = False
        self.loaded = {}

    def compose(self):
        yield Static(Text(self.title), id='form-title')
        yield Static(Text(self.help_text), id='form-help')
        with NonFocusableScroll(id='fields'):
            if self.service_name:
                yield Label('Startup (saved: loading...)', id='startup-state')
                yield FormSelect((('OFF - disabled', False), ('ON - enabled', True)), value=False,
                                 allow_blank=False, id='form-startup', disabled=True)
            for field in self.fields:
                yield from self.before_field(field)
                yield Label(Text(field.label))
                if field.kind == 'multiline':
                    yield Label('Enter: edit text | Esc: finish editing')
                    yield EditTextArea('', id='field-' + field.name, read_only=True, tab_behavior='focus')
                elif field.kind in ('choice', 'bool'):
                    options = field.options if field.kind == 'choice' else (('No', False), ('Yes', True))
                    yield FormSelect(options, value=options[0][1], allow_blank=False, id='field-' + field.name)
                else: yield Input(password=field.kind == 'secret', id='field-' + field.name)
        yield Static('Loading saved configuration...', id='form-result', markup=False)
        with Horizontal():
            yield Button('Apply configuration', id='form-save', variant='primary')
            if self.detailer: yield Button('Check status', id='form-details')
            if self.log_loader: yield Button('View logs', id='form-logs')
            yield Button('Back', id='form-back')
        yield Static(NAVIGATION_HELP)

    def before_field(self, field): return ()
    def extra_action(self, button): pass

    def on_mount(self):
        self.set_busy(True)
        if self.fields: self.query_one('#field-' + self.fields[0].name).focus()
        self.load_values()

    def set_busy(self, value):
        self.busy = value
        for button in self.query(Button): button.disabled = value
        if self.service_name:
            self.query_one('#form-startup', Select).disabled = value or self.startup_enabled is None

    def refresh_startup(self):
        if not self.service_name: return
        import components
        self.startup_enabled = bool(components.choices().get(self.service_name))
        self.query_one('#startup-state', Label).update('Startup (saved: ' + ('ON' if self.startup_enabled else 'OFF') + ')')
        self.query_one('#form-startup', Select).value = self.startup_enabled

    def values(self):
        result = {}
        for field in self.fields:
            widget = self.query_one('#field-' + field.name)
            result[field.name] = widget.text if isinstance(widget, TextArea) else widget.value
        return result

    def has_unsaved(self):
        return self.values() != self.loaded or (self.service_name and self.query_one('#form-startup', Select).value != self.startup_enabled)

    def fill(self, values):
        for field in self.fields:
            widget = self.query_one('#field-' + field.name)
            value = values.get(field.name, False if field.kind == 'bool' else '')
            if isinstance(widget, TextArea): widget.load_text(str(value))
            elif isinstance(widget, Select): widget.value = value
            else: widget.value = str(value)
        self.loaded = self.values()
        self.refresh_startup()
        self.query_one('#form-result', Static).update('Saved settings loaded. Changes apply only after confirmation.')
        self.set_busy(False)

    @work(thread=True, exclusive=True, group='form-load')
    def load_values(self):
        self.app.call_from_thread(self.set_busy, True)
        try: self.app.call_from_thread(self.fill, self.loader())
        except Exception as exc: self.app.call_from_thread(self.failed, exc)

    def failed(self, exc):
        self.set_busy(False)
        message = error_message(exc)
        self.query_one('#form-result', Static).update(Text(display(message)))
        self.app.push_screen(Details(message, title='Configuration error'))

    def on_button_pressed(self, event: Button.Pressed):
        if self.busy: return
        if event.button.id == 'form-save':
            values = self.values()
            message = 'Apply ' + self.title + '?\nThis may restart the enabled service or change connectivity.'
            if self.service_name:
                enabled = self.query_one('#form-startup', Select).value
                message += '\nStartup: ' + ('ON - enable and start service' if enabled else 'OFF - save settings without running service')
            self.app.push_screen(Confirm(message),
                                 lambda yes: self.start_apply(values) if yes else None)
        elif event.button.id == 'form-details':
            # Open immediately. Status is read-only and must never block Back
            # or leave the form's controls disabled while a backend responds.
            self.app.push_screen(Details(title=self.title + ' status', loader=self.detailer))
        elif event.button.id == 'form-logs':
            self.app.push_screen(Details(title=self.title + ' logs', loader=self.log_loader))
        elif event.button.id == 'form-back': self.action_back()
        else: self.extra_action(event.button.id)

    def start_apply(self, values):
        if self.busy: return
        startup = bool(self.query_one('#form-startup', Select).value) if self.service_name else None
        self.set_busy(True)
        self.query_one('#form-result', Static).update('Applying configuration...')
        self.apply_values(values, startup, values != self.loaded)

    @work(thread=True, exclusive=True, group='form-save')
    def apply_values(self, values, startup=None, changed=True):
        self.app.call_from_thread(self.set_busy, True)
        try:
            if self.service_name:
                from service_manager import apply_service_settings
                result = apply_service_settings(self.service_name, startup, changed, lambda: self.saver(values))
            else: result = self.saver(values)
            if self.reload_after_apply:
                saved = self.loader()
                self.app.call_from_thread(self.applied_with_values, result, saved)
            else:
                self.app.call_from_thread(self.applied, result)
        except Exception as exc:
            if self.service_name: self.app.call_from_thread(self.refresh_startup)
            self.app.call_from_thread(self.failed, exc)

    def applied_with_values(self, result, saved):
        self.fill(saved)
        self.applied(result)

    def applied(self, result):
        from service_config import TrialChange
        self.refresh_startup()
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
            if self.result_dialog and result: self.app.push_screen(Details(result))

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

    def action_back(self):
        if self.busy: return
        if self.has_unsaved():
            self.app.push_screen(Confirm('Discard unsaved form changes?'), lambda yes: self.app.pop_screen() if yes else None)
        else: self.app.pop_screen()


class ReadOnlyScreen(Screen):
    """Present fixed system policy/status without a mutation or shutdown path."""
    BINDINGS = [('escape', 'back', 'Back'), ('pageup', 'scroll(-1)', ''), ('pagedown', 'scroll(1)', '')]
    DEFAULT_CSS = '''
    ReadOnlyScreen { padding: 1 2; }
    ReadOnlyScreen VerticalScroll { height: 1fr; border: ascii $accent; }
    ReadOnlyScreen #policy-body { height: auto; }
    ReadOnlyScreen Horizontal { height: 3; }
    '''

    def __init__(self, title, loader):
        super().__init__();self.title=title;self.loader=loader;self.busy=False
        self.request=0;self.status_timer=None;self.status_worker=None
    def compose(self):
        yield Static(Text(self.title))
        yield Static('Required system component. Policy changes are delivered through signed image updates.')
        with NonFocusableScroll(): yield Static('Loading...', id='policy-body')
        with Horizontal():
            yield Button('Refresh', id='policy-refresh')
            yield Button('Back', id='policy-back')
        yield Static('Up/Down: scroll | Left/Right: actions | Enter: select | Esc: back')
    def on_mount(self): self.refresh_status()
    def refresh_status(self):
        self.busy=True
        self.request += 1
        request = self.request
        self.query_one('#policy-refresh', Button).disabled=True
        self.query_one('#policy-body', Static).update('Checking status... You can press Esc to go back.')
        self.set_focus(self.query_one('#policy-back', Button))
        self.status_timer = self.set_timer(Details.STATUS_TIMEOUT, lambda: self.status_timeout(request))
        self.status_worker = self.load_status(request)
    @work(thread=True, exclusive=True)
    def load_status(self, request):
        worker = get_current_worker()
        try: text=self.loader()
        except Exception as exc: text='Unable to read status: ' + error_message(exc)
        if not worker.is_cancelled:
            try: self.app.call_from_thread(self.update_body,request,text)
            except RuntimeError:
                if not worker.is_cancelled: raise
    def update_body(self, request, text):
        if request != self.request or not self.is_mounted: return
        if self.status_timer: self.status_timer.stop()
        self.status_worker=None
        self.query_one('#policy-body', Static).update(Text(display(text)))
        self.busy=False
        for button in self.query(Button):button.disabled=False
        if not isinstance(self.focused, Button): self.query_one('#policy-refresh', Button).focus()
    def status_timeout(self, request):
        if request != self.request or self.status_worker is None: return
        self.status_worker.cancel()
        self.update_body(request, 'Status query timed out. Go back or refresh to retry.')
        self.request += 1
    def on_unmount(self):
        if self.status_timer: self.status_timer.stop()
        if self.status_worker: self.status_worker.cancel()
    def on_button_pressed(self, event):
        if event.button.id=='policy-refresh':
            if not self.busy: self.refresh_status()
        else:self.action_back()
    def action_back(self):
        self.app.pop_screen()
    def action_scroll(self, direction):
        body = self.query_one(VerticalScroll)
        (body.scroll_page_up if direction < 0 else body.scroll_page_down)()
