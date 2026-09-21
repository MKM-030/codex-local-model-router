import socket, threading, unittest
from pathlib import Path
import httpx
from test_bridge import router, cfg


def unused_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.backend = unused_port()
        self.listen = unused_port()
        self.engine = router.Router(cfg(self.listen, self.backend), Path('.'))
        self.server = router.make_server(self.engine)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = 'http://127.0.0.1:' + str(self.listen)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_missing_backend_returns_explicit_503(self):
        response = httpx.post(self.url + '/v1/responses',
                              json={'model': 'test-local', 'input': 'health probe'},
                              trust_env=False)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error']['code'], 'local_backend_unavailable')

    def test_ready_distinct_from_health(self):
        self.assertEqual(httpx.get(self.url + '/health', trust_env=False).status_code, 200)
        response = httpx.get(self.url + '/ready', trust_env=False)
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()['ready'])
        self.assertIn(response.json()['upstreams'][0]['errorCode'],
                      ['local_backend_unavailable', 'local_backend_timeout'])
