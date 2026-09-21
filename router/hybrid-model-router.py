"""Loopback Codex router v0.2.0. Tools execute in Codex; routing is fail-closed."""

from __future__ import annotations
import argparse, copy, gzip, io, ipaddress, json, logging, os, select, socket, threading, time, uuid, zlib
from logging.handlers import RotatingFileHandler
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
import httpx
from tool_bridge import BridgeError, ToolBridge, dumps

VERSION = "0.2.0"
CLOUD_BASE = "https://chatgpt.com/backend-api/codex"
MAX_BODY = 64 * 1024 * 1024
HOP_HEADERS = {
    "host",
    "content-length",
    "connection",
    "transfer-encoding",
    "keep-alive",
    "te",
    "trailer",
    "upgrade",
    "proxy-authorization",
    "proxy-authenticate",
}
LOCAL_SECRET_HEADERS = {
    "authorization",
    "chatgpt-account-id",
    "cookie",
    "x-openai-actor-authorization",
}


def api_suffix(path):
    for prefix in ("/backend-api/codex", "/v1"):
        if (
            path == prefix
            or path.startswith(prefix + "/")
            or path.startswith(prefix + "?")
        ):
            return path[len(prefix) :] or "/"
    return path


def loopback_url(url):
    u = urlsplit(url)
    try:
        loopback = ipaddress.ip_address(u.hostname or "").is_loopback
    except ValueError:
        loopback = u.hostname == "localhost"
    return (
        u.scheme in ("http", "https")
        and loopback
        and not u.username
        and not u.password
        and not u.query
        and not u.fragment
    )


def decode_body(raw, encoding, limit=MAX_BODY):
    encoding = encoding.strip().lower()
    try:
        if encoding in ("", "identity"):
            decoded = raw
        elif encoding in ("gzip", "x-gzip"):
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as f:
                decoded = f.read(limit + 1)
        elif encoding == "deflate":
            obj = zlib.decompressobj()
            decoded = obj.decompress(raw, limit + 1)
            if not obj.eof and len(decoded) <= limit:
                raise BridgeError(
                    "Invalid or truncated deflate request", "invalid_encoding"
                )
        elif encoding == "zstd":
            try:
                import zstandard
            except ImportError as exc:
                raise BridgeError(
                    "Install the zstandard dependency; nothing was sent upstream",
                    "missing_zstd_dependency",
                    415,
                ) from exc
            with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(raw)) as f:
                decoded = f.read(limit + 1)
        else:
            raise BridgeError(
                f"Unsupported Content-Encoding {encoding!r}; nothing was sent upstream",
                "unsupported_encoding",
                415,
            )
    except BridgeError:
        raise
    except Exception as exc:
        raise BridgeError(
            "Invalid compressed request; nothing was sent upstream", "invalid_encoding"
        ) from exc
    if len(decoded) > limit:
        raise BridgeError(
            "Decoded request exceeds body size limit", "request_too_large", 413
        )
    return decoded


def parse_payload(raw, encoding):
    decoded = decode_body(raw, encoding)
    try:
        payload = json.loads(decoded)
    except (ValueError, UnicodeError) as exc:
        raise BridgeError(
            "Invalid JSON request; refused cloud fallback", "invalid_json"
        ) from exc
    if not isinstance(payload, dict):
        raise BridgeError("Request JSON must be an object", "invalid_json")
    return payload


def headers_for(headers, *, local, changed=False):
    # Allowlist ensures future/unknown auth headers cannot leak to local servers.
    if local:
        return {
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json",
            "Accept-Encoding": "identity",
        }
    banned = HOP_HEADERS | ({"content-encoding"} if changed else set())
    result = {k: v for k, v in headers.items() if k.lower() not in banned}
    result["Accept-Encoding"] = "identity"
    return result


