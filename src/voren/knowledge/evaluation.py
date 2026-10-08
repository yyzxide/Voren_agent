"""Local, reference-based retrieval evaluation without a model judge.

The bundled corpus is deliberately small and synthetic. Its results check the
retrieval and citation contracts; they are not independent semantic validation.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median
from time import perf_counter_ns
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from voren.knowledge.models import KnowledgeDocument, KnowledgeSearchHit, KnowledgeSourceKind
from voren.knowledge.retrieval import CHUNK_OVERLAP, CHUNK_SIZE, RRF_K
from voren.knowledge.store import SQLiteKnowledgeStore


RetrievalMode = Literal["lexical", "bm25", "dense", "hybrid"]
_FIXTURE_TIME = datetime(2026, 10, 8, tzinfo=UTC)
_LIMITATIONS = (
    "The bundled default is a small controlled synthetic bilingual corpus authored with this feature; custom datasets need independent source and label review.",
    "No independent semantic-quality validation or model judge.",
    "Recall and MRR measure document retrieval; evidence hits require exact labelled text in snippets.",
    "Latency measures this local run only, including database reads; it is not a general performance claim.",
    "Dense retrieval is not a calibrated answerability detector; unrelated positive-cosine hits count as no-answer false positives.",
)


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FixtureDocument(FrozenModel):
    document_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]*$")
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    active: bool = True
    source_kind: KnowledgeSourceKind = KnowledgeSourceKind.MEETING_NOTE

    def as_document(self) -> KnowledgeDocument:
        return KnowledgeDocument.create(
            document_id=self.document_id,
            title=self.title,
            content=self.content,
            source_uri=f"fixture://knowledge-retrieval/{self.document_id}",
            source_kind=self.source_kind,
            created_at=_FIXTURE_TIME,
        )


class ExpectedEvidence(FrozenModel):
    document_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class RetrievalCase(FrozenModel):
    case_id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    query: str = Field(min_length=1, max_length=500)
    expected_document_ids: tuple[str, ...]
    expected_evidence: tuple[ExpectedEvidence, ...] = ()

    @model_validator(mode="after")
    def labels_are_unambiguous(self) -> RetrievalCase:
        if not self.query.strip():
            raise ValueError("retrieval case query must be non-empty")
        if len(set(self.expected_document_ids)) != len(self.expected_document_ids):
            raise ValueError("expected document IDs must be unique")
        for evidence in self.expected_evidence:
            if evidence.document_id not in self.expected_document_ids:
                raise ValueError("evidence must belong to an expected document")
        return self


class RetrievalDataset(FrozenModel):
    schema_version: Literal["voren-retrieval-dataset/v1"]
    dataset_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    synthetic: bool
    documents: tuple[FixtureDocument, ...] = Field(min_length=1)
    cases: tuple[RetrievalCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def active_documents_and_labels_match(self) -> RetrievalDataset:
        active: dict[str, KnowledgeDocument] = {}
        versions: set[tuple[str, str]] = set()
        for fixture in self.documents:
            document = fixture.as_document()
            version = (document.ref.document_id, document.ref.version_id)
            if version in versions:
                raise ValueError("fixture document versions must be unique")
            versions.add(version)
            if fixture.active:
                if fixture.document_id in active:
                    raise ValueError("each fixture document may have only one active version")
                active[fixture.document_id] = document
        case_ids = tuple(case.case_id for case in self.cases)
        if not active:
            raise ValueError("retrieval fixture must contain an active document")
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("retrieval case IDs must be unique")
        for case in self.cases:
            if not set(case.expected_document_ids).issubset(active):
                raise ValueError("expected documents must be active fixture versions")
            for evidence in case.expected_evidence:
                if evidence.text not in active[evidence.document_id].content:
                    raise ValueError("expected evidence must occur in the active source content")
        return self

    @property
    def dataset_digest(self) -> str:
        return _digest(self.model_dump(mode="json"))


class RankedResult(FrozenModel):
    rank: int = Field(ge=1)
    hit: KnowledgeSearchHit
    citation_valid: bool
    citation_errors: tuple[str, ...] = ()


class RetrievalCaseResult(FrozenModel):
    case_id: str
    category: str
    query: str
    expected_document_ids: tuple[str, ...]
    expected_evidence: tuple[ExpectedEvidence, ...]
    results: tuple[RankedResult, ...]
    document_recall_at_k: float | None = Field(default=None, ge=0, le=1)
    reciprocal_rank: float | None = Field(default=None, ge=0, le=1)
    evidence_hit: bool | None = None
    no_answer_false_positive: bool | None = None
    latency_ms: float = Field(ge=0)


class RetrievalSummary(FrozenModel):
    case_count: int = Field(ge=1)
    answer_case_count: int = Field(ge=0)
    evidence_case_count: int = Field(ge=0)
    no_answer_case_count: int = Field(ge=0)
    recall_at_k: float | None = Field(default=None, ge=0, le=1)
    mrr: float | None = Field(default=None, ge=0, le=1)
    evidence_hit_rate: float | None = Field(default=None, ge=0, le=1)
    no_answer_false_positive_rate: float | None = Field(default=None, ge=0, le=1)
    result_count: int = Field(ge=0)
    valid_citation_count: int = Field(ge=0)
    citation_version_correctness: float | None = Field(default=None, ge=0, le=1)
    latency_median_ms: float = Field(ge=0)
    latency_max_ms: float = Field(ge=0)


class RetrievalModeEvaluation(FrozenModel):
    mode: RetrievalMode
    summary: RetrievalSummary
    cases: tuple[RetrievalCaseResult, ...]


class EmbeddingStageUsage(FrozenModel):
    requests_attempted: int = Field(ge=0)
    prompt_tokens: int = Field(ge=0)
    usage_complete: bool


class RetrievalEvaluationArtifact(FrozenModel):
    schema_version: Literal["voren-retrieval-evaluation/v1"] = "voren-retrieval-evaluation/v1"
    created_at: datetime
    dataset_id: str
    dataset_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_file_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    synthetic: bool
    document_version_count: int = Field(ge=1)
    active_document_count: int = Field(ge=1)
    case_count: int = Field(ge=1)
    code_revision: str
    code_dirty: bool
    k: int = Field(ge=1, le=20)
    embedding_fingerprint: str | None = None
    embedding_usage: dict[str, EmbeddingStageUsage] | None = None
    retrieval_configuration: dict[str, Any]
    metric_definitions: dict[str, str]
    limitations: tuple[str, ...]
    evaluations: tuple[RetrievalModeEvaluation, ...]
    ranking_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    def calculated_digest(self) -> str:
        return _digest(self.model_dump(mode="json", exclude={"artifact_digest"}))

    def assert_integrity(self) -> None:
        if self.artifact_digest != self.calculated_digest():
            raise ValueError("retrieval artifact digest does not match its content")
        if self.ranking_digest != _ranking_digest(self.evaluations):
            raise ValueError("retrieval ranking digest does not match its results")
        modes = tuple(evaluation.mode for evaluation in self.evaluations)
        if not modes or len(modes) != len(set(modes)):
            raise ValueError("retrieval artifact modes must be non-empty and unique")
        first_cases = self.evaluations[0].cases
        case_ids = tuple(case.case_id for case in first_cases)
        if len(case_ids) != self.case_count or len(case_ids) != len(set(case_ids)):
            raise ValueError("retrieval artifact case counts or IDs do not match")
        labels = tuple(_case_labels(case) for case in first_cases)
        for evaluation in self.evaluations:
            if tuple(_case_labels(case) for case in evaluation.cases) != labels:
                raise ValueError("retrieval modes must evaluate the same ordered case labels")
            for case in evaluation.cases:
                if len(case.results) > self.k or tuple(result.rank for result in case.results) != tuple(range(1, len(case.results) + 1)):
                    raise ValueError("retrieval result ranks do not match the configured k")
                if any(result.hit.retrieval_method != evaluation.mode for result in case.results):
                    raise ValueError("retrieval result method differs from its evaluation mode")
                for field, expected in _case_scores(case.expected_document_ids, case.expected_evidence, case.results).items():
                    if getattr(case, field) != expected:
                        raise ValueError("retrieval case metrics do not match ranked results")
            if evaluation.summary != summarize_cases(evaluation.cases):
                raise ValueError("retrieval summary does not match case results")


def load_retrieval_dataset(path: Path) -> RetrievalDataset:
    return RetrievalDataset.model_validate_json(path.read_text(encoding="utf-8"))


def measure_case(
    case: RetrievalCase,
    hits: Sequence[KnowledgeSearchHit],
    *,
    active_documents: dict[str, KnowledgeDocument],
    k: int,
    latency_ms: float,
) -> RetrievalCaseResult:
    """Score source citations and exact evidence separately from document recall."""

    ranked = tuple(
        RankedResult(
            rank=rank,
            hit=hit,
            citation_valid=not (errors := _citation_errors(hit, active_documents)),
            citation_errors=errors,
        )
        for rank, hit in enumerate(hits[:k], 1)
    )
    return RetrievalCaseResult(
        case_id=case.case_id,
        category=case.category,
        query=case.query,
        expected_document_ids=case.expected_document_ids,
        expected_evidence=case.expected_evidence,
        results=ranked,
        latency_ms=latency_ms,
        **_case_scores(case.expected_document_ids, case.expected_evidence, ranked),
    )


def summarize_cases(cases: Sequence[RetrievalCaseResult]) -> RetrievalSummary:
    if not cases:
        raise ValueError("retrieval evaluation requires cases")
    answer = tuple(case for case in cases if case.expected_document_ids)
    evidence = tuple(case for case in cases if case.evidence_hit is not None)
    no_answer = tuple(case for case in cases if case.no_answer_false_positive is not None)
    results = tuple(result for case in cases for result in case.results)
    valid_citations = sum(result.citation_valid for result in results)
    return RetrievalSummary(
        case_count=len(cases),
        answer_case_count=len(answer),
        evidence_case_count=len(evidence),
        no_answer_case_count=len(no_answer),
        recall_at_k=mean(case.document_recall_at_k for case in answer) if answer else None,
        mrr=mean(case.reciprocal_rank for case in answer) if answer else None,
        evidence_hit_rate=mean(case.evidence_hit for case in evidence) if evidence else None,
        no_answer_false_positive_rate=mean(case.no_answer_false_positive for case in no_answer) if no_answer else None,
        result_count=len(results),
        valid_citation_count=valid_citations,
        citation_version_correctness=valid_citations / len(results) if results else None,
        latency_median_ms=median(case.latency_ms for case in cases),
        latency_max_ms=max(case.latency_ms for case in cases),
    )


def evaluate_retrieval(
    dataset: RetrievalDataset,
    *,
    modes: Sequence[RetrievalMode] = ("lexical", "bm25"),
    k: int = 5,
    embedder: Any = None,
    code_revision: str = "unavailable",
    code_dirty: bool = False,
    dataset_file_digest: str | None = None,
) -> RetrievalEvaluationArtifact:
    """Evaluate every mode on one isolated fixture database.

    No provider is inferred from the environment. Dense/hybrid evaluation needs
    an explicitly supplied embedder; only callers decide to use an external API.
    """

    if not 1 <= k <= 20:
        raise ValueError("retrieval evaluation k must be between 1 and 20")
    modes = tuple(modes)
    if not modes or len(modes) != len(set(modes)):
        raise ValueError("retrieval evaluation modes must be non-empty and unique")
    if any(mode not in {"lexical", "bm25", "dense", "hybrid"} for mode in modes):
        raise ValueError("unsupported retrieval evaluation mode")
    uses_embeddings = bool({"dense", "hybrid"}.intersection(modes))
    if uses_embeddings and embedder is None:
        raise ValueError("dense/hybrid evaluation requires an explicit embedding provider")
    evaluations: list[RetrievalModeEvaluation] = []
    embedding_usage: dict[str, EmbeddingStageUsage] = {}
    active: dict[str, KnowledgeDocument] = {}
    with tempfile.TemporaryDirectory(prefix="voren-retrieval-eval-") as temporary:
        store = SQLiteKnowledgeStore(Path(temporary) / "fixture.sqlite3")
        try:
            for fixture in dataset.documents:
                document = fixture.as_document()
                store.install(document)
                if fixture.active:
                    store.activate(document.ref, reason="controlled retrieval evaluation fixture", activated_at=_FIXTURE_TIME)
                    active[fixture.document_id] = document
            if uses_embeddings:
                before = _embedding_usage_snapshot(embedder)
                store.index_embeddings(embedder)
                _record_embedding_usage(embedding_usage, "corpus_indexing", before, embedder)
            for mode in modes:
                before = _embedding_usage_snapshot(embedder) if mode in {"dense", "hybrid"} else None
                case_results: list[RetrievalCaseResult] = []
                for case in dataset.cases:
                    started = perf_counter_ns()
                    hits = store.search(case.query, limit=k, mode=mode, embedder=embedder)
                    latency_ms = (perf_counter_ns() - started) / 1_000_000
                    case_results.append(measure_case(case, hits, active_documents=active, k=k, latency_ms=latency_ms))
                evaluations.append(RetrievalModeEvaluation(mode=mode, summary=summarize_cases(case_results), cases=tuple(case_results)))
                if mode in {"dense", "hybrid"}:
                    _record_embedding_usage(embedding_usage, f"queries:{mode}", before, embedder)
        finally:
            store.close()
    artifact = RetrievalEvaluationArtifact(
        created_at=datetime.now(UTC),
        dataset_id=dataset.dataset_id,
        dataset_digest=dataset.dataset_digest,
        dataset_file_digest=dataset_file_digest,
        synthetic=dataset.synthetic,
        document_version_count=len(dataset.documents),
        active_document_count=len(active),
        case_count=len(dataset.cases),
        code_revision=code_revision,
        code_dirty=code_dirty,
        k=k,
        embedding_fingerprint=embedder.fingerprint if uses_embeddings else None,
        embedding_usage=embedding_usage or None,
        retrieval_configuration={
            "chunk_size_unicode_characters": CHUNK_SIZE,
            "chunk_overlap_unicode_characters": CHUNK_OVERLAP,
            "rrf_k": RRF_K,
            "document_deduplication": True,
            "ranking_scores_are_confidence": False,
            "algorithm_parameters_source": "recorded source revision; see knowledge/retrieval.py",
        },
        metric_definitions={
            "recall_at_k": "Mean fraction of labelled document IDs retrieved in the first k hits; duplicate chunks count once; only valid active-source citations count.",
            "mrr": "Mean reciprocal rank of the first relevant, valid-citation hit across answerable cases; missing answers contribute zero.",
            "evidence_hit_rate": "Fraction of evidence-labelled cases for which every exact labelled quotation appears in a valid, relevant snippet at k.",
            "no_answer_false_positive_rate": "Fraction of no-answer cases returning any hit at k; excluded from answerable-case recall and MRR.",
            "citation_version_correctness": "Fraction of hits matching an active fixture version, digest, title, URI and (if supplied) exact source offsets and chunk digest.",
            "latency_ms": "Search wall-clock duration for this local run; excludes corpus installation and embedding indexing, includes query embedding when used.",
        },
        limitations=_LIMITATIONS,
        evaluations=tuple(evaluations),
        ranking_digest=_ranking_digest(evaluations),
        artifact_digest="0" * 64,
    )
    artifact = artifact.model_copy(update={"artifact_digest": artifact.calculated_digest()})
    artifact.assert_integrity()
    return artifact


def write_retrieval_evaluation(path: Path, artifact: RetrievalEvaluationArtifact) -> None:
    artifact.assert_integrity()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as temporary:
            temporary_name = temporary.name
            temporary.write(artifact.model_dump_json(indent=2) + "\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def read_retrieval_evaluation(path: Path) -> RetrievalEvaluationArtifact:
    artifact = RetrievalEvaluationArtifact.model_validate_json(path.read_text(encoding="utf-8"))
    artifact.assert_integrity()
    return artifact


def _citation_errors(hit: KnowledgeSearchHit, active: dict[str, KnowledgeDocument]) -> tuple[str, ...]:
    document = active.get(hit.ref.document_id)
    if document is None:
        return ("document is not active in the evaluated corpus",)
    errors: list[str] = []
    if hit.ref != document.ref:
        errors.append("source version differs from the active fixture version")
    if hit.content_digest != document.content_digest:
        errors.append("source content digest differs")
    if (hit.title, hit.source_uri, hit.source_kind) != (document.title, document.source_uri, document.source_kind):
        errors.append("source metadata differs")
    start, end = getattr(hit, "chunk_start", None), getattr(hit, "chunk_end", None)
    if start is not None or end is not None:
        if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(document.content):
            errors.append("chunk offsets are outside the source")
        else:
            source_text = document.content[start:end]
            if hit.snippet != source_text:
                errors.append("snippet differs from exact source offsets")
            if getattr(hit, "chunk_digest", None) != hashlib.sha256(source_text.encode("utf-8")).hexdigest():
                errors.append("chunk content digest differs")
            identity = {
                "version_id": document.ref.version_id,
                "start": start,
                "end": end,
                "content_digest": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
            }
            if getattr(hit, "chunk_id", None) != _digest(identity):
                errors.append("chunk identity differs from exact source metadata")
    else:
        source_snippet = hit.snippet.strip("…").strip()
        if not source_snippet or source_snippet not in document.content:
            errors.append("snippet has no actual text from source content")
    return tuple(errors)


def _case_scores(
    document_ids: Sequence[str],
    evidence: Sequence[ExpectedEvidence],
    ranked: Sequence[RankedResult],
) -> dict[str, float | bool | None]:
    expected = set(document_ids)
    valid = tuple(result for result in ranked if result.citation_valid)
    retrieved = {result.hit.ref.document_id for result in valid}
    first_rank = next((result.rank for result in valid if result.hit.ref.document_id in expected), None)
    return {
        "document_recall_at_k": len(expected & retrieved) / len(expected) if expected else None,
        "reciprocal_rank": (1 / first_rank if first_rank else 0) if expected else None,
        "evidence_hit": (
            all(any(result.hit.ref.document_id == quotation.document_id and quotation.text in result.hit.snippet for result in valid) for quotation in evidence)
            if evidence else None
        ),
        "no_answer_false_positive": bool(ranked) if not expected else None,
    }


def _case_labels(case: RetrievalCaseResult) -> dict[str, Any]:
    return case.model_dump(mode="json", include={"case_id", "category", "query", "expected_document_ids", "expected_evidence"})


def _ranking_digest(evaluations: Sequence[RetrievalModeEvaluation]) -> str:
    return _digest([
        {"mode": evaluation.mode, "cases": [
            {"case_id": case.case_id, "ranked_sources": [
                {"rank": result.rank, "document_id": result.hit.ref.document_id, "version_id": result.hit.ref.version_id, "chunk_id": getattr(result.hit, "chunk_id", None)}
                for result in case.results
            ]} for case in evaluation.cases
        ]} for evaluation in evaluations
    ])


def _embedding_usage_snapshot(embedder: Any) -> tuple[int, int, bool] | None:
    values = (
        getattr(embedder, "requests_attempted", None),
        getattr(embedder, "prompt_tokens", None),
        getattr(embedder, "usage_complete", None),
    )
    if type(values[0]) is int and type(values[1]) is int and type(values[2]) is bool:
        return values
    return None


def _record_embedding_usage(
    usage: dict[str, EmbeddingStageUsage],
    stage: str,
    before: tuple[int, int, bool] | None,
    embedder: Any,
) -> None:
    after = _embedding_usage_snapshot(embedder)
    if before is not None and after is not None:
        usage[stage] = EmbeddingStageUsage(
            requests_attempted=after[0] - before[0],
            prompt_tokens=after[1] - before[1],
            usage_complete=before[2] and after[2],
        )


def _digest(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
