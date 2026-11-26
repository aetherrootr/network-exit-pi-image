"""Textual form presentation; backend operations are kept in singbox_config."""
from enodectl_core.textual_ui import Field, FormScreen
import singbox_config as backend
import service_diagnostics as diagnostics

def screen():
    return FormScreen('sing-box', [
        Field('config', 'sing-box JSON (blank creates an authenticated localhost proxy)', 'multiline'),
    ], backend.load, backend.save, lambda: diagnostics.status('singbox'), logs=lambda: diagnostics.logs('singbox'), service_name='singbox', help_text='JSON may contain credentials visible in this terminal. Existing credentials are retained unless edited.')
