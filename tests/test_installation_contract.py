"""Source guards plus optional real PowerShell helper tests (no live installation)."""
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class InstallationContractTests(unittest.TestCase):
    def setUp(self):
        self.installer = (ROOT / 'Install-CodexLocalModel.ps1').read_text(encoding='utf-8-sig')
        self.readme = (ROOT / 'README.md').read_text(encoding='utf-8')

    def test_backend_probe_is_opt_in(self):
        self.assertIn('[switch]$CheckLocalServer', self.installer)
        self.assertIn('if ($CheckLocalServer -or $RunInferenceProbe) {\n'
                      '    $localEndpointAvailable = Test-OptionalLocalEndpoint $LocalBaseUrl $ModelId\n}',
                      self.installer)
        self.assertNotIn('Local endpoint is not reachable at $LocalBaseUrl. Start it first.', self.installer)
        calls = re.findall(r'^\s*[^#\n]*Invoke-RestMethod[^\n]*$', self.installer, re.M)
        self.assertEqual(len(calls), 3)  # optional /models, router /health, optional inference
        self.assertFalse(any('/ready' in line for line in calls))

    def test_diagnostics_do_not_claim_server_started(self):
        self.assertIn('no running model server required for installation', self.installer)
        self.assertIn('No reinstall is needed when that server starts later.', self.installer)
        self.assertIn("elseif ($SkipAutostart)", self.installer)
        self.assertNotIn('Get-Process llama-server', self.installer)
        self.assertNotIn('llama-server.exe', self.installer)

    def test_configuration_validation_remains(self):
        self.assertIn('Assert-LocalEndpointSettings $LocalBaseUrl $RouterPort', self.installer)
        self.assertLess(self.installer.index('Assert-LocalEndpointSettings $LocalBaseUrl $RouterPort'),
                        self.installer.index('New-Item -ItemType Directory'))
        self.assertIn("RouterPort and the local model server port must be different.", self.installer)

    def test_readme_is_not_a_changelog(self):
        self.assertIn('## Fresh installation', self.readme)
        self.assertIn('does not have to be running during installation', self.readme)
        self.assertNotRegex(self.readme, r'(?im)^#{1,6}.*(?:version|changelog|history|routing fix)')
        self.assertNotRegex(self.readme, r'\bv0\.[0-9]+\.[0-9]+\b')

    def test_readme_distinguishes_install_from_readiness(self):
        self.assertIn('**200**', self.readme)
        self.assertIn('**503**', self.readme)
        self.assertIn('no need to reinstall', self.readme)
        self.assertIn('requires internet access', self.readme)

    @unittest.skipUnless(shutil.which('powershell.exe') or shutil.which('pwsh'),
                         'PowerShell is unavailable; Windows helper execution not validated here')
    def test_real_powershell_helpers(self):
        executable = shutil.which('powershell.exe') or shutil.which('pwsh')
        command = [executable, '-NoProfile']
        if os.name == 'nt':
            command += ['-ExecutionPolicy', 'Bypass']
        command += ['-File', str(ROOT / 'tests/Test-InstallerPreflight.ps1')]
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + '\n' + result.stderr)
        self.assertIn('PASS:', result.stdout)


if __name__ == '__main__':
    unittest.main()