class Router:
    def __init__(self, config, base_dir):
        self.config = copy.deepcopy(config)
        self.base_dir = base_dir
        if config.get("host", "127.0.0.1") != "127.0.0.1":
            raise BridgeError("This router must bind to 127.0.0.1")
        if config.get("cloudBase", CLOUD_BASE).rstrip("/") != CLOUD_BASE:
            raise BridgeError(
                "Cloud auth forwarding is restricted to the ChatGPT Codex origin"
            )
        self.port = int(config.get("port", 8831))
        if not 1 <= self.port <= 65535:
            raise BridgeError("Invalid router port")
        self.models = {}
        for item in config.get("models", []):
            if (
                not isinstance(item.get("id"), str)
                or not item["id"]
                or not loopback_url(item.get("baseUrl", ""))
            ):
                raise BridgeError(
                    "Every local model requires an id and a loopback baseUrl"
                )
            if urlsplit(item["baseUrl"]).port == self.port:
                raise BridgeError("Local endpoint points back at router")
            for alias in [item["id"], *item.get("aliases", [])]:
                key = alias.casefold()
                if key in self.models:
                    raise BridgeError("Duplicate local model id/alias")
                self.models[key] = item
        if not self.models:
            raise BridgeError("At least one local model is required")
        self.cloud_models = set(config.get("cloudModels", []))
        self.cloud_prefixes = tuple(
            config.get("cloudModelPrefixes", ["gpt-", "codex-", "o1", "o3", "o4"])
        )
        self.lock = threading.Lock()
        self.active = 0
        self.logger = logging.getLogger("codex_local_router." + str(id(self)))
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        if config.get("logMetadata", True):
            handler = RotatingFileHandler(
                base_dir / "router-events.jsonl",
                maxBytes=2_000_000,
                backupCount=2,
                encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            self.logger.addHandler(handler)
        else:
            self.logger.addHandler(logging.NullHandler())

    def local_model(self, model):
        return self.models.get(model.casefold()) if isinstance(model, str) else None

    def route(self, path, payload, method):
        suffix = api_suffix(path)
        plain = urlsplit(suffix).path
        if method == "GET" and plain == "/models":
            return CLOUD_BASE + suffix, None, payload
        if method != "POST":
            raise BridgeError("Unsupported router route", "route_not_supported", 404)
        if plain == "/alpha/search":
            # Standalone search is a CLOUD TOOL. Codex supplies recent input.
            if not self.config.get("allowCloudSearch", False):
                raise BridgeError(
                    "Cloud search is disabled in router-config.json",
                    "cloud_search_disabled",
                )
            if self.local_model((payload or {}).get("model")):
                search_model = self.config.get("searchModel")
                if not search_model or not search_model.startswith(self.cloud_prefixes):
                    raise BridgeError(
                        "Configure searchModel with an authorized cloud model for standalone search",
                        "search_model_required",
                    )
                payload = copy.deepcopy(payload)
                payload["model"] = search_model
            model = (payload or {}).get("model")
            if not isinstance(model, str) or (
                model not in self.cloud_models
                and not model.startswith(self.cloud_prefixes)
            ):
                raise BridgeError(
                    "Unknown search model; refused cloud fallback", "unknown_model"
                )
            return CLOUD_BASE + suffix, None, payload
        if plain not in ("/responses", "/responses/compact", "/responses/input_tokens"):
            raise BridgeError("Unsupported router route", "route_not_supported", 404)
        model = (payload or {}).get("model")
        if not isinstance(model, str) or not model.strip():
            raise BridgeError(
                "Missing model id; refused cloud fallback", "missing_model"
            )
        local = self.local_model(model)
        if local:
            if plain != "/responses":
                raise BridgeError(
                    "Local model cannot use hosted compaction/token endpoints. Use Codex client-side compaction; no local prompt was forwarded to OpenAI",
                    "local_endpoint_not_supported",
                )
            return local["baseUrl"].rstrip("/") + suffix, local, payload
        if model not in self.cloud_models and not model.startswith(self.cloud_prefixes):
            raise BridgeError(
                "Unknown model id; refused cloud fallback. Add exact local id or alias to router-config.json",
                "unknown_model",
            )
        return CLOUD_BASE + suffix, None, payload

    def catalogs(self):
        result = {}
        for model in self.config["models"]:
            path = Path(model.get("catalogPath", "local-model-catalog.json"))
            if not path.is_absolute():
                path = self.base_dir / path
            with path.open(encoding="utf-8-sig") as f:
                catalog = json.load(f)
            entry = next(
                (m for m in catalog.get("models", []) if m.get("slug") == model["id"]),
                None,
            )
            if entry is None:
                raise BridgeError(
                    "Local model is missing from metadata catalog",
                    "invalid_catalog",
                    500,
                )
            entry = copy.deepcopy(entry)
            for key in (
                "include_skills_usage_instructions",
                "include_plugin_usage_instructions",
                "include_apps_usage_instructions",
            ):
                entry[key] = True
            entry["use_responses_lite"] = False
            result[entry["slug"]] = entry
        return list(result.values())

    def merge_catalog(self, data):
        if not isinstance(data.get("models"), list):
            raise BridgeError(
                "Cloud model catalog has unexpected schema", "invalid_catalog", 502
            )
        local = self.catalogs()
        local_ids = {m["slug"] for m in local}
        with self.lock:
            self.cloud_models.update(
                m["slug"] for m in data["models"] if isinstance(m.get("slug"), str)
            )
        return dict(
            data,
            models=[m for m in data["models"] if m.get("slug") not in local_ids]
            + local,
        )

    def readiness(self):
        upstreams = []
        for item in self.config["models"]:
            record = {"model": item["id"], "baseUrl": item["baseUrl"], "ready": False}
            try:
                headers = {}
                if item.get("apiKeyEnv"):
                    key = os.environ.get(item["apiKeyEnv"])
                    if not key:
                        raise ValueError("local_auth_missing")
                    headers["Authorization"] = "Bearer " + key
                with httpx.Client(timeout=2, trust_env=False, follow_redirects=False) as client:
                    response = client.get(item["baseUrl"].rstrip("/") + "/models", headers=headers)
                record["httpStatus"] = response.status_code
                if response.status_code == 200:
                    ids = [m.get("id") for m in response.json().get("data", [])]
                    expected = item.get("upstreamModel", item["id"])
                    record["ready"] = expected in ids
                    if not record["ready"]:
                        record["errorCode"] = "local_model_id_mismatch"
                else:
                    record["errorCode"] = "local_backend_not_ready"
            except httpx.ConnectError:
                record["errorCode"] = "local_backend_unavailable"
            except httpx.TimeoutException:
                record["errorCode"] = "local_backend_timeout"
            except Exception as exc:
                record["errorCode"] = "readiness_error"
                record["errorType"] = type(exc).__name__
            upstreams.append(record)
        return dict(self.health(), ready=all(u["ready"] for u in upstreams), upstreams=upstreams)

    def health(self):
        with self.lock:
            active = self.active
        return {
            "status": "ok",
            "version": VERSION,
            "pid": os.getpid(),
            "activeRequests": active,
            "localModels": [m["id"] for m in self.config["models"]],
            "toolBridge": True,
            "failClosedRouting": True,
            "cloudSearchEnabled": bool(self.config.get("allowCloudSearch", False)),
        }


class Server(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_server(router):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "CodexLocalRouter/" + VERSION

        def log_message(self, *args):
            pass

        def send_json(self, status, payload):
            raw = dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(raw)
            self.wfile.flush()

        def read_body(self):
            self.connection.settimeout(30)
            te = self.headers.get("Transfer-Encoding", "").lower()
            cl = self.headers.get("Content-Length")
            if te and cl:
                raise BridgeError("Conflicting request framing", "invalid_http")
            if te:
                if te != "chunked":
                    raise BridgeError("Unsupported Transfer-Encoding", "invalid_http")
                body = bytearray()
                while True:
                    line = self.rfile.readline(8193)
                    if len(line) > 8192 or not line.endswith(b"\r\n"):
                        raise BridgeError("Invalid chunk header", "invalid_http")
                    try:
                        length = int(line.split(b";", 1)[0].strip(), 16)
                    except ValueError as exc:
                        raise BridgeError("Invalid chunk size", "invalid_http") from exc
                    if length < 0 or len(body) + length > MAX_BODY:
                        raise BridgeError("Body too large", "request_too_large", 413)
                    if length == 0:
                        for _ in range(100):
                            trailer = self.rfile.readline(8193)
                            if trailer == b"\r\n":
                                return bytes(body)
                            if not trailer or len(trailer) > 8192:
                                break
                        raise BridgeError("Invalid chunk trailers", "invalid_http")
                    chunk = self.rfile.read(length)
                    if len(chunk) != length or self.rfile.read(2) != b"\r\n":
                        raise BridgeError("Truncated chunk", "invalid_http")
                    body.extend(chunk)
            try:
                length = int(cl or "0")
            except ValueError as exc:
                raise BridgeError("Invalid Content-Length", "invalid_http") from exc
            if length < 0 or length > MAX_BODY:
                raise BridgeError("Body too large", "request_too_large", 413)
            body = self.rfile.read(length)
            if len(body) != length:
                raise BridgeError("Truncated request body", "invalid_http")
            return body

        def handle_proxy(self):
            started = time.monotonic()
            info = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "requestId": uuid.uuid4().hex,
                "method": self.command,
                "path": urlsplit(self.path).path,
                "route": "rejected",
            }
            headers_sent = False
            local = None
            stop = threading.Event()
            client_disconnected = threading.Event()
            counted = not (self.command == "GET" and self.path in ("/health", "/healthz", "/ready", "/readyz"))
            with router.lock:
                router.active += int(counted)
            try:
                host = self.headers.get("Host", "").split(":")[0].lower()
                if host not in ("127.0.0.1", "localhost"):
                    raise BridgeError("Unexpected Host header", "invalid_host")
                origin = self.headers.get("Origin")
                if origin and urlsplit(origin).hostname not in (
                    "127.0.0.1",
                    "localhost",
                ):
                    raise BridgeError(
                        "Cross-origin requests are not permitted", "invalid_origin", 403
                    )
                if self.command == "GET" and self.path in ("/health", "/healthz"):
                    self.send_json(200, router.health())
                    info.update(status=200, route="health")
                    return
                if self.command == "GET" and self.path in ("/ready", "/readyz"):
                    ready = router.readiness()
                    code = 200 if ready["ready"] else 503
                    self.send_json(code, ready)
                    info.update(status=code, route="readiness")
                    return
                raw = self.read_body() if self.command == "POST" else b""
                encoding = self.headers.get("Content-Encoding", "identity")
                payload = (
                    parse_payload(raw, encoding) if self.command == "POST" else None
                )
                info.update(encoding=encoding, model=(payload or {}).get("model"))
                url, local, routed_payload = router.route(
                    self.path, payload, self.command
                )
                info["route"] = (
                    "local"
                    if local
                    else (
                        "cloud_search"
                        if api_suffix(self.path).startswith("/alpha/search")
                        else "cloud"
                    )
                )
                bridge = None
                changed = False
                if local:
                    bridge = ToolBridge()
                    routed_payload = bridge.prepare(routed_payload)
                    routed_payload["model"] = local.get("upstreamModel", local["id"])
                    info["functionTools"] = len(routed_payload.get("tools", []))
                    raw = dumps(routed_payload).encode("utf-8")
                    changed = True
                elif routed_payload is not payload:
                    raw = dumps(routed_payload).encode("utf-8")
                    changed = True
                headers = headers_for(self.headers, local=bool(local), changed=changed)
                if self.command == "GET":
                    headers = {
                        k: v
                        for k, v in headers.items()
                        if k.lower() not in {"if-none-match", "if-modified-since"}
                    }
                if local and local.get("apiKeyEnv"):
                    key = os.environ.get(local["apiKeyEnv"])
                    if not key:
                        raise BridgeError(
                            "Configured local API key environment variable is missing",
                            "local_auth_missing",
                            500,
                        )
                    headers["Authorization"] = "Bearer " + key
                self.connection.settimeout(600)
                with httpx.Client(
                    timeout=httpx.Timeout(600, connect=10, pool=10),
                    follow_redirects=False,
                    trust_env=not bool(local),
                ) as client:
                    with client.stream(
                        self.command, url, headers=headers, content=raw
                    ) as response:
                        info["status"] = response.status_code
                        if (
                            self.command == "GET"
                            and api_suffix(self.path).split("?")[0] == "/models"
                            and response.status_code == 200
                        ):
                            self.send_json(
                                200, router.merge_catalog(json.loads(response.read()))
                            )
                            return
                        content_type = response.headers.get("content-type", "")
                        is_sse = bool(
                            bridge
                            and response.status_code == 200
                            and "text/event-stream" in content_type
                        )
                        if bridge and response.status_code == 200 and not is_sse:
                            self.send_json(
                                200,
                                bridge.restore_response(json.loads(response.read())),
                            )
                            return
                        self.send_response(response.status_code)
                        for key, value in response.headers.items():
                            if key.lower() not in HOP_HEADERS | {"server", "date"} | (
                                {"content-encoding", "etag"} if is_sse else set()
                            ):
                                self.send_header(key, value)
                        self.send_header("Connection", "close")
                        self.end_headers()
                        headers_sent = True

                        def cancel_on_disconnect():
                            while not stop.wait(0.2):
                                try:
                                    readable, _, _ = select.select(
                                        [self.connection], [], [], 0
                                    )
                                    if (
                                        readable
                                        and self.connection.recv(1, socket.MSG_PEEK)
                                        == b""
                                    ):
                                        client_disconnected.set()
                                        response.close()
                                        return
                                except (OSError, ValueError):
                                    return

                        threading.Thread(
                            target=cancel_on_disconnect, daemon=True
                        ).start()
                        chunks = (
                            bridge.translate_sse(response.iter_lines())
                            if is_sse
                            else response.iter_raw()
                        )
                        for chunk in chunks:
                            if chunk:
                                self.wfile.write(chunk)
                                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                info["errorCode"] = "client_disconnected"
            except Exception as exc:
                if client_disconnected.is_set():
                    info["errorCode"] = "client_disconnected"
                    return
                if local and isinstance(exc, httpx.ConnectError):
                    exc = BridgeError(
                        "Local model " + local["id"] + " is not reachable at " + local["baseUrl"] + ". Start the model server and check /ready. No request was sent to cloud inference.",
                        "local_backend_unavailable", 503,
                    )
                elif local and isinstance(exc, httpx.TimeoutException):
                    exc = BridgeError(
                        "Local model server timed out. Check loading state, active slots and /ready. No cloud fallback was attempted.",
                        "local_backend_timeout", 504,
                    )
                err = (
                    exc
                    if isinstance(exc, BridgeError)
                    else BridgeError(
                        "Router transport/stream failure: " + type(exc).__name__,
                        "transport_error",
                        502,
                    )
                )
                info.update(status=err.status, errorCode=err.code)
                try:
                    if headers_sent:
                        evt = {"type": "error", "code": err.code, "message": str(err)}
                        self.wfile.write(
                            ("event: error\ndata: " + dumps(evt) + "\n\n").encode(
                                "utf-8"
                            )
                        )
                        self.wfile.flush()
                    else:
                        self.send_json(
                            err.status,
                            {
                                "error": {
                                    "message": str(err),
                                    "type": "router_error",
                                    "code": err.code,
                                }
                            },
                        )
                except OSError:
                    pass
            finally:
                stop.set()
                self.close_connection = True
                with router.lock:
                    router.active -= int(counted)
                info["elapsedMs"] = round((time.monotonic() - started) * 1000)
                router.logger.info(dumps(info))

        do_POST = handle_proxy
        do_GET = handle_proxy

    return Server(("127.0.0.1", router.port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default=os.environ.get(
            "CODEX_LOCAL_ROUTER_CONFIG",
            str(Path(__file__).with_name("router-config.json")),
        ),
    )
    args = parser.parse_args()
    path = Path(args.config).resolve()
    with path.open(encoding="utf-8-sig") as f:
        config = json.load(f)
    router = Router(config, path.parent)
    with make_server(router) as server:
        import sys

        if sys.stdout is not None:
            print(
                f"Codex local router {VERSION}: http://127.0.0.1:{router.port}",
                flush=True,
            )
        server.serve_forever()


if __name__ == "__main__":
    main()
