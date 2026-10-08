"""Validate retrieval citations against the actual immutable source document.

These checks establish source identity and exact quoted text, not whether a
generated claim follows from that text or answers the user's question.
"""

from __future__ import annotations

import hashlib
import json

from voren.knowledge.models import KnowledgeDocument, KnowledgeSearchHit


class CitationValidationError(ValueError):
    """Source-independent, bounded error labels suitable for tool results."""

    def __init__(self, errors: tuple[str, ...]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def validate_knowledge_hit(
    hit: KnowledgeSearchHit, document: KnowledgeDocument,
) -> None:
    """Reject a citation whose version, metadata or text differs from its source."""
    errors: list[str] = []
    if hit.ref != document.ref:
        errors.append("source version differs from the bound source version")
    if hit.content_digest != document.content_digest:
        errors.append("source content digest differs")
    if (hit.title, hit.source_uri, hit.source_kind) != (
        document.title, document.source_uri, document.source_kind,
    ):
        errors.append("source metadata differs")
    start, end = hit.chunk_start, hit.chunk_end
    if any(value is not None for value in (
        hit.chunk_id, start, end, hit.chunk_digest,
    )):
        if (
            not isinstance(start, int) or isinstance(start, bool)
            or not isinstance(end, int) or isinstance(end, bool)
            or not 0 <= start < end <= len(document.content)
        ):
            errors.append("chunk offsets are outside the source")
        else:
            source_text = document.content[start:end]
            source_digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
            if hit.snippet != source_text:
                errors.append("snippet differs from exact source offsets")
            if hit.chunk_digest != source_digest:
                errors.append("chunk content digest differs")
            identity = json.dumps(
                {
                    "version_id": document.ref.version_id,
                    "start": start,
                    "end": end,
                    "content_digest": source_digest,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            if hit.chunk_id != hashlib.sha256(identity.encode("utf-8")).hexdigest():
                errors.append("chunk identity differs from exact source metadata")
    else:
        if hit.retrieval_method != "lexical":
            errors.append("chunk retrieval requires exact chunk metadata")
        source_snippet = hit.snippet.strip("…").strip()
        if not source_snippet or source_snippet not in document.content:
            errors.append("snippet has no actual text from source content")
    if errors:
        raise CitationValidationError(tuple(errors))
