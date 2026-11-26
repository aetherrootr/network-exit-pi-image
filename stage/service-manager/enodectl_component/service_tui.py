"""Textual service dashboard. All state changes live in service_manager/backends."""
import sys
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, DataTable, Static
from enodectl_core import registry
from enodectl_core.terminal import KeyboardDriver
from enodectl_core.textual_ui import Confirm, Details, display, error_message, NAVIGATION_BINDINGS, navigation_enabled, navigate
import service_manager


class ServiceApp(App):
    TITLE = 'Embedded Node Controller'
    BINDINGS = [('ctrl+q', 'quit', 'Quit'), ('f5', 'reload', 'Refresh'), *NAVIGATION_BINDINGS]
    CSS = '''
    Screen { padding: 1 2; }
    #title { height: 2; text-style: bold; }
    #services { height: 1fr; border: ascii $accent; }
    #selection-info { height: auto; max-height: 3; }
    #result { height: auto; max-height: 4; }
    Horizontal { height: 3; }
    Button { min-width: 10; }
    Button:focus { text-style: bold reverse; }
    DataTable:focus, Input:focus, Select:focus, TextArea:focus { border: ascii $success; }
    '''

    def __init__(self, model=None, first_boot=False):
        super().__init__(driver_class=KeyboardDriver)
        self.model, self.first_boot = model, first_boot
        self.records = {}
        self.selected = None
        self.busy = False

    def compose(self) -> ComposeResult:
        yield Static('Embedded Node Controller - Services', id='title')
        yield Static('Select a service to edit saved settings. Required services cannot be disabled.', id='selection-info')
        yield DataTable(id='services', cursor_type='row')
        with Horizontal():
            yield Button('Configure / view', id='configure', variant='primary')
            yield Button('Stage toggle', id='toggle')
            yield Button('Apply changes', id='apply')
            yield Button('Refresh', id='refresh')
        yield Static('Loading service state...', id='result', markup=False)
        with Horizontal():
            yield Button('Finish setup', id='finish')
            yield Button('Quit', id='quit')
        yield Static('Up/Down: service | Right: actions | Enter: configure/select | F5: refresh | Ctrl+Q: quit')

    def on_mount(self):
        self.query_one('#services', DataTable).add_columns('Service', 'Startup', 'Runtime', 'Policy')
        self.query_one('#services', DataTable).focus()
        self.set_busy(True)
        self.reload_records()

    def check_action(self, action, parameters):
        if action == 'navigate': return navigation_enabled(self.screen, parameters[0])
        return super().check_action(action, parameters)

    def action_navigate(self, key): navigate(self.screen, key)

    def set_busy(self, value):
        self.busy = value
        for button in self.query(Button): button.disabled = value
        if not value: self.update_buttons()

    def update_buttons(self):
        row = self.records.get(self.selected, {})
        self.query_one('#toggle', Button).disabled = self.busy or not row or bool(row.get('mandatory'))
        self.query_one('#configure', Button).disabled = self.busy or not bool(row.get('view'))

    def show_records(self, records):
        previous = self.selected
        self.records = {row['name']: row for row in records}
        table = self.query_one('#services', DataTable)
        table.clear()
        for row in records:
            table.add_row(Text(display(row['label'])), 'ON' if row['enabled'] else 'OFF',
                          Text(display(row['state'])), 'Required' if row.get('mandatory') else 'Optional', key=row['name'])
        if records:
            index = next((i for i, row in enumerate(records) if row['name'] == previous), 0)
            table.move_cursor(row=index)
            self.selected = records[index]['name']
        self.set_busy(False)
        changes = self.model.changes()
        self.query_one('#result', Static).update('Unsaved startup choices: ' + ', '.join(changes) if changes else 'Saved startup choices. Configuration forms have their own Apply confirmation.')

    @work(thread=True, exclusive=True, group='service-read')
    def reload_records(self, preserve=True):
        self.call_from_thread(self.set_busy, True)
        try:
            if self.model is None: self.model = service_manager.ServiceModel()
            else: self.model.refresh(preserve=preserve)
            self.call_from_thread(self.show_records, self.model.records())
        except Exception as exc: self.call_from_thread(self.failed, exc)

    def failed(self, exc):
        self.set_busy(False)
        message = error_message(exc)
        self.query_one('#result', Static).update(Text(display(message)))
        self.push_screen(Details(message, title='Service configuration error'))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted):
        if event.data_table.id != 'services': return
        self.selected = str(event.row_key.value)
        self.update_buttons()

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        if event.data_table.id != 'services': return
        self.selected = str(event.row_key.value)
        self.open_selected()

    def open_selected(self):
        if self.busy: return
        reference = self.records.get(self.selected, {}).get('view')
        if reference:
            try: self.push_screen(registry.resolve(reference)(), lambda result: self.reload_records())
            except Exception as exc: self.failed(exc)

    def on_button_pressed(self, event: Button.Pressed):
        if self.busy: return
        button = event.button.id
        if button == 'quit': self.action_quit()
        elif button == 'refresh': self.reload_records()
        elif button == 'configure': self.open_selected()
        elif button == 'toggle':
            try:
                self.model.toggle(self.selected)
                # The model owns staged values; this refresh reads runtime only.
                self.reload_records()
            except Exception as exc: self.failed(exc)
        elif button in ('apply', 'finish'):
            message = 'Apply selected startup changes now?' if button == 'apply' else 'Apply startup choices and confirm setup health? SSH keys and Tailscale authorization must be ready.'
            if button == 'finish':
                for stage in registry.stages():
                    if stage.get('setup_notice'): message += '\n' + stage['setup_notice']
            self.push_screen(Confirm(message), lambda yes: self.start_operation(button == 'finish') if yes else None)

    def start_operation(self, finish):
        if self.busy: return
        self.set_busy(True)
        self.apply_changes(finish)

    @work(thread=True, exclusive=True, group='service-write')
    def apply_changes(self, finish=False):
        self.call_from_thread(self.set_busy, True)
        try:
            self.model.apply()
            if finish: service_manager.finish_setup()
            self.call_from_thread(self.show_records, self.model.records())
            if finish and self.first_boot: self.call_from_thread(self.exit, True)
            else: self.call_from_thread(self.show_message, 'Setup confirmed.' if finish else 'Startup choices applied.')
        except Exception as exc:
            self.model.refresh()
            self.call_from_thread(self.show_records, self.model.records())
            self.call_from_thread(self.failed, exc)

    def action_reload(self):
        if len(self.screen_stack) == 1 and not self.busy: self.reload_records()

    def show_message(self, message): self.query_one('#result', Static).update(message)

    def action_quit(self):
        if self.busy or getattr(self.screen, 'busy', False): return
        unsaved = any(getattr(screen, 'has_unsaved', lambda: False)() for screen in self.screen_stack)
        if (self.model is not None and self.model.changes()) or unsaved or self.first_boot:
            self.push_screen(Confirm('Discard unapplied changes and quit? Incomplete first-boot setup will be offered again.'), lambda yes: self.exit(False) if yes else None)
        else: self.exit(False)


def configure():
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError('The service dashboard requires an interactive terminal; connect with `ssh -t`')
    return ServiceApp().run()
