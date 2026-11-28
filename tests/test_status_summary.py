"""Status output has compact local tables and no package dumps or remote probes."""
from _stage_support import ROOT
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
import status_summary


INFO={'version':'v0.0.2','layout':'ab-v2','state':'trial','current_slot':'B',
      'confirmed_slot':'A','pending_slot':'B','remaining_boot_attempts':2,
      'slots':{'A':{'valid':True},'B':{'valid':True}},
      'trial_deadline_active':True,'trial_seconds_remaining':120}


class StatusSummaryTests(unittest.TestCase):
    def test_bootchain_slot_table_and_banner_include_trial(self):
        with patch.object(status_summary,'bootchain',return_value=(INFO,None)), \
                patch.object(status_summary,'service_rows',return_value=([('SSH','ON','active','Required')],False)), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(status_summary.show(),0)
            self.assertIn('Slot B',status_summary.bootchain_banner())
            self.assertIn('Pending B',status_summary.bootchain_banner())
        text=output.getvalue()
        for value in ('Bootchain','Current slot','Confirmed slot','Pending slot','Trial time left','120 s','Key services','SSH','active'):
            self.assertIn(value,text)

    def test_service_rows_keep_disabled_and_on_demand_and_tailscale_login(self):
        records=[{'name':'tailscale','label':'Tailscale','units':['tailscaled.service'],'enabled':True,'state':'active','mandatory':True},
                 {'name':'cloudflared','label':'Cloudflare Tunnel','units':['cloudflared.service'],'enabled':False,'state':'inactive','mandatory':False},
                 {'name':'ota','label':'A/B update services','units':['rauc.service'],'enabled':True,'state':'on-demand','mandatory':True},
                 {'name':'system-power','label':'System power policy','units':[],'enabled':True,'state':'built-in','mandatory':True}]
        with patch('service_manager.ServiceModel.records',return_value=records), \
                patch('tailscale_control.backend_status',return_value={'BackendState':'NeedsLogin'}):
            rows,failed=status_summary.service_rows()
        self.assertEqual(rows,[('Tailscale','ON','active / NeedsLogin','Required'),('Cloudflare Tunnel','OFF','inactive','Optional'),('A/B update services','ON','on-demand','Required')])
        self.assertFalse(failed)

    def test_missing_bootchain_still_shows_service_status(self):
        with patch.object(status_summary,'bootchain',return_value=(None,'No boot metadata')), \
                patch.object(status_summary,'service_rows',return_value=([('SSH','ON','active','Required')],False)), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(status_summary.show(),1)
        self.assertIn('No boot metadata',output.getvalue());self.assertIn('SSH',output.getvalue())
