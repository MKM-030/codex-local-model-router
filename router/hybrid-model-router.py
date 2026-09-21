from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os

import httpx

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get(
    "CODEX_LOCAL_ROUTER_CONFIG",
    os.path.join(BASE_DIR, "router-config.json"),
)
DROP_REQUEST_HEADERS = {"host", "content-length", "connection", "transfer-encoding"}
DROP_RESPONSE_HEADERS = {"content-length", "connection", "transfer-encoding"}
LOCAL_SECRET_HEADERS = {
    "authorization",
    "chatgpt-account-id",
    "cookie",
    "x-openai-actor-authorization",
}


def load_config():
    with open(CONFIG_PATH, encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if not data.get("models"):
        raise RuntimeError("router-config.json must contain at least one local model")
    return data
CONFIG = load_config()
HOST = CONFIG.get("host", "127.0.0.1")
PORT = int(CONFIG.get("port", 8831))
CLOUD_BASE = CONFIG.get(
    "cloudBase",
    "https://chatgpt.com/backend-api/codex",
).rstrip("/")
LOCAL_MODELS = {item["id"]: item for item in CONFIG["models"]}


def api_suffix(path):
    if path.startswith("/backend-api/codex"):
        return path[len("/backend-api/codex") :]
    if path.startswith("/v1"):
        return path[len("/v1") :]
    return path


def request_model(body):
    if not body:
        return None
    try:
        payload = json.loads(body)
    except Exception:
        return None
    return payload.get("model") if isinstance(payload, dict) else None


def target_for(path, body):
    model_id = request_model(body)
    local = LOCAL_MODELS.get(model_id)
    suffix = api_suffix(path)
    if local is not None:
        return local["baseUrl"].rstrip("/") + suffix, True
    return CLOUD_BASE + suffix, False
def catalog_models():
    result = []
    for item in CONFIG["models"]:
        catalog_path = item.get("catalogPath", "local-model-catalog.json")
        if not os.path.isabs(catalog_path):
            catalog_path = os.path.join(BASE_DIR, catalog_path)
        with open(catalog_path, encoding="utf-8-sig") as handle:
            catalog = json.load(handle)
        result.extend(catalog.get("models", []))
    return result


def merge_model_catalog(raw):
    data = json.loads(raw)
    models = data.get("models")
    if not isinstance(models, list):
        return raw
    local_models = catalog_models()
    local_slugs = {model.get("slug") for model in local_models}
    data["models"] = [
        model for model in models if model.get("slug") not in local_slugs
    ] + local_models
    return json.dumps(
        data,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _proxy(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        url, is_local = target_for(self.path, body)
        headers = {}
        for key, value in self.headers.items():
            lower = key.lower()
            if lower in DROP_REQUEST_HEADERS:
                continue
            if is_local and lower in LOCAL_SECRET_HEADERS:
                continue
            headers[key] = value
        headers["Accept-Encoding"] = "identity"

        try:
            with httpx.Client(timeout=None, follow_redirects=False) as client:
                with client.stream(
                    self.command,
                    url,
                    headers=headers,
                    content=body,
                ) as response:
                    if (
                        self.command == "GET"
                        and api_suffix(self.path).startswith("/models")
                        and response.status_code == 200
                    ):
                        payload = merge_model_catalog(response.read())
                        self.send_response(response.status_code)
                        for key, value in response.headers.items():
                            if key.lower() not in DROP_RESPONSE_HEADERS:
                                self.send_header(key, value)
                        self.send_header("Content-Length", str(len(payload)))
                        self.send_header("Connection", "close")
                        self.end_headers()
                        self.wfile.write(payload)
                        return
                    self.send_response(response.status_code)
                    for key, value in response.headers.items():
                        if key.lower() not in DROP_RESPONSE_HEADERS:
                            self.send_header(key, value)
                    self.send_header("Connection", "close")
                    self.end_headers()
                    for chunk in response.iter_raw():
                        if chunk:
                            self.wfile.write(chunk)
                            self.wfile.flush()
        except Exception as exc:
            payload = json.dumps(
                {
                    "error": {
                        "message": f"router: {type(exc).__name__}: {exc}",
                        "type": "router_error",
                    }
                }
            ).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
        finally:
            self.close_connection = True

    def do_POST(self):
        self._proxy()

    def do_GET(self):
        self._proxy()

    def log_message(self, fmt, *args):
        return
if __name__ == "__main__":
    print(
        f"codex-local-model-router listening on http://{HOST}:{PORT}",
        flush=True,
    )
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
