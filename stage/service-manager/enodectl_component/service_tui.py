"""Textual service dashboard. All state changes live in service_manager/backends."""
import sys
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, DataTable, Static
from enodectl_core import registry
from enodectl_core.terminal import TerminalDriver
from enodectl_core.textual_ui import Confirm, Details, display, error_message, NAVIGATION_BINDINGS, navigation_enabled, navigate
import service_manager
import service_diagnostics
import status_summary


class ServiceApp(App):
    TITLE = 'Embedded Node Controller'
    BINDINGS = [('ctrl+q', 'quit', 'Quit'), ('f5', 'reload', 'Refresh'), *NAVIGATION_BINDINGS]
    CSS = '''
    Screen { padding: 1 2; }
    #title { height: 2; text-style: bold; }
    #bootchain-summary { height: auto; max-height: 2; }
    #services { height: 1fr; border: ascii $accent; }
    #selection-info { height: auto; max-height: 3; }
    #result { height: auto; max-height: 4; }
    Horizontal { height: 3; }
    Button { min-width: 10; }
    Button:focus { text-style: bold reverse; }
    DataTable:focus, Input:focus, Select:focus, TextArea:focus { border: ascii $success; }
    '''

    def __init__(self, model=None, first_boot=False):
        super().__init__(driver_class=TerminalDriver)
        self.model, self.first_boot = model, first_boot
        self.records = {}
        self.selected = None
        self.busy = False

    def compose(self) -> ComposeResult:
        yield Static('Embedded Node Controller - Services', id='title')
        yield Static('Bootchain: loading...', id='bootchain-summary', markup=False)
        yield Static('Select a service to configure it, manage startup, or inspect its status and logs.', id='selection-info')
        yield DataTable(id='services', cursor_type='row')
        with Horizontal():
            yield Button('Configure / view', id='configure', variant='primary')
            yield Button('Refresh', id='refresh')
        yield Static('Loading service state...', id='result', markup=False)
        with Horizontal():
            yield Button('Finish / exit', id='finish')
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
        self.query_one('#configure', Button).disabled = self.busy or not bool(row.get('view'))

    def show_records(self, records, bootchain=None):
        if bootchain is not None: self.query_one('#bootchain-summary', Static).update(Text(display(bootchain)))
        previous = self.selected
        self.records = {row['name']: row for row in records}
        table = self.query_one('#services', DataTable)
        table.clear()
        for row in records:
            startup = 'ON' if row['enabled'] else 'OFF'
            table.add_row(Text(display(row['label'])), startup,
                          Text(display(row['state'])), 'Required' if row.get('mandatory') else 'Optional', key=row['name'])
        if records:
            index = next((i for i, row in enumerate(records) if row['name'] == previous), 0)
            table.move_cursor(row=index)
            self.selected = records[index]['name']
        self.set_busy(False)
        self.query_one('#result', Static).update('Saved startup choices. Change configuration or startup from the service page.')

    @work(thread=True, exclusive=True, group='service-read')
    def reload_records(self):
        self.call_from_thread(self.set_busy, True)
        try:
            if self.model is None: self.model = service_manager.ServiceModel()
            else: self.model.refresh()
            self.call_from_thread(self.show_records, self.model.records(), status_summary.bootchain_banner())
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
        elif button == 'finish':
            message = 'Confirm setup health and exit? SSH keys and Tailscale authorization must be ready.'
            for stage in registry.stages():
                if stage.get('setup_notice'): message += '\n' + stage['setup_notice']
            self.push_screen(Confirm(message), lambda yes: self.start_operation() if yes else None)

    def start_operation(self):
        if self.busy: return
        self.set_busy(True)
        self.apply_changes()

    @work(thread=True, exclusive=True, group='service-write')
    def apply_changes(self):
        self.call_from_thread(self.set_busy, True)
        try:
            service_manager.finish_setup()
            self.model.refresh()
            self.call_from_thread(self.show_records, self.model.records())
            self.call_from_thread(self.exit, True)
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
        if unsaved or self.first_boot:
            self.push_screen(Confirm('Discard unapplied changes and check health before exit?'), lambda yes: self.start_exit_check() if yes else None)
        else: self.start_exit_check()

    def start_exit_check(self):
        if self.busy: return
        self.set_busy(True)
        self.show_message('Checking public Internet and required services before exit...')
        self.check_exit()

    @work(thread=True, exclusive=True, group='exit-health')
    def check_exit(self):
        try:
            service_diagnostics.health()
            if not (service_manager.components.CONFIG / 'initialized').exists():
                raise ValueError('First-boot setup is incomplete. Use Finish / exit to confirm configuration.')
            self.call_from_thread(self.exit, True)
        except Exception as exc: self.call_from_thread(self.exit_failed, error_message(exc))

    def exit_failed(self, message):
        self.set_busy(False)
        self.show_message('Health check failed. Correct the problems before exiting.')
        self.push_screen(Details(message + '\n\nReturn to configuration to resolve the errors.', title='Exit health check failed'),
                         lambda _: self.push_screen(Confirm('Exit with an error status anyway?'), lambda yes: self.exit(False) if yes else None))


def configure():
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError('The service dashboard requires an interactive terminal; connect with `ssh -t`')
    return ServiceApp().run()
