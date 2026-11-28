"""Textual OTA actions; all installation and recovery logic lives in backends."""
from enodectl_core.textual_ui import Field, FormScreen, display
from textual.widgets import Button, Input, Select, Static
import ota_status
import ota_update_config as backend
import service_diagnostics as diagnostics


class OTAInstallScreen(FormScreen):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        saver = self.saver
        self.saver = lambda values: saver(values, report=self.report_progress)

    def report_progress(self, message):
        self.app.call_from_thread(self.show_progress, message)

    def show_progress(self, message):
        self.query_one('#form-result', Static).update(display(message))

    def set_busy(self, value):
        super().set_busy(value)
        for widget in self.query('Input, Select'): widget.disabled = value

    def start_apply(self, values):
        self.query_one('#form-result', Static).update('Installing OTA to the inactive slot. Keep power connected; this may take several minutes.')
        super().start_apply(values)

    def on_mount(self):
        super().on_mount()
        self.query_one('#form-save', Button).label = 'Install OTA'

    def applied(self, result):
        # Never carry an unsafe choice or its authorization into another install.
        self.fill(backend.load())
        super().applied(result)

    def failed(self, exc):
        self.query_one('#field-skip_signature_verification', Select).value = False
        self.query_one('#field-confirmation', Input).value = ''
        super().failed(exc)


def screen():
    return OTAInstallScreen('OTA installation', [
        Field('bundle', 'Local OTA bundle path (blank uses latest signed release)'),
        Field('skip_signature_verification', 'Skip signature verification for this install', 'bool'),
        Field('confirmation', f'Recovery only: type exactly `{backend.CONFIRMATION}` (without backticks)'),
        Field('retry_failed', 'Retry after a previously failed trial', 'bool'),
    ], backend.load, backend.save, ota_status.details, logs=lambda: diagnostics.logs('ota'),
        help_text='Signature verification defaults to ON. Bypass requires a local plain rootfs-only bundle and the exact phrase. It does not authenticate the image. Device/size/checksum/readback and A/B trial checks remain active. This screen never reboots automatically.')
