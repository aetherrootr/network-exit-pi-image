"""SSH key/CA presentation. Validation and persistence belong to ssh_editor."""
from rich.text import Text
from textual import work
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, DataTable, Static
from enodectl_core.textual_ui import Confirm, Details, Field, FormScreen, confirm_discard, display, error_message
from ssh_editor import SSHEditor
import service_diagnostics as diagnostics


class SSHScreen(Screen):
    BINDINGS = [('escape', 'back', 'Back')]
    DEFAULT_CSS = '''
    SSHScreen { padding: 1 2; }
    SSHScreen #ssh-title { height: 2; text-style: bold; }
    SSHScreen #keys { height: 1fr; border: ascii $accent; }
    SSHScreen #ssh-result { height: auto; max-height: 4; }
    SSHScreen Horizontal { height: 3; }
    '''

    def __init__(self, model=None):
        super().__init__()
        self.model = model
        self.records = {}
        self.selected = None
        self.busy = False

    def compose(self):
        yield Static('SSH access - pi', id='ssh-title')
        yield Static('Public-key/certificate login only. Root SSH is disabled. Keep at least one login key or CA.')
        yield DataTable(id='keys', cursor_type='row')
        with Horizontal():
            yield Button('Add login key', id='ssh-add-key')
            yield Button('Add CA key', id='ssh-add-ca')
            yield Button('Details', id='ssh-details')
            yield Button('Remove', id='ssh-remove')
        yield Static('Loading authorizations...', id='ssh-result', markup=False)
        with Horizontal():
            yield Button('Save authorizations', id='ssh-save', variant='primary')
            yield Button('Check status', id='ssh-status')
            yield Button('View logs', id='ssh-logs')
            yield Button('Back', id='ssh-back')
        yield Static('Up/Down: key | Right: actions | Enter: details/select | Esc: back')

    def on_mount(self):
        self.query_one('#keys', DataTable).add_columns('Purpose', 'Type', 'SHA256 fingerprint', 'Comment')
        self.query_one('#keys', DataTable).focus()
        self.set_busy(True)
        self.load_rows()

    def set_busy(self, busy):
        self.busy = busy
        for button in self.query(Button): button.disabled = busy

    def rows(self, rows):
        self.records = {row['id']: row for row in rows}
        table = self.query_one('#keys', DataTable)
        table.clear()
        for row in rows:
            table.add_row('CA' if row['role'] == 'ca' else 'Login', Text(row['type']), Text(row['fingerprint']),
                          Text(display(row['comment'], multiline=False) + (' [options]' if row['restricted'] else '')), key=row['id'])
        self.selected = rows[0]['id'] if rows else None
        self.set_busy(False)
        self.query_one('#ssh-result', Static).update('Unsaved authorizations.' if self.model.changed() else 'Saved authorizations. Changes are staged until Save.')

    @work(thread=True, exclusive=True)
    def load_rows(self):
        self.app.call_from_thread(self.set_busy, True)
        try:
            if self.model is None: self.model = SSHEditor()
            self.app.call_from_thread(self.rows, self.model.records())
        except Exception as exc: self.app.call_from_thread(self.failed, exc)

    def failed(self, exc):
        self.set_busy(False)
        message = error_message(exc)
        self.query_one('#ssh-result', Static).update(Text(display(message)))
        self.app.push_screen(Details(message, title='SSH configuration error'))

    def on_data_table_row_highlighted(self, event):
        event.stop()
        self.selected = str(event.row_key.value)

    def show_selected(self):
        if self.busy or not self.selected: return
        row = self.records[self.selected]
        self.app.push_screen(Details('\n'.join(str(k) + ': ' + str(row[k]) for k in ('role', 'type', 'fingerprint', 'comment', 'restricted'))))

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        event.stop()
        self.selected = str(event.row_key.value)
        self.show_selected()

    def on_button_pressed(self, event: Button.Pressed):
        if self.busy: return
        id = event.button.id
        if id in ('ssh-add-key', 'ssh-add-ca'):
            role = 'login' if id == 'ssh-add-key' else 'ca'
            form = FormScreen('Add SSH ' + ('login key(s)' if role == 'login' else 'CA key(s)'),
                              [Field('source', 'HTTPS URL, device-local public-key file, or pasted public key')],
                              lambda: {'source': ''}, lambda values: self.model.add(role, values['source']),
                              help_text='Keys are staged only. Private keys, certificates and imported options are rejected.')
            self.app.push_screen(form, lambda result: self.load_rows())
        elif id == 'ssh-details': self.show_selected()
        elif id == 'ssh-status': self.app.push_screen(Details(title='SSH status', loader=lambda: diagnostics.status('ssh')))
        elif id == 'ssh-logs': self.app.push_screen(Details(title='SSH logs', loader=lambda: diagnostics.logs('ssh')))
        elif id == 'ssh-remove' and self.selected:
            selected = self.selected
            self.app.push_screen(Confirm('Stage removal of this authorization?\n' + self.records[selected]['fingerprint']),
                                 lambda yes: self.start_operation('remove', selected) if yes else None)
        elif id == 'ssh-save':
            self.app.push_screen(Confirm('Save SSH authorizations? Keep this connection open and verify a NEW login.'),
                                 lambda yes: self.start_operation('save') if yes else None)
        elif id == 'ssh-back': self.action_back()

    def start_operation(self, action, id=None):
        if self.busy: return
        self.set_busy(True)
        self.operate(action, id)

    @work(thread=True, exclusive=True)
    def operate(self, action, id=None):
        self.app.call_from_thread(self.set_busy, True)
        try:
            message = self.model.remove(id) if action == 'remove' else self.model.save()
            self.app.call_from_thread(self.rows, self.model.records())
            self.app.call_from_thread(self.show_message, message)
        except Exception as exc:
            self.app.call_from_thread(self.rows, self.model.records())
            self.app.call_from_thread(self.failed, exc)

    def show_message(self, message): self.query_one('#ssh-result', Static).update(Text(display(message)))

    def action_back(self):
        if self.busy: return
        if self.model and self.model.changed():
            confirm_discard(self, 'Discard unsaved SSH authorizations?')
        else: self.app.pop_screen()

    def has_unsaved(self): return bool(self.model and self.model.changed())
