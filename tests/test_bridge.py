import concurrent.futures, gzip, importlib.util, json, socket, sys, threading, unittest, zlib
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import httpx, zstandard

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "router"))
from tool_bridge import BridgeError, ToolBridge, alias_for, dumps

spec = importlib.util.spec_from_file_location(
    "router", ROOT / "router/hybrid-model-router.py"
)
router = importlib.util.module_from_spec(spec)
spec.loader.exec_module(router)


def fn(name="echo"):
    return dict(
        type="function",
        name=name,
        description="Echo",
        strict=False,
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    )


def ns(name="repo", tool=None):
    return dict(
        type="namespace", name=name, description="Test namespace", tools=[tool or fn()]
    )


def custom():
    return dict(
        type="custom",
        name="apply_patch",
        description="Apply patch",
        format={"type": "text"},
    )


def cfg(port=8831, local_port=8826):
    return dict(
        host="127.0.0.1",
        port=port,
        logMetadata=False,
        models=[
            dict(
                id="test-local",
                aliases=["local-alias"],
                baseUrl=f"http://127.0.0.1:{local_port}/v1",
                catalogPath="catalog.json",
            )
        ],
    )


class BridgeTests(unittest.TestCase):
    def test_plain_function(self):
        self.assertEqual(ToolBridge().prepare({"tools": [fn()]})["tools"], [fn()])

    def test_namespace(self):
        b = ToolBridge()
        t = b.prepare({"tools": [ns()]})["tools"][0]
        o = b.restore_item(
            dict(type="function_call", name=t["name"], arguments="{}", call_id="c")
        )
        self.assertEqual(
            (o["namespace"], o["name"], o["call_id"]), ("repo", "echo", "c")
        )
        self.assertEqual(t["parameters"], fn()["parameters"])

    def test_aliases(self):
        aliases = [
            alias_for("function", n, "a" * 200) for n in ["a.b", "a_b", "a" * 200]
        ]
        self.assertEqual(len(set(aliases)), 3)
        self.assertTrue(all(len(n) <= 64 for n in aliases))

    def test_duplicate(self):
        with self.assertRaises(BridgeError):
            ToolBridge().prepare({"tools": [fn(), fn()]})

    def test_custom_json(self):
        b = ToolBridge()
        a = b.prepare({"tools": [custom()]})["tools"][0]["name"]
        raw = 'patch\n"Ã¤" \\ end'
        o = b.restore_item(
            dict(
                type="function_call",
                name=a,
                arguments=dumps({"input": raw}),
                call_id="c",
            )
        )
        self.assertEqual(
            o, dict(type="custom_tool_call", name="apply_patch", input=raw, call_id="c")
        )

    def test_history_custom(self):
        orig = dict(
            type="custom_tool_call", name="apply_patch", input="patch\nÎ±", call_id="c"
        )
        b = ToolBridge()
        o = b.prepare(
            {
                "tools": [],
                "input": [
                    orig,
                    dict(type="custom_tool_call_output", call_id="c", output="ok"),
                ],
            }
        )["input"]
        self.assertEqual(b.restore_item(o[0]), orig)
        self.assertEqual(o[1]["type"], "function_call_output")

    def test_history_namespace(self):
        orig = dict(
            type="function_call",
            namespace="repo",
            name="echo",
            arguments="{}",
            call_id="c",
        )
        b = ToolBridge()
        o = b.prepare({"tools": [], "input": [orig]})["input"][0]
        self.assertEqual(b.restore_item(o), orig)

    def test_choice(self):
        o = ToolBridge().prepare(
            {
                "tools": [ns()],
                "tool_choice": dict(type="function", namespace="repo", name="echo"),
            }
        )
        self.assertEqual(o["tools"][0]["name"], o["tool_choice"]["name"])

    def test_allowed_choice(self):
        o = ToolBridge().prepare(
            {
                "tools": [ns()],
                "tool_choice": dict(
                    type="allowed_tools",
                    mode="auto",
                    tools=[dict(type="function", namespace="repo", name="echo")],
                ),
            }
        )
        self.assertEqual(o["tools"][0]["name"], o["tool_choice"]["tools"][0]["name"])

    def test_unsupported(self):
        for kind in ["web_search", "computer", "file_search", "unknown"]:
            with self.subTest(kind=kind), self.assertRaises(BridgeError):
                ToolBridge().prepare({"tools": [dict(type=kind)]})

    def test_invalid_wrapper(self):
        for value in ["{", "{}", '{"input":2}', '{"input":"x","extra":1}']:
            with self.subTest(value=value), self.assertRaises(BridgeError):
                ToolBridge.raw_input(value)

    def test_unknown_alias(self):
        with self.assertRaises(BridgeError):
            ToolBridge().restore_item(
                dict(type="function_call", name="tb_unknown", arguments="{}")
            )

    def test_custom_sse(self):
        b = ToolBridge()
        a = b.prepare({"tools": [custom()]})["tools"][0]["name"]
        raw = '*** Begin Patch\n"Ã¤" \\ end'
        args = dumps({"input": raw})
        item = dict(id="i", type="function_call", name=a, arguments=args, call_id="c")
        events = [
            dict(
                type="response.output_item.added",
                output_index=0,
                item=dict(item, arguments=""),
            )
        ]
        events += [
            dict(
                type="response.function_call_arguments.delta",
                output_index=0,
                item_id="i",
                delta=ch,
            )
            for ch in args
        ]
        events += [
            dict(
                type="response.function_call_arguments.done",
                output_index=0,
                item_id="i",
                arguments=args,
            ),
            dict(type="response.output_item.done", output_index=0, item=item),
            dict(type="response.completed", response={"output": [item]}),
        ]
        out = [o for e in events for o in b.transform_event(e)]
        self.assertEqual("".join(o.get("delta", "") for o in out), raw)
        self.assertEqual(
            sum(o["type"] == "response.custom_tool_call_input.done" for o in out), 1
        )
        self.assertEqual(out[-1]["response"]["output"][0]["input"], raw)

    def test_sse_missing_done(self):
        b = ToolBridge()
        a = b.prepare({"tools": [custom()]})["tools"][0]["name"]
        item = dict(
            id="i",
            type="function_call",
            name=a,
            arguments='{"input":"patch"}',
            call_id="c",
        )
        b.transform_event(
            dict(
                type="response.output_item.added",
                output_index=0,
                item=dict(item, arguments=""),
            )
        )
        out = b.transform_event(
            dict(type="response.output_item.done", output_index=0, item=item)
        )
        self.assertEqual(out[0]["delta"], "patch")

    def test_sse_multiline(self):
        raw = b"".join(
            ToolBridge().translate_sse(
                [
                    'data: {"type":"response.output_text.delta",',
                    'data: "delta":"hi"}',
                    "",
                    "data: [DONE]",
                    "",
                ]
            )
        )
        self.assertIn(b'"delta":"hi"', raw)
        self.assertIn(b"[DONE]", raw)

    def test_sequences(self):
        b = ToolBridge()
        self.assertEqual(
            b.transform_event(dict(type="response.created", sequence_number=99))[0][
                "sequence_number"
            ],
            0,
        )

    def test_parallel(self):
        def work(n):
            b = ToolBridge()
            a = b.prepare({"tools": [ns(str(n))]})["tools"][0]["name"]
            return b.restore_item(dict(type="function_call", name=a, arguments="{}"))[
                "namespace"
            ]

        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            self.assertEqual(list(pool.map(work, range(50))), list(map(str, range(50))))


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.r = router.Router(cfg(), Path("."))

    def test_local(self):
        for m in ["test-local", "TEST-LOCAL", "local-alias"]:
            self.assertIsNotNone(self.r.route("/v1/responses", {"model": m}, "POST")[1])

    def test_cloud(self):
        self.assertIsNone(
            self.r.route("/v1/responses", {"model": "gpt-test"}, "POST")[1]
        )

    def test_unknown_missing(self):
        for p in [{}, {"model": "Qwen-typo"}, {"model": None}, {"model": []}]:
            with self.assertRaises(BridgeError):
                self.r.route("/v1/responses", p, "POST")

    def test_json(self):
        for raw in [b"{", b"[]", b"null", b""]:
            with self.assertRaises(BridgeError):
                router.parse_payload(raw, "identity")

    def test_gzip_deflate(self):
        raw = dumps({"model": "test-local"}).encode()
        for enc, body in [
            ("gzip", gzip.compress(raw)),
            ("deflate", zlib.compress(raw)),
        ]:
            self.assertIsNotNone(
                self.r.route("/v1/responses", router.parse_payload(body, enc), "POST")[
                    1
                ]
            )

    def test_zstd(self):
        raw = zstandard.ZstdCompressor().compress(
            dumps({"model": "test-local"}).encode()
        )
        self.assertIsNotNone(
            self.r.route("/v1/responses", router.parse_payload(raw, "zstd"), "POST")[1]
        )

    def test_invalid_encoding(self):
        for enc in ["zstd", "gzip", "br", "gzip, zstd"]:
            with self.assertRaises(BridgeError):
                router.parse_payload(b"bad", enc)

    def test_size_limit(self):
        for enc, raw in [
            ("gzip", gzip.compress(b"x" * 100)),
            ("deflate", zlib.compress(b"x" * 100)),
            ("zstd", zstandard.ZstdCompressor().compress(b"x" * 100)),
        ]:
            with self.assertRaises(BridgeError):
                router.decode_body(raw, enc, 30)

    def test_local_auth_allowlist(self):
        out = router.headers_for(
            {
                "Authorization": "SECRET",
                "x-future-auth": "SECRET",
                "Cookie": "SECRET",
                "chatgpt-account-id": "SECRET",
            },
            local=True,
        )
        self.assertNotIn("SECRET", dumps(out))

    def test_cloud_auth(self):
        out = router.headers_for(
            {"Authorization": "TEST", "Content-Encoding": "zstd", "Host": "localhost"},
            local=False,
        )
        self.assertEqual(out["Authorization"], "TEST")
        self.assertEqual(out["Content-Encoding"], "zstd")
        self.assertNotIn("Host", out)

    def test_compaction(self):
        with self.assertRaises(BridgeError):
            self.r.route("/v1/responses/compact", {"model": "test-local"}, "POST")

    def test_unknown_path(self):
        with self.assertRaises(BridgeError):
            self.r.route("/v1/evil", {"model": "gpt-test"}, "POST")

    def test_search_optin(self):
        with self.assertRaises(BridgeError):
            self.r.route("/v1/alpha/search", {"model": "test-local"}, "POST")

    def test_search_model(self):
        self.r.config.update(allowCloudSearch=True, searchModel="gpt-test")
        u, l, p = self.r.route("/v1/alpha/search", {"model": "test-local"}, "POST")
        self.assertIsNone(l)
        self.assertEqual(p["model"], "gpt-test")

    def test_cloud_origin(self):
        with self.assertRaises(BridgeError):
            router.Router(cfg() | {"cloudBase": "https://evil.invalid"}, Path("."))

    def test_local_origin(self):
        c = cfg()
        c["models"][0]["baseUrl"] = "https://evil.invalid/v1"
        with self.assertRaises(BridgeError):
            router.Router(c, Path("."))

    def test_bind(self):
        with self.assertRaises(BridgeError):
            router.Router(cfg() | {"host": "0.0.0.0"}, Path("."))


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seen = []

        class Upstream(BaseHTTPRequestHandler):
            def do_POST(self):
                p = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                cls.seen.append((dict(self.headers), p))
                if p.get("input") == "FAIL":
                    self.send_response(503)
                    self.end_headers()
                    self.wfile.write(b'{"error":"busy"}')
                    return
                item = dict(
                    id="item1",
                    type="function_call",
                    name=p["tools"][0]["name"],
                    arguments='{"value":"ok"}',
                    call_id="call1",
                )
                result = {"output": [item]}
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    "text/event-stream" if p.get("stream") else "application/json",
                )
                self.end_headers()
                if p.get("stream"):
                    for e in [
                        dict(
                            type="response.output_item.added",
                            item=dict(item, arguments=""),
                            output_index=0,
                        ),
                        dict(
                            type="response.output_item.done", item=item, output_index=0
                        ),
                        dict(type="response.completed", response=result),
                    ]:
                        self.wfile.write(("data: " + dumps(e) + "\n\n").encode())
                        self.wfile.flush()
                else:
                    self.wfile.write(dumps(result).encode())

            def log_message(self, *args):
                pass

        cls.up = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        threading.Thread(target=cls.up.serve_forever, daemon=True).start()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        cls.server = router.make_server(
            router.Router(cfg(port, cls.up.server_port), Path("."))
        )
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.up.shutdown()
        cls.up.server_close()

    def test_roundtrip(self):
        r = httpx.post(
            self.base + "/v1/responses",
            json={"model": "test-local", "tools": [ns()]},
            headers={"Authorization": "fake-secret", "x-future-auth": "fake-secret"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["output"][0]["namespace"], "repo")
        self.assertNotIn("fake-secret", str(self.seen[-1][0]))

    def test_chunked_zstd(self):
        raw = zstandard.ZstdCompressor().compress(
            dumps({"model": "test-local", "tools": [ns()]}).encode()
        )
        r = httpx.post(
            self.base + "/v1/responses",
            content=iter([raw[:9], raw[9:]]),
            headers={"Content-Encoding": "zstd"},
        )
        self.assertEqual(r.status_code, 200)

    def test_invalid_never_upstream(self):
        n = len(self.seen)
        r = httpx.post(self.base + "/v1/responses", content=b"bad")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(n, len(self.seen))

    def test_sse(self):
        r = httpx.post(
            self.base + "/v1/responses",
            json={"model": "test-local", "tools": [ns()], "stream": True},
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn('"namespace":"repo"', r.text)

    def test_503_no_fallback(self):
        r = httpx.post(
            self.base + "/v1/responses",
            json={"model": "test-local", "tools": [], "input": "FAIL"},
        )
        self.assertEqual(r.status_code, 503)

    def test_health(self):
        self.assertEqual(httpx.get(self.base + "/health").json()["version"], "0.2.0")

    def test_origin(self):
        r = httpx.post(
            self.base + "/v1/responses",
            json={"model": "test-local"},
            headers={"Origin": "https://evil.invalid"},
        )
        self.assertEqual(r.status_code, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
