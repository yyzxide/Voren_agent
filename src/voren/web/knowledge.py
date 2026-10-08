"""Explicit, read-only HTTP access to citation-checked knowledge answers."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Literal, Self

from fastapi import APIRouter, HTTPException
from pydantic import Field, field_validator, model_validator

from voren.knowledge.answers import KnowledgeAnswerResult, KnowledgeAnswerService
from voren.knowledge.models import KnowledgeCorpusSnapshot, KnowledgeSearchHit
from voren.knowledge.store import SQLiteKnowledgeStore
from voren.providers.openai_responses import ModelConfigurationError, OpenAIResponsesModelAdapter
from voren.runtime.cancellation import CancellationToken
from voren.runtime.models import ModelMessage, ModelResponse, ToolDefinition
from voren.runtime.ports import ModelAdapter
from voren.testing.scripted_model import ScriptedModelAdapter
from voren.web.models import WebModel


def _check_corpus_bounds(corpus: KnowledgeCorpusSnapshot) -> None:
    if len(corpus.refs) > 1_000 or any(len(ref.document_id) > 500 for ref in corpus.refs):
        raise ValueError("knowledge corpus exceeds HTTP snapshot limits")


class KnowledgeSearchRequest(WebModel):
    question: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=20, strict=True)
    corpus: KnowledgeCorpusSnapshot | None = None

    @field_validator("corpus", mode="before")
    @classmethod
    def corpus_input_is_bounded(cls, value):
        if isinstance(value, dict):
            refs = value.get("refs")
            if isinstance(refs, (list, tuple)):
                if len(refs) > 1_000:
                    raise ValueError("knowledge corpus exceeds HTTP snapshot limits")
                for ref in refs:
                    if isinstance(ref, dict) and isinstance(ref.get("document_id"), str) and len(ref["document_id"]) > 500:
                        raise ValueError("knowledge document ID exceeds HTTP limits")
        return value

    @model_validator(mode="after")
    def question_is_nonblank(self) -> Self:
        if not self.question.strip():
            raise ValueError("question must not be blank")
        if self.corpus is not None:
            _check_corpus_bounds(self.corpus)
        return self


class KnowledgeAskRequest(KnowledgeSearchRequest):
    # Preserve the original JSON text: duplicate proposal keys must reach the
    # answer service unchanged, rather than disappear in an HTTP JSON decoder.
    draft: str | None = Field(default=None, min_length=1, max_length=64_000)
    allow_model_api: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def choose_one_proposal_source(self) -> Self:
        if (self.draft is not None) == self.allow_model_api:
            raise ValueError("choose either a draft or explicit model API access")
        if self.draft is not None and len(self.draft.encode("utf-8")) > 64_000:
            raise ValueError("answer draft exceeds the byte limit")
        return self


class KnowledgeSearchView(WebModel):
    question: str
    mode: Literal["bm25"] = "bm25"
    corpus: KnowledgeCorpusSnapshot
    hits: tuple[KnowledgeSearchHit, ...]


class KnowledgeAnswerView(KnowledgeAnswerResult):
    proposal_mode: Literal["operator_draft", "online_model"]


def _live_model() -> ModelAdapter:
    model = os.environ.get("VOREN_MODEL")
    if not model:
        raise ModelConfigurationError("knowledge answer model is not configured")
    return OpenAIResponsesModelAdapter.from_environment(
        model=model,
        base_url=os.environ.get("OPENAI_BASE_URL"),
        provider_profile=os.environ.get("VOREN_RESPONSES_PROFILE"),
    )


class _LazyModel:
    """Do not even configure an online provider until evidence warrants a call.

    The answer service sanitizes factory and provider exceptions into its
    bounded failure result. Empty retrieval never constructs this provider.
    """

    def __init__(self, factory: Callable[[], ModelAdapter]) -> None:
        self._factory = factory

    def complete(
        self, *, messages: tuple[ModelMessage, ...], tools: tuple[ToolDefinition, ...],
        cancellation: CancellationToken,
    ) -> ModelResponse:
        return self._factory().complete(
            messages=messages, tools=tools, cancellation=cancellation,
        )


def create_knowledge_router(
    *, database: Path, model_factory: Callable[[], ModelAdapter] | None = None,
) -> APIRouter:
    """BM25 only: no implicit embedding request, indexing, or write action."""
    router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

    @router.post("/search", response_model=KnowledgeSearchView)
    def search(payload: KnowledgeSearchRequest) -> KnowledgeSearchView:
        store = None
        try:
            store = SQLiteKnowledgeStore(database)
            corpus = payload.corpus if payload.corpus is not None else store.snapshot_corpus()
            _check_corpus_bounds(corpus)
            store.load_corpus(corpus)
            return KnowledgeSearchView(
                question=payload.question.strip(), corpus=corpus,
                hits=store.search(payload.question.strip(), limit=payload.limit, mode="bm25", corpus=corpus),
            )
        except Exception:
            # Do not return raw SQLite, path, provider, or source-content errors.
            raise HTTPException(status_code=503, detail="knowledge_evidence_unavailable") from None
        finally:
            if store is not None:
                store.close()

    @router.post("/ask", response_model=KnowledgeAnswerView)
    def ask(payload: KnowledgeAskRequest) -> KnowledgeAnswerView:
        store = None
        try:
            store = SQLiteKnowledgeStore(database)
            corpus = payload.corpus if payload.corpus is not None else store.snapshot_corpus()
            _check_corpus_bounds(corpus)
            if payload.draft is not None:
                model = ScriptedModelAdapter((ModelResponse(text=payload.draft),))
                proposal_mode = "operator_draft"
            else:
                model = _LazyModel(model_factory or _live_model)
                proposal_mode = "online_model"
            result = KnowledgeAnswerService(store, model, corpus=corpus).answer(
                payload.question.strip(), limit=payload.limit,
            )
            return KnowledgeAnswerView(**result.model_dump(), proposal_mode=proposal_mode)
        except Exception:
            raise HTTPException(status_code=503, detail="knowledge_answer_unavailable") from None
        finally:
            if store is not None:
                store.close()

    return router
