"""Explicit, bounded access to an OpenAI-compatible embeddings endpoint.

No default endpoint/model and no key discovery from chat-model settings: users
must configure this independent data boundary before enabling dense retrieval.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class EmbeddingError(RuntimeError):
    """Embedding failure whose message never includes provider text or secrets."""


class EmbeddingConfigurationError(EmbeddingError, ValueError):
    pass


class EmbeddingProvider(Protocol):
    @property
    def fingerprint(self) -> str: ...

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a credential or document body to a redirect target.
        return None


@dataclass(slots=True)
class HTTPEmbeddingProvider:
    endpoint: str = field(repr=False)
    model: str
    api_key: str | None = field(default=None, repr=False)
    dimensions: int | None = None
    timeout_seconds: float = 10.0
    max_requests: int = 128
    requests_attempted: int = field(default=0, init=False)
    prompt_tokens: int = field(default=0, init=False)
    usage_complete: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        try:
            url = urlsplit(self.endpoint)
            valid_port = url.port
            del valid_port
            hostname = url.hostname or ""
            loopback = hostname == "localhost"
            if not loopback:
                try:
                    loopback = ipaddress.ip_address(hostname).is_loopback
                except ValueError:
                    pass
        except ValueError:
            raise EmbeddingConfigurationError("invalid embedding endpoint") from None
        if (
            not hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or not url.path.rstrip("/").endswith("/embeddings")
            or any(char.isspace() for char in self.endpoint)
            or (url.scheme != "https" and not (url.scheme == "http" and loopback))
        ):
            raise EmbeddingConfigurationError(
                "embedding endpoint must use HTTPS (or loopback HTTP), end in "
                "/embeddings, and contain no credentials, query, or fragment"
            )
        self.endpoint = self.endpoint.rstrip("/")
        self.model = self.model.strip()
        if not self.model or len(self.model) > 200:
            raise EmbeddingConfigurationError("set a non-empty embedding model")
        if self.api_key is not None:
            self.api_key = self.api_key.strip()
            if not self.api_key or any(char.isspace() for char in self.api_key):
                raise EmbeddingConfigurationError("invalid embedding API key")
        if not loopback and self.api_key is None:
            raise EmbeddingConfigurationError("set VOREN_EMBEDDING_API_KEY")
        if self.dimensions is not None and (
            type(self.dimensions) is not int or not 1 <= self.dimensions <= 16_384
        ):
            raise EmbeddingConfigurationError("embedding dimensions must be 1..16384")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise EmbeddingConfigurationError("embedding timeout must be finite and positive")
        if type(self.max_requests) is not int or self.max_requests < 1:
            raise EmbeddingConfigurationError("embedding request budget must be positive")

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            {
                "contract": "openai-compatible-embeddings-float-v1",
                "endpoint": self.endpoint,
                "model": self.model,
                "dimensions": self.dimensions,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        if not texts:
            return ()
        if len(texts) > 32 or any(
            not isinstance(text, str) or not text.strip() or len(text) > 5_000
            for text in texts
        ):
            raise EmbeddingError("embedding batch must contain 1..32 non-empty bounded texts")
        if self.requests_attempted >= self.max_requests:
            raise EmbeddingError("embedding request budget exhausted")
        body: dict = {"model": self.model, "input": list(texts), "encoding_format": "float"}
        if self.dimensions is not None:
            body["dimensions"] = self.dimensions
        headers = {"Content-Type": "application/json"}
        if self.api_key is not None:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(
            self.endpoint,
            data=json.dumps(body, ensure_ascii=False).encode(),
            headers=headers,
            method="POST",
        )
        self.requests_attempted += 1
        # No automatic retry: a timed-out attempt may already have been billed.
        try:
            with build_opener(_NoRedirect()).open(request, timeout=self.timeout_seconds) as response:
                raw = response.read(8_388_609)
                if len(raw) > 8_388_608:
                    raise EmbeddingError("embedding response exceeds size limit")
            result = json.loads(raw)
        except HTTPError as error:
            self.usage_complete = False
            raise EmbeddingError(f"embedding HTTP request failed (status {error.code})") from None
        except (URLError, OSError, TimeoutError):
            self.usage_complete = False
            raise EmbeddingError("embedding transport failed") from None
        except (ValueError, UnicodeError):
            self.usage_complete = False
            raise EmbeddingError("embedding response is not valid JSON") from None
        except EmbeddingError:
            self.usage_complete = False
            raise
        try:
            vectors = self._parse_vectors(result, len(texts))
        except (ValueError, TypeError, KeyError, OverflowError):
            self.usage_complete = False
            raise EmbeddingError("embedding response violates vector contract") from None
        usage = result.get("usage")
        tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        if type(tokens) is int and tokens >= 0:
            self.prompt_tokens += tokens
        else:
            self.usage_complete = False
        return vectors

    def _parse_vectors(self, result: dict, expected: int) -> tuple[tuple[float, ...], ...]:
        if not isinstance(result, dict) or result.get("model") != self.model:
            raise ValueError("model mismatch")
        items = result["data"]
        if not isinstance(items, list) or len(items) != expected:
            raise ValueError("batch mismatch")
        by_index: dict[int, tuple[float, ...]] = {}
        dimension = self.dimensions
        for item in items:
            index = item["index"]
            values = item["embedding"]
            if type(index) is not int or not 0 <= index < expected or index in by_index:
                raise ValueError("invalid result index")
            if not isinstance(values, list) or not 1 <= len(values) <= 16_384:
                raise ValueError("invalid dimension")
            if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
                raise ValueError("invalid numeric value")
            vector = tuple(float(value) for value in values)
            norm = math.hypot(*vector)
            if not math.isfinite(norm) or norm <= 0:
                raise ValueError("invalid vector norm")
            if dimension is None:
                dimension = len(vector)
            if len(vector) != dimension:
                raise ValueError("dimension mismatch")
            by_index[index] = vector
        return tuple(by_index[index] for index in range(expected))


def embedding_provider_from_env(
    environ: Mapping[str, str] | None = None,
) -> HTTPEmbeddingProvider:
    settings = os.environ if environ is None else environ
    endpoint = settings.get("VOREN_EMBEDDING_ENDPOINT", "")
    model = settings.get("VOREN_EMBEDDING_MODEL", "")
    if not endpoint or not model:
        raise EmbeddingConfigurationError(
            "set VOREN_EMBEDDING_ENDPOINT and VOREN_EMBEDDING_MODEL explicitly"
        )
    try:
        dimensions = settings.get("VOREN_EMBEDDING_DIMENSIONS")
        budget = int(settings.get("VOREN_EMBEDDING_MAX_REQUESTS", "128"))
        timeout = float(settings.get("VOREN_EMBEDDING_TIMEOUT_SECONDS", "10"))
        return HTTPEmbeddingProvider(
            endpoint=endpoint,
            model=model,
            api_key=settings.get("VOREN_EMBEDDING_API_KEY"),
            dimensions=int(dimensions) if dimensions else None,
            max_requests=budget,
            timeout_seconds=timeout,
        )
    except EmbeddingConfigurationError:
        raise
    except (ValueError, OverflowError):
        raise EmbeddingConfigurationError("invalid embedding numeric setting") from None


def retrieval_mode_from_env(environ: Mapping[str, str] | None = None) -> str:
    settings = os.environ if environ is None else environ
    mode = settings.get("VOREN_KNOWLEDGE_RETRIEVAL_MODE", "bm25")
    if mode not in {"lexical", "bm25", "dense", "hybrid"}:
        raise EmbeddingConfigurationError("unknown knowledge retrieval mode")
    return mode
