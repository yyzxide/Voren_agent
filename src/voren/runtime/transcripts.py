"""Encrypted, integrity-checked checkpoints for resumable agent transcripts."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Self

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel, ConfigDict, Field, model_validator

from voren.actions.errors import InvalidOperationStateError
from voren.runtime.models import ModelMessage, RuntimeUsage


TRANSCRIPT_SCHEMA_VERSION = "voren.transcript.v1"


class TranscriptKeyError(ValueError):
    """Raised when transcript key material is missing or malformed."""


class TranscriptIntegrityError(InvalidOperationStateError):
    """Raised when an encrypted checkpoint cannot be authenticated."""


class TranscriptCheckpoint(BaseModel):
    """Complete loop state at a safe model-request boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = TRANSCRIPT_SCHEMA_VERSION
    run_id: str = Field(min_length=1)
    config_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    next_model_step: int = Field(ge=1)
    messages: tuple[ModelMessage, ...] = Field(min_length=2)
    tool_call_count: int = Field(ge=0)
    signature_counts: dict[str, int] = Field(default_factory=dict)
    seen_call_ids: tuple[str, ...] = ()
    observation_digests: tuple[str, ...] = ()
    usage: RuntimeUsage = Field(default_factory=RuntimeUsage)

    @model_validator(mode="after")
    def state_is_consistent(self) -> Self:
        if self.schema_version != TRANSCRIPT_SCHEMA_VERSION:
            raise ValueError("unsupported transcript checkpoint schema")
        if self.next_model_step != self.usage.model_requests + 1:
            raise ValueError(
                "next_model_step must follow the persisted model request count"
            )
        if self.tool_call_count != len(self.seen_call_ids):
            raise ValueError("tool_call_count must match unique seen call IDs")
        if len(self.seen_call_ids) != len(set(self.seen_call_ids)):
            raise ValueError("seen_call_ids must be unique")
        for digest, count in self.signature_counts.items():
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise ValueError("signature count keys must be SHA-256 digests")
            if count < 1:
                raise ValueError("signature counts must be positive")
        for digest in self.observation_digests:
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise ValueError("observation digests must be SHA-256 values")
        return self

    def calculated_digest(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def restored_signature_counts(self) -> Counter[str]:
        return Counter(self.signature_counts)


class SQLiteTranscriptStore:
    """Store AES-256-GCM ciphertext while keeping key material outside SQLite."""

    def __init__(self, path: str | Path, *, key: bytes) -> None:
        if len(key) != 32:
            raise TranscriptKeyError("transcript key must decode to exactly 32 bytes")
        self._cipher = AESGCM(key)
        self._key_id = hashlib.sha256(key).hexdigest()[:16]
        self._connection = sqlite3.connect(str(path))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS run_transcripts (
                run_id TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL,
                config_digest TEXT NOT NULL,
                key_id TEXT NOT NULL,
                nonce BLOB NOT NULL,
                ciphertext BLOB NOT NULL,
                checkpoint_digest TEXT NOT NULL,
                revision INTEGER NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(run_id)
            )
            """
        )
        self._connection.commit()

    @classmethod
    def from_base64_key(
        cls, path: str | Path, *, encoded_key: str
    ) -> SQLiteTranscriptStore:
        try:
            key = base64.urlsafe_b64decode(encoded_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as error:
            raise TranscriptKeyError(
                "VOREN_TRANSCRIPT_KEY must be URL-safe base64"
            ) from error
        return cls(path, key=key)

    @staticmethod
    def generate_key() -> str:
        """Return URL-safe base64 key material suitable for local setup."""

        return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")

    @property
    def key_id(self) -> str:
        return self._key_id

    def close(self) -> None:
        self._connection.close()

    def save(self, checkpoint: TranscriptCheckpoint) -> None:
        plaintext = checkpoint.model_dump_json().encode("utf-8")
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(
            nonce,
            plaintext,
            self._associated_data(
                checkpoint.run_id,
                checkpoint.config_digest,
                checkpoint.schema_version,
            ),
        )
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO run_transcripts (
                    run_id, schema_version, config_digest, key_id, nonce,
                    ciphertext, checkpoint_digest, revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(run_id) DO UPDATE SET
                    schema_version = excluded.schema_version,
                    config_digest = excluded.config_digest,
                    key_id = excluded.key_id,
                    nonce = excluded.nonce,
                    ciphertext = excluded.ciphertext,
                    checkpoint_digest = excluded.checkpoint_digest,
                    revision = run_transcripts.revision + 1
                """,
                (
                    checkpoint.run_id,
                    checkpoint.schema_version,
                    checkpoint.config_digest,
                    self._key_id,
                    nonce,
                    ciphertext,
                    checkpoint.calculated_digest(),
                ),
            )

    def load(self, run_id: str) -> TranscriptCheckpoint:
        row = self._connection.execute(
            "SELECT * FROM run_transcripts WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise InvalidOperationStateError(
                f"run {run_id!r} has no recoverable transcript checkpoint"
            )
        if row["key_id"] != self._key_id:
            raise TranscriptIntegrityError(
                f"run {run_id!r} transcript was encrypted with another key"
            )
        try:
            plaintext = self._cipher.decrypt(
                row["nonce"],
                row["ciphertext"],
                self._associated_data(
                    run_id, row["config_digest"], row["schema_version"]
                ),
            )
        except InvalidTag as error:
            raise TranscriptIntegrityError(
                f"run {run_id!r} transcript authentication failed"
            ) from error
        try:
            checkpoint = TranscriptCheckpoint.model_validate_json(plaintext)
        except ValueError as error:
            raise TranscriptIntegrityError(
                f"run {run_id!r} transcript payload is invalid"
            ) from error
        if (
            checkpoint.run_id != run_id
            or checkpoint.config_digest != row["config_digest"]
            or checkpoint.schema_version != row["schema_version"]
            or checkpoint.calculated_digest() != row["checkpoint_digest"]
        ):
            raise TranscriptIntegrityError(
                f"run {run_id!r} transcript metadata disagrees with its payload"
            )
        return checkpoint

    def delete(self, run_id: str) -> None:
        with self._connection:
            self._connection.execute(
                "DELETE FROM run_transcripts WHERE run_id = ?", (run_id,)
            )

    def contains(self, run_id: str) -> bool:
        return (
            self._connection.execute(
                "SELECT 1 FROM run_transcripts WHERE run_id = ?", (run_id,)
            ).fetchone()
            is not None
        )

    @staticmethod
    def _associated_data(
        run_id: str, config_digest: str, schema_version: str
    ) -> bytes:
        return f"{schema_version}\n{run_id}\n{config_digest}".encode("utf-8")
