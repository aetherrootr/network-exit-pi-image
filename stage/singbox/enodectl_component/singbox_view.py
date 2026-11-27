"""Textual form presentation; backend operations are kept in singbox_config."""
from enodectl_core.textual_ui import Field, FormScreen
import singbox_config as backend
import service_diagnostics as diagnostics

def screen():
    return FormScreen('sing-box', [
        Field('config', 'sing-box JSON (blank keeps saved settings or creates a new localhost proxy)', 'multiline'),
    ], backend.load, backend.save, lambda: diagnostics.status('singbox'), logs=lambda: diagnostics.logs('singbox'), service_name='singbox', reload_after_apply=True, help_text='JSON may contain credentials visible in this terminal. Existing credentials are retained unless edited.')
