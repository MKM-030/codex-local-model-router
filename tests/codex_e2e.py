"""Real Codex executor + deterministic mock model. Not a Qwen quality test."""

from __future__ import annotations
import argparse, importlib.util, json, os, socket, subprocess, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "router"))
from tool_bridge import dumps

spec = importlib.util.spec_from_file_location(
    "router", ROOT / "router/hybrid-model-router.py"
)
router = importlib.util.module_from_spec(spec)
spec.loader.exec_module(router)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex", required=True)
    parser.add_argument("--codex-home", help="Use existing Codex sandbox setup via a temporary profile; never replace its config")
    parser.add_argument(
        "--protocol-only",
        action="store_true",
        help="Test dispatch, custom-tool validation, shell and MCP without filesystem writes",
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(
        prefix="codex-bridge-e2e-", dir=str(ROOT)
    ) as directory:
        root = Path(directory)
        home = root / "home"
        work = root / "work"
        home.mkdir()
        work.mkdir()
        skill = work / ".agents/skills/bridge-check/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            "---\nname: bridge-check\ndescription: Read the validation marker when testing the tool compatibility bridge.\n---\nSKILL_CONTENT_MARKER_8A32\n",
            encoding="utf-8",
        )
        catalog = json.loads(
            (ROOT / "examples/qwen3.8-flash-next.catalog.json").read_text(
                encoding="utf-8-sig"
            )
        )
        m = catalog["models"][0]
        m["slug"] = "bridge-test-local"
        m["display_name"] = "Bridge test local"
        m["default_reasoning_level"] = "low"
        for key in (
            "include_skills_usage_instructions",
            "include_plugin_usage_instructions",
            "include_apps_usage_instructions",
        ):
            m[key] = True
        (home / "catalog.json").write_text(dumps(catalog), encoding="utf-8")
        requests = []
        failures = []
        mockport = free_port()
        bridgeport = free_port()

        class Mock(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                body = dumps({"data": [{"id": "bridge-test-local"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                p = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(p)
                i = len(requests) - 1
                try:
                    tools = p.get("tools", [])
                    assert all(
                        t["type"] == "function" for t in tools
                    ), "Non-function tool reached mock model"
                    if i == 0:
                        name = next(
                            t["name"] for t in tools if "apply_patch" in t["name"]
                        )
                        arguments = {
                            "input": "*** Begin Patch\n*** Add File: bridge-result.txt\n+PATCH_OK\n*** End Patch"
                        }
                    elif i == 1:
                        name = next(
                            t["name"] for t in tools if t["name"] == "exec_command"
                        )
                        arguments = {
                            "cmd": "Get-Content -LiteralPath '" + str(skill) + "'; Get-Content -LiteralPath '" + str(work / "bridge-result.txt") + "'; Remove-Item -LiteralPath '" + str(work / "bridge-result.txt") + "'",
                            "max_output_tokens": 500,
                        }
                    elif i == 2:
                        name = next(
                            t["name"]
                            for t in tools
                            if "bridge_fixture" in t["name"] and "echo" in t["name"]
                        )
                        arguments = {"value": "NAMESPACE_OK"}
                    else:
                        name = None
                        arguments = None
                    if args.protocol_only and i == 0:
                        arguments = {"input": "*** Begin Patch\n*** End Patch"}
                    if args.protocol_only and i == 1:
                        arguments = {
                            "cmd": "Write-Output BRIDGE_SHELL_OK",
                            "max_output_tokens": 100,
                        }
                    response = {
                        "id": "resp" + str(i),
                        "object": "response",
                        "created_at": int(time.time()),
                        "model": "bridge-test-local",
                        "status": "in_progress",
                        "output": [],
                    }
                    if name:
                        item = {
                            "id": "item" + str(i),
                            "type": "function_call",
                            "call_id": "call" + str(i),
                            "name": name,
                            "arguments": dumps(arguments),
                            "status": "completed",
                        }
                    else:
                        item = {
                            "id": "msg" + str(i),
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "BRIDGE_CODEX_E2E_OK",
                                    "annotations": [],
                                }
                            ],
                        }
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    events = [{"type": "response.created", "response": dict(response)}]
                    if name:
                        events += [
                            {
                                "type": "response.output_item.added",
                                "output_index": 0,
                                "item": dict(item, arguments="", status="in_progress"),
                            },
                            {
                                "type": "response.function_call_arguments.delta",
                                "item_id": item["id"],
                                "output_index": 0,
                                "delta": item["arguments"],
                            },
                            {
                                "type": "response.function_call_arguments.done",
                                "item_id": item["id"],
                                "output_index": 0,
                                "arguments": item["arguments"],
                            },
                        ]
                    else:
                        events += [
                            {
                                "type": "response.output_item.added",
                                "output_index": 0,
                                "item": dict(item, content=[], status="in_progress"),
                            },
                            {
                                "type": "response.output_text.delta",
                                "output_index": 0,
                                "content_index": 0,
                                "item_id": item["id"],
                                "delta": "BRIDGE_CODEX_E2E_OK",
                            },
                        ]
                    events += [
                        {
                            "type": "response.output_item.done",
                            "output_index": 0,
                            "item": item,
                        },
                        {
                            "type": "response.completed",
                            "response": dict(
                                response,
                                status="completed",
                                output=[item],
                                usage={
                                    "input_tokens": 100,
                                    "output_tokens": 20,
                                    "total_tokens": 120,
                                },
                            ),
                        },
                    ]
                    for seq, e in enumerate(events):
                        e["sequence_number"] = seq
                        self.wfile.write(
                            (
                                "event: " + e["type"] + "\ndata: " + dumps(e) + "\n\n"
                            ).encode()
                        )
                        self.wfile.flush()
                    self.close_connection = True
                except Exception as exc:
                    failures.append(type(exc).__name__ + ": " + str(exc))
                    body = dumps({"error": failures[-1]}).encode()
                    self.send_response(500)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

        mock = ThreadingHTTPServer(("127.0.0.1", mockport), Mock)
        threading.Thread(target=mock.serve_forever, daemon=True).start()
        c = {
            "host": "127.0.0.1",
            "port": bridgeport,
            "logMetadata": False,
            "models": [
                {
                    "id": "bridge-test-local",
                    "baseUrl": f"http://127.0.0.1:{mockport}/v1",
                    "catalogPath": "catalog.json",
                }
            ],
        }
        bridge = router.make_server(router.Router(c, home))
        threading.Thread(target=bridge.serve_forever, daemon=True).start()
        config = "\n".join(
            [
                'model="bridge-test-local"',
                'model_provider="bridge_test"',
                'model_reasoning_effort="low"',
                'web_search="disabled"',
                'sandbox_mode="workspace-write"',
                "model_catalog_json=" + json.dumps(str(home / "catalog.json")),
                "[features]",
                "plugins=false",
                "apps=false",
                "[model_providers.bridge_test]",
                'name="Bridge validation"',
                f'base_url="http://127.0.0.1:{bridgeport}/v1"',
                'wire_api="responses"',
                "requires_openai_auth=false",
                "supports_websockets=false",
                "request_max_retries=0",
                "stream_max_retries=0",
                "[mcp_servers.bridge_fixture]",
                "command=" + json.dumps(sys.executable),
                "args=" + json.dumps([str(ROOT / "tests/fixtures/mcp_echo.py")]),
            ]
        )
        config += (
            '\n[windows]\nsandbox="unelevated"\n[projects.'
            + json.dumps(str(work))
            + ']\ntrust_level="trusted"\n'
        )
        (home / "config.toml").write_text(config, encoding="utf-8")
        # Python's Windows TemporaryDirectory is private by default. Give the
        # existing Codex sandbox group access only to this disposable fixture.
        # No production directory or permission configuration is changed.
        if os.name == "nt":
            principal = "CodexSandboxUsers:(OI)(CI)M"
            acl = subprocess.run(
                ["icacls", str(root), "/grant", principal, "/T", "/C"],
                capture_output=True,
                text=True,
            )
            if acl.returncode != 0:
                raise RuntimeError(
                    "Isolated fixture ACL setup failed: " + acl.stderr[-500:]
                )
        env = os.environ.copy()
        env["CODEX_HOME"] = str(home)
        profile_file = None
        profile_args = []
        if args.codex_home:
            import uuid
            profile_name = "bridge-validation-" + uuid.uuid4().hex[:12]
            profile_file = Path(args.codex_home) / (profile_name + ".config.toml")
            profile_config = config.replace('[windows]\nsandbox="unelevated"\n', "")
            profile_file.write_text(profile_config, encoding="utf-8")
            env["CODEX_HOME"] = str(Path(args.codex_home).resolve())
            profile_args = ["--profile", profile_name]
        try:
            result = subprocess.run(
                [
                    args.codex,
                    *profile_args,
                    "exec",
                    "--skip-git-repo-check",
                    "--ephemeral",
                    "--color",
                    "never",
                    "-s",
                    "workspace-write",
                    "-C",
                    str(work),
                    "Validate the bridge using apply_patch to create bridge-result.txt, read the bridge-check skill, and call the bridge_fixture echo MCP tool.",
                ],
                input="",
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=100,
            )
            print(result.stdout[-10000:])
            print(result.stderr[-3000:])
            print("REQUESTS", len(requests), "MOCK_FAILURES", failures)
            assert result.returncode == 0, "Codex process failed"
            shell_outputs = [i.get("output") for i in requests[-1]["input"] if isinstance(i, dict) and i.get("type") == "function_call_output" and i.get("call_id") == "call1"]
            patch_ok = "PATCH_OK" in dumps(shell_outputs)
            if not args.protocol_only:
                assert (
                    patch_ok
                ), "Custom patch dispatch succeeded but the OS sandbox did not permit the write"
            assert len(requests) >= 4, "Multi-step execution did not complete"
            history = dumps(requests[-1]["input"])
            skill_ok = "SKILL_CONTENT_MARKER_8A32" in history
            if not args.protocol_only:
                assert skill_ok, "Skill file read did not succeed under the OS sandbox"
            assert (
                "MCP_EXECUTED:NAMESPACE_OK" in history
            ), "Namespaced MCP echo was not executed"
            assert any(
                i.get("type") == "function_call_output" and i.get("call_id") == "call0"
                for i in requests[1]["input"]
            ), "Custom tool output did not round trip"
            assert not failures, failures
            print(
                "FILESYSTEM_VALIDATION",
                json.dumps(
                    {
                        "patchExecuted": patch_ok,
                        "skillRead": skill_ok,
                        "protocolOnly": args.protocol_only,
                    }
                ),
            )
            print(
                "PASS: CODEX_TOOL_DISPATCH, NAMESPACED_MCP_EXECUTION, MULTI_TURN_HISTORY"
            )
        finally:
            if profile_file is not None:
                profile_file.unlink(missing_ok=True)
            bridge.shutdown()
            bridge.server_close()
            mock.shutdown()
            mock.server_close()


if __name__ == "__main__":
    main()
