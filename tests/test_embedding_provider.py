from __future__ import annotations

import io
import json
import unittest
from unittest.mock import MagicMock, Mock, patch
from urllib.error import HTTPError

from voren.knowledge.embeddings import (
    EmbeddingConfigurationError,
    EmbeddingError,
    HTTPEmbeddingProvider,
    embedding_provider_from_env,
)


class EmbeddingProviderTest(unittest.TestCase):
    def provider(self, **kwargs) -> HTTPEmbeddingProvider:
        return HTTPEmbeddingProvider(
            endpoint="http://127.0.0.1:12345/v1/embeddings", model="test-model", **kwargs
        )

    def response(self, data, **kwargs):
        return {
            "model": "test-model",
            "data": data,
            "usage": {"prompt_tokens": 12},
            **kwargs,
        }

    def mocked_transport(self, payload):
        response = Mock()
        response.read.return_value = json.dumps(payload).encode()
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response
        return patch("voren.knowledge.embeddings.build_opener", return_value=opener), opener

    def test_indexes_restore_input_order_and_request_uses_float_encoding(self):
        transport, opener = self.mocked_transport(self.response([
            {"index": 1, "embedding": [0, 1]},
            {"index": 0, "embedding": [1, 0]},
        ]))
        provider = self.provider(api_key="private-test-key")
        with transport:
            vectors = provider.embed(("first", "second"))
        self.assertEqual(vectors, ((1.0, 0.0), (0.0, 1.0)))
        request = opener.open.call_args.args[0]
        self.assertEqual(json.loads(request.data), {
            "model": "test-model", "input": ["first", "second"], "encoding_format": "float",
        })
        self.assertEqual(request.get_header("Authorization"), "Bearer private-test-key")
        self.assertEqual(provider.prompt_tokens, 12)
        self.assertTrue(provider.usage_complete)
        self.assertNotIn("private-test-key", repr(provider))

    def test_bad_vectors_or_model_never_escape_contract(self):
        payloads = [
            self.response([{"index": 0, "embedding": [0, 0]}]),
            self.response([{"index": 0, "embedding": [float("nan"), 1]}]),
            self.response([{"index": 0, "embedding": [True, 1]}]),
            self.response([{"index": True, "embedding": [1, 0]}]),
            self.response([{"index": 1, "embedding": [1, 0]}]),
            self.response([{"index": 0, "embedding": [1, 0]}], model="other-model"),
            self.response([]),
            ["invalid-top-level"],
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                transport, _ = self.mocked_transport(payload)
                provider = self.provider()
                with transport, self.assertRaisesRegex(EmbeddingError, "vector contract"):
                    provider.embed(("query",))
                self.assertFalse(provider.usage_complete)

    def test_duplicate_indexes_and_inconsistent_dimensions_rejected(self):
        for values in (
            [{"index": 0, "embedding": [1, 0]}, {"index": 0, "embedding": [0, 1]}],
            [{"index": 0, "embedding": [1, 0]}, {"index": 1, "embedding": [0, 1, 0]}],
        ):
            transport, _ = self.mocked_transport(self.response(values))
            with transport, self.assertRaises(EmbeddingError):
                self.provider().embed(("a", "b"))

    def test_http_errors_do_not_log_provider_body_and_are_not_retried(self):
        error = HTTPError("https://service.invalid", 401, "secret provider text", {}, io.BytesIO(b"private"))
        opener = Mock()
        opener.open.side_effect = error
        provider = self.provider(max_requests=1)
        with patch("voren.knowledge.embeddings.build_opener", return_value=opener):
            with self.assertRaisesRegex(EmbeddingError, r"\(status 401\)") as caught:
                provider.embed(("private document",))
            self.assertNotIn("private", str(caught.exception))
            with self.assertRaisesRegex(EmbeddingError, "budget exhausted"):
                provider.embed(("private document",))
        self.assertEqual(opener.open.call_count, 1)
        self.assertFalse(provider.usage_complete)

    def test_missing_usage_is_not_reported_as_measured_zero(self):
        payload = self.response([{"index": 0, "embedding": [1, 0]}])
        del payload["usage"]
        transport, _ = self.mocked_transport(payload)
        provider = self.provider()
        with transport:
            provider.embed(("query",))
        self.assertFalse(provider.usage_complete)
        self.assertEqual(provider.prompt_tokens, 0)

    def test_endpoint_requires_safe_transport_and_never_embeds_secrets_in_url(self):
        for endpoint in (
            "http://example.com/v1/embeddings",
            "https://user:key@example.com/v1/embeddings",
            "https://example.com/v1/embeddings?key=secret",
            "https://example.com/v1/embeddings#secret",
            "https://example.com/v1/responses",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(EmbeddingConfigurationError):
                HTTPEmbeddingProvider(endpoint=endpoint, model="test", api_key="key")
        with self.assertRaisesRegex(EmbeddingConfigurationError, "API_KEY"):
            HTTPEmbeddingProvider(endpoint="https://example.com/v1/embeddings", model="test")

    def test_fingerprint_changes_model_dimensions_endpoint_but_not_credentials(self):
        first = self.provider(api_key="first")
        rotated = self.provider(api_key="second", timeout_seconds=20)
        self.assertEqual(first.fingerprint, rotated.fingerprint)
        self.assertNotEqual(first.fingerprint, self.provider(dimensions=2).fingerprint)
        self.assertNotEqual(first.fingerprint, HTTPEmbeddingProvider(
            endpoint="http://localhost:12345/v1/embeddings", model="other",
        ).fingerprint)

    def test_env_is_explicit_and_input_limits_reject_before_dispatch(self):
        with self.assertRaises(EmbeddingConfigurationError):
            embedding_provider_from_env({"OPENAI_API_KEY": "unrelated-chat-key"})
        provider = self.provider()
        for texts in (("",), tuple("x" for _ in range(33)), ("x" * 5001,)):
            with self.assertRaises(EmbeddingError):
                provider.embed(texts)
        self.assertEqual(provider.requests_attempted, 0)
        self.assertEqual(provider.embed(()), ())


if __name__ == "__main__":
    unittest.main()
