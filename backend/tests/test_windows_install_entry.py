"""Exercise Windows installer dispatch without installing or changing services."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == 'nt', 'Windows PowerShell entry points')
class WindowsInstallEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='docbase entry ')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        source = Path(__file__).resolve().parents[2] / 'deploy' / 'windows'
        for name in ('install.ps1', 'install.cmd', 'common.ps1'):
            shutil.copy2(source / name, self.directory / name)
        (self.directory / 'bootstrap.ps1').write_text('''param(
            $InstallDir, $DataDir, $BackupDir, $Address,
            $PostgresPassword, [switch]$UseExistingFrontend)
            @{InstallDir=$InstallDir; DataDir=$DataDir; Address=$Address} |
                ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $PSScriptRoot 'called.json')
            exit 7
        ''', encoding='utf-8-sig')

    def run_install(self, *arguments):
        return subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy',
                               'Bypass', '-File', str(self.directory / 'install.ps1'),
                               *arguments], capture_output=True, timeout=30)

    def test_default_dispatch_and_failure_propagation(self):
        result = self.run_install()
        self.assertEqual(result.returncode, 7, result.stdout)
        actual = json.loads((self.directory / 'called.json').read_text(encoding='utf-8-sig'))
        self.assertEqual(actual['DataDir'], 'C:\\docbase-data')
        self.assertEqual(actual['Address'], 'localhost')

    def test_custom_paths_with_spaces(self):
        result = self.run_install('-DataDir', 'D:\\项目 files', '-Address', '192.168.1.50')
        self.assertEqual(result.returncode, 7, result.stdout)
        actual = json.loads((self.directory / 'called.json').read_text(encoding='utf-8-sig'))
        self.assertEqual(actual['DataDir'], 'D:\\项目 files')
        self.assertEqual(actual['Address'], '192.168.1.50')

    def test_offline_and_internal_entry_require_complete_package(self):
        for flag in ('-Offline', '-EnvironmentReady', '-NoService'):
            with self.subTest(flag=flag):
                self.assertEqual(self.run_install(flag).returncode, 1)
                self.assertFalse((self.directory / 'called.json').exists())

    def test_double_click_launcher_preserves_exit_code(self):
        result = subprocess.run(['cmd.exe', '/d', '/c', 'install.cmd'],
                                cwd=self.directory, input=b'\r\n',
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 7, result.stdout)
        self.assertTrue((self.directory / 'called.json').exists())


if __name__ == '__main__':
    unittest.main()
