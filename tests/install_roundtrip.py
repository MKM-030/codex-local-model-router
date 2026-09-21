"""Isolated Windows installer/reinstaller/updater/uninstaller test; no model needed."""

import json, subprocess, tempfile, threading, tomllib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Status(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"data":[{"id":"Qwen3.8-Flash-Next"}]}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Status)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix="router-installer-test-") as d:
            home = Path(d)
            original = 'model="gpt-test"\nmodel_provider="openai"\n[features]\nplugins=true\nstandalone_web_search=false\n[mcp_servers.untouched]\ncommand="python"\n'
            (home / "config.toml").write_text(original, encoding="utf-8")

            def run(script, *args):
                r = subprocess.run(
                    [
                        "powershell.exe",
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(script),
                        "-CodexHome",
                        str(home),
                        *args,
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                )
                if r.returncode:
                    raise RuntimeError(r.stdout[-2000:] + "\n" + r.stderr[-2000:])

            install = ROOT / "Install-CodexLocalModel.ps1"
            params = [
                "-LocalBaseUrl",
                f"http://127.0.0.1:{server.server_port}/v1",
                "-RouterPort",
                "8848",
                "-SkipAutostart",
            ]
            run(install, *params)
            state = (home / "local-model-router/install-state.json").read_bytes()
            run(install, *params)
            assert (
                home / "local-model-router/install-state.json"
            ).read_bytes() == state, "Reinstall overwrote original rollback state"
            config = tomllib.loads(
                (home / "config.toml").read_text(encoding="utf-8-sig")
            )
            assert config["features"]["standalone_web_search"] is True
            # BOM-less UTF-8 must survive repeated PS 5.1 updates, including paths.
            installed = home / "local-model-router"
            catalog_path = installed / "catalog-\u00fc-\u6a21\u578b.json"
            catalog = json.loads((installed / "local-model-catalog.json").read_text(encoding="utf-8-sig"))
            unicode_label = "Qwen \u2014 Lokal \u00e4\u00f6\u00fc \u6a21\u578b \U0001f9e0"
            catalog["models"][0]["display_name"] = unicode_label
            catalog["models"][0]["description"] = "F\u00fcr lokale Modelle \u2013 unver\u00e4ndert"
            catalog_path.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
            settings_path = installed / "router-config.json"
            settings = json.loads(settings_path.read_text(encoding="utf-8-sig"))
            settings["models"][0]["catalogPath"] = catalog_path.name
            settings_path.write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
            for _ in range(2):
                run(ROOT / "Update-CodexToolBridge.ps1", "-NoStart")
                self_catalog = json.loads(catalog_path.read_text(encoding="utf-8-sig"))
                assert self_catalog["models"][0]["display_name"] == unicode_label, "Unicode model label corrupted"
                assert self_catalog["models"][0]["description"] == catalog["models"][0]["description"]
                assert json.loads(settings_path.read_text(encoding="utf-8-sig"))["models"][0]["catalogPath"] == catalog_path.name
            run(ROOT / "Update-CodexToolBridge.ps1", "-NoStart", "-DisplayName", "Qwen3.8 Flash Next - Local")
            repaired = json.loads(catalog_path.read_text(encoding="utf-8-sig"))
            assert repaired["models"][0]["display_name"] == "Qwen3.8 Flash Next - Local"
            repaired["models"][0]["display_name"] = unicode_label
            assert repaired == self_catalog, "Display repair changed unrelated metadata"
            for rel in [
                "tool_bridge.py",
                "hybrid-model-router.py",
                "scripts/configure_tool_bridge.py",
            ]:
                assert (home / "local-model-router" / rel).exists(), rel
            run(home / "local-model-router/Uninstall-CodexLocalModel.ps1")
            final = tomllib.loads(
                (home / "config.toml").read_text(encoding="utf-8-sig")
            )
            assert final == tomllib.loads(original), (final, tomllib.loads(original))
            print(
                "PASS: INSTALL, REINSTALL, UTF8 UPDATES, DISPLAY REPAIR, UNINSTALL; ORIGINAL CONFIG RESTORED"
            )
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
