"""Real loopback HTTP transport contracts; vectors are test doubles, not a model."""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from voren.knowledge.embeddings import EmbeddingError, HTTPEmbeddingProvider


class HTTPEmbeddingIntegrationTest(unittest.TestCase):
    def start_server(self, handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def close():
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.addCleanup(close)
        return f"http://127.0.0.1:{server.server_port}/v1/embeddings"

    def test_real_post_matches_response_indexes_and_records_usage(self):
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append((self.path, self.headers.get("Authorization"), body))
                payload = json.dumps({
                    "model": "local-test-double",
                    "data": [
                        {"index": 1, "embedding": [0.0, 1.0]},
                        {"index": 0, "embedding": [1.0, 0.0]},
                    ],
                    "usage": {"prompt_tokens": 4, "total_tokens": 4},
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        provider = HTTPEmbeddingProvider(
            endpoint=self.start_server(Handler), model="local-test-double",
            api_key="local-contract-key", dimensions=2,
        )
        self.assertEqual(provider.embed(("first", "second")), ((1.0, 0.0), (0.0, 1.0)))
        self.assertEqual(seen, [(
            "/v1/embeddings", "Bearer local-contract-key",
            {"model": "local-test-double", "input": ["first", "second"],
             "encoding_format": "float", "dimensions": 2},
        )])
        self.assertEqual(provider.prompt_tokens, 4)

    def test_redirect_never_forwards_document_or_credential(self):
        forwarded = []

        class Target(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                forwarded.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()

            do_POST = do_GET

        target = self.start_server(Target)

        class Redirect(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(302)
                self.send_header("Location", target)
                self.end_headers()

        provider = HTTPEmbeddingProvider(
            endpoint=self.start_server(Redirect), model="local-test-double",
            api_key="local-contract-key",
        )
        with self.assertRaisesRegex(EmbeddingError, "status 302"):
            provider.embed(("private fixture document",))
        self.assertEqual(forwarded, [])


if __name__ == "__main__":
    unittest.main()
