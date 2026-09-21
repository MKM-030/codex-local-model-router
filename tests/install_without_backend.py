"""Windows install without a model, then readiness recovery without reinstall.

Use only in a full source checkout. Uses an isolated CODEX_HOME, no startup entry,
no model weights, dynamic loopback ports, and terminates only its own router child.
Requires the documented Python dependencies and an installed Windows PowerShell.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
MODEL = 'Qwen3.8-Flash-Next'
OPENER = build_opener(ProxyHandler({}))


def get_json(url: str) -> tuple[int, dict]:
    try:
        response = OPENER.open(url, timeout=8)
    except HTTPError as exc:
        response = exc
    with response:
        return response.code, json.load(response)


def install(home: Path, base_url: str, port: int, *options: str,
            expected_success: bool = True) -> str:
    env = os.environ.copy()
    env['CODEX_HOME'] = str(home)
    command = [shutil.which('powershell.exe'), '-NoProfile', '-ExecutionPolicy',
               'Bypass', '-File', str(ROOT / 'Install-CodexLocalModel.ps1'),
               '-CodexHome', str(home), '-LocalBaseUrl', base_url,
               '-RouterPort', str(port), '-SkipAutostart', *options]
    result = subprocess.run(command, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=120, env=env)
    output = result.stdout + '\n' + result.stderr
    if expected_success != (result.returncode == 0):
        raise AssertionError(output[-5000:])
    return output


def main() -> None:
    if os.name != 'nt' or not shutil.which('powershell.exe'):
        raise SystemExit('Requires Windows PowerShell; this integration test has NOT run.')
    if not (ROOT / 'router/hybrid-model-router.py').exists():
        raise SystemExit('Run this test from a complete source checkout.')
    import httpx  # noqa: F401  # Avoid installing dependencies during this test.
    import zstandard  # noqa: F401

    class LaterBackend(BaseHTTPRequestHandler):
        calls = 0

        def log_message(self, *_):
            pass

        def do_GET(self):
            type(self).calls += 1
            data = json.dumps({'data': [{'id': MODEL}]}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    # Bind to reserve an isolated port, but do NOT listen during installation.
    backend = HTTPServer(('127.0.0.1', 0), LaterBackend, bind_and_activate=False)
    backend.server_bind()
    backend_port = backend.server_address[1]
    base_url = f'http://127.0.0.1:{backend_port}/v1'
    router_child = None
    backend_started = False
    try:
        with tempfile.TemporaryDirectory(prefix='codex-no-model-install-') as directory:
            home = Path(directory) / 'home'
            home.mkdir()
            initial = 'model="gpt-test"\nmodel_provider="openai"\n[features]\nplugins=true\n'
            config_path = home / 'config.toml'
            config_path.write_text(initial, encoding='utf-8')
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                router_port = reservation.getsockname()[1]
                # Default installation never probes the model endpoint.
                output = install(home, base_url, router_port)
                assert 'Installed Codex local-model routing successfully.' in output
                assert 'no running model server required' in output
                settings = tomllib.loads(config_path.read_text(encoding='utf-8-sig'))
                assert settings['model_provider'] == 'hybrid_router'
                assert settings['features']['plugins'] is True
                installed = home / 'local-model-router'
                for relative in ['hybrid-model-router.py', 'tool_bridge.py',
                                 'router-config.json', 'local-model-catalog.json',
                                 'install-state.json', 'Uninstall-CodexLocalModel.ps1']:
                    assert (installed / relative).is_file(), relative
                state = (installed / 'install-state.json').read_bytes()
                # Even explicitly requested checks cannot turn an absent model
                # into an installation failure. Inference must be skipped.
                output = install(home, base_url, router_port,
                                 '-CheckLocalServer', '-RunInferenceProbe')
                assert 'Inference probe skipped' in output
                assert (installed / 'install-state.json').read_bytes() == state
                assert LaterBackend.calls == 0
                before_invalid = config_path.read_bytes()
                install(home, 'not-a-url', router_port, expected_success=False)
                assert config_path.read_bytes() == before_invalid

            # Start only this test router, without a global startup entry.
            log = (Path(directory) / 'router.log').open('w', encoding='utf-8')
            env = os.environ.copy()
            env['CODEX_LOCAL_ROUTER_CONFIG'] = str(installed / 'router-config.json')
            router_child = subprocess.Popen(
                [sys.executable, str(installed / 'hybrid-model-router.py')],
                stdout=log, stderr=subprocess.STDOUT, env=env)
            try:
                router_url = f'http://127.0.0.1:{router_port}'
                deadline = time.monotonic() + 15
                while True:
                    try:
                        status, health = get_json(router_url + '/health')
                        assert status == 200
                        assert health['pid'] == router_child.pid
                        break
                    except OSError:
                        if router_child.poll() is not None or time.monotonic() > deadline:
                            raise AssertionError('Test router did not start')
                        time.sleep(0.25)
                status, readiness = get_json(router_url + '/ready')
                assert status == 503 and not readiness['ready']
                config_before = config_path.read_bytes()
                backend.server_activate()
                threading.Thread(target=backend.serve_forever, daemon=True).start()
                backend_started = True
                status, readiness = get_json(router_url + '/ready')
                assert status == 200 and readiness['ready']
                assert config_path.read_bytes() == config_before
                print('PASS: INSTALL_WITHOUT_MODEL, NONFATAL_OPTIONAL_CHECKS, '
                      'INVALID_CONFIG_REJECTED, HEALTH_200_READY_503, '
                      'BACKEND_READY_LATER_WITHOUT_REINSTALL')
            finally:
                if router_child.poll() is None:
                    router_child.terminate()
                router_child.wait(timeout=10)
                router_child = None
                log.close()
    finally:
        if router_child is not None and router_child.poll() is None:
            router_child.terminate()
            router_child.wait(timeout=10)
        if backend_started:
            backend.shutdown()
        backend.server_close()


if __name__ == '__main__':
    main()
