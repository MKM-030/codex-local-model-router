"""Regression: exercise the actual PowerShell JSON reads with Unicode metadata."""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    "Update-CodexToolBridge.ps1",
    "Uninstall-CodexLocalModel.ps1",
    "scripts/Test-CodexLocalModel.ps1",
)


@unittest.skipUnless(shutil.which("powershell.exe"), "Windows PowerShell required")
class CatalogEncodingTests(unittest.TestCase):
    def assert_roundtrip(self, with_bom):
        payload = {
            "display_name": "Qwen3.8 Flash Next \u2014 Local",
            "description": "F\u00fcr lokale Modelle: \u00e4\u00f6\u00fc \u00df \u6a21\u578b \U0001f9e0",
            "path": "C:/Models/M\u00fcnchen/catalog-\u6a21\u578b.json",
        }
        matched = 0
        with tempfile.TemporaryDirectory(prefix="codex-encoding-test-") as d:
            source = Path(d) / "input.json"
            output = Path(d) / "output.json"
            source.write_text(json.dumps(payload, ensure_ascii=False),
                              encoding="utf-8-sig" if with_bom else "utf-8")
            for relative in SCRIPTS:
                lines = (ROOT / relative).read_text(encoding="utf-8-sig").splitlines()
                for line in lines:
                    match = re.match(r"^\s*\$(\w+) = (Get-Content -LiteralPath \$(\w+).*\| ConvertFrom-Json)$", line)
                    if not match:
                        continue
                    matched += 1
                    result_var, expression, path_var = match.groups()
                    with self.subTest(script=relative, variable=path_var, bom=with_bom):
                        command = "$ErrorActionPreference='Stop';"
                        command += "$" + path_var + "='" + str(source).replace("'", "''") + "';"
                        command += "$" + result_var + "=" + expression + ";"
                        command += "[IO.File]::WriteAllText('" + str(output).replace("'", "''") + "',($" + result_var + "|ConvertTo-Json -Depth 20),[Text.UTF8Encoding]::new($false))"
                        run = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, timeout=30)
                        self.assertEqual(run.returncode, 0, run.stderr.decode(errors="replace"))
                        self.assertEqual(json.loads(output.read_text(encoding="utf-8")), payload)
            self.assertEqual(matched, 5, "Update this test when JSON-read sites change")

    def test_utf8_without_bom(self):
        self.assert_roundtrip(False)

    def test_utf8_with_bom(self):
        self.assert_roundtrip(True)


if __name__ == "__main__":
    unittest.main()
