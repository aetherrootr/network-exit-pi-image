from _stage_support import ROOT
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
import components
import service_diagnostics as diagnostics


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.config = Path(temp.name)
        scope = patch.object(components, 'CONFIG', self.config); scope.start(); self.addCleanup(scope.stop)

    def test_health_checks_https_required_and_selected_units_not_on_demand_rauc(self):
        (self.config/'components.json').write_text(json.dumps({'cloudflared':True}))
        def query(args, **kwargs):
            if args[0]=='curl':
                self.assertIn('https://www.cloudflare.com/cdn-cgi/trace',args)
                return 0,''
            self.assertIn('network-exit-cloudflared.service',args)
            self.assertIn('tailscaled.service',args)
            self.assertNotIn('rauc.service',args)
            return 0,'\n'.join('active' for _ in args[2:])
        with patch.object(diagnostics,'query',side_effect=query), patch.object(diagnostics.registry,'hooks',return_value=[lambda:None]):
            self.assertIn('passed',diagnostics.health())

    def test_failed_internet_and_tailscale_report_both(self):
        def fail(): raise ValueError('Tailscale not connected')
        def query(args, **kwargs): return (1,'') if args[0]=='curl' else (0,'\n'.join('active' for _ in args[2:]))
        with patch.object(diagnostics,'query',side_effect=query), patch.object(diagnostics.registry,'hooks',return_value=[fail]):
            with self.assertRaises(ValueError) as error: diagnostics.health()
        self.assertIn('Public Internet',str(error.exception)); self.assertIn('Tailscale not connected',str(error.exception))

    def test_logs_include_ddns_worker_and_redact_known_credentials(self):
        (self.config/'cloudflare-token').write_text('fixture-tunnel-token')
        (self.config/'ddns.json').write_text(json.dumps({'secret':'fixture-ddns-secret'}))
        with patch.object(diagnostics,'query',return_value=(0,'connection error fixture-tunnel-token fixture-ddns-secret')) as query:
            logs=diagnostics.logs('ddns')
        self.assertIn('network-exit-ddns.service',query.call_args.args[0]); self.assertIn('network-exit-ddns.timer',query.call_args.args[0])
        self.assertNotIn('fixture-tunnel-token',logs);self.assertNotIn('fixture-ddns-secret',logs)
        self.assertIn('connection error',logs)

    def test_disabled_service_status_explains_save_vs_enable(self):
        with patch.object(diagnostics,'query',return_value=(0,'ActiveState=inactive')):
            status=diagnostics.status('cloudflared')
        self.assertIn('disabled',status);self.assertIn('Startup ON',status)
