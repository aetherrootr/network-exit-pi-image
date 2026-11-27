"""Textual form presentation; backend operations are kept in tailscale_config."""
from enodectl_core.textual_ui import Confirm, Field, FormScreen
from textual.widgets import Static
import tailscale_config as backend
import service_diagnostics as diagnostics


class TailscaleScreen(FormScreen):
    """Ask for explicit reauthentication only when the live identity must switch."""
    def start_apply(self, values):
        if not self.busy: self.pending_values = dict(values)
        super().start_apply(values)

    def failed(self, exc):
        if not isinstance(exc, backend.ReauthenticationRequired):
            return super().failed(exc)
        self.set_busy(False)
        values = dict(self.pending_values)
        self.query_one('#form-result', Static).update('Control-plane switch requires separate confirmation.')
        message = ('Switch the existing Tailscale identity to a different control plane?\n'
                   f'Current: {exc.current}\nNew: {exc.target}\n'
                   'This may disconnect the current Tailscale session. Use a LAN or console connection.')
        def confirmed(yes):
            if yes: self.start_apply({**values, 'reauth_confirm': True})
            else: self.query_one('#form-result', Static).update('Control-plane switch cancelled; no settings changed.')
        self.app.push_screen(Confirm(message), confirmed)


def screen():
    return TailscaleScreen('Tailscale / Headscale', [
        Field('provider', 'Control plane', 'choice', (('Official Tailscale', 'official'), ('Headscale', 'headscale'))),
        Field('headscale_url', 'Headscale HTTPS origin (when selected)'),
        Field('advertise_exit', 'Advertise as an exit node', 'bool'),
    ], backend.load, backend.save, backend.details, result_dialog=True, logs=lambda: diagnostics.logs('tailscale'),
        help_text='Tailscale is mandatory. Apply configuration to request login. Check status for the URL or connection errors. Switching control plane requires confirmation.')
