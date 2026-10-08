"""Offline answer-proposal replay; citation validity is separate from semantics.

Preparation captures exact BM25 windows. Evaluation imports unmodified model
text and makes no API calls. Integrity checks rebuild retrieval and replay that
text, rather than trusting cached statuses or a recomputed JSON checksum.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from voren.knowledge.answers import KnowledgeAnswerDraft, KnowledgeAnswerResult, KnowledgeAnswerService
from voren.knowledge.models import (
    KnowledgeCorpusSnapshot, KnowledgeDocument, KnowledgeSearchHit, KnowledgeSourceKind,
)
from voren.knowledge.retrieval import CHUNK_OVERLAP, CHUNK_SIZE
from voren.knowledge.store import SQLiteKnowledgeStore
from voren.runtime.models import ModelResponse
from voren.testing.scripted_model import ScriptedModelAdapter


_FIXTURE_TIME = datetime(2026, 10, 8, tzinfo=UTC)
_MAX_FILE_BYTES = 32_000_000
_STATUSES = ("answered", "abstained", "rejected", "failed", "cancelled")
_LIMITATIONS = (
    "This run imports supplied proposal text and makes no model or embedding API calls.",
    "Answerability is a supplied dataset label, not an independently established fact.",
    "An answered status verifies exact source citations, not semantic correctness or completeness.",
    "Abstention decision counts are not semantic answer-accuracy measurements.",
    "Human support labels are supplied annotations; only reviewed answered cases enter their denominator.",
    "Checksums detect content changes and replay checks internal consistency; neither authenticates a model provider or reviewer.",
    "Historical artifacts require a compatible retrieval and answer implementation to replay.",
)


def _digest(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")


class AnswerEvaluationDocument(FrozenModel):
    document_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]*$")
    title: str = Field(min_length=1, max_length=500)
    source_uri: str = Field(min_length=1, max_length=2_048)
    source_kind: KnowledgeSourceKind = KnowledgeSourceKind.OPERATOR_NOTE
    content: str = Field(min_length=1, max_length=1_000_000)

    def as_document(self) -> KnowledgeDocument:
        return KnowledgeDocument.create(
            document_id=self.document_id, title=self.title, source_uri=self.source_uri,
            source_kind=self.source_kind, content=self.content, created_at=_FIXTURE_TIME,
        )


class AnswerEvaluationCase(FrozenModel):
    case_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]*$", max_length=100)
    question: str = Field(min_length=1, max_length=500)
    answerable: bool = Field(strict=True)

    @model_validator(mode="after")
    def question_is_nonblank(self) -> Self:
        if not self.question.strip():
            raise ValueError("answer evaluation question must not be blank")
        self.question.encode("utf-8")
        return self


class AnswerEvaluationDataset(FrozenModel):
    schema_version: Literal["voren-answer-dataset/v1"] = "voren-answer-dataset/v1"
    dataset_id: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=2_000)
    synthetic: bool = Field(strict=True)
    documents: tuple[AnswerEvaluationDocument, ...] = Field(min_length=1, max_length=100)
    cases: tuple[AnswerEvaluationCase, ...] = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def identifiers_are_unique_and_sources_valid(self) -> Self:
        document_ids = tuple(document.document_id for document in self.documents)
        case_ids = tuple(case.case_id for case in self.cases)
        if len(set(document_ids)) != len(document_ids):
            raise ValueError("answer dataset document IDs must be unique")
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("answer dataset case IDs must be unique")
        for document in self.documents:
            document.as_document()
        return self

    @property
    def dataset_digest(self) -> str:
        return _digest(self.model_dump(mode="json"))


class AnswerEvaluationSettings(FrozenModel):
    retrieval_mode: Literal["bm25"] = "bm25"
    limit: int = Field(default=5, ge=1, le=20, strict=True)
    chunk_size: int = Field(default=CHUNK_SIZE, ge=1, strict=True)
    chunk_overlap: int = Field(default=CHUNK_OVERLAP, ge=0, strict=True)
    answer_contract: Literal["voren-knowledge-answer/v1"] = "voren-knowledge-answer/v1"

    def assert_compatible(self) -> None:
        if (self.chunk_size, self.chunk_overlap) != (CHUNK_SIZE, CHUNK_OVERLAP):
            raise ValueError("prepared retrieval configuration differs from this implementation")


class PreparedAnswerCase(FrozenModel):
    case_id: str
    case_ref: str = Field(pattern=r"^[0-9a-f]{64}$")
    question: str
    answerable: bool = Field(strict=True)
    hits: tuple[KnowledgeSearchHit, ...] = Field(max_length=20)
    retrieval_window_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class PreparedAnswers(FrozenModel):
    schema_version: Literal["voren-prepared-answers/v1"] = "voren-prepared-answers/v1"
    created_at: datetime
    code_revision: str
    code_dirty: bool = Field(strict=True)
    dataset: AnswerEvaluationDataset
    dataset_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    settings: AnswerEvaluationSettings
    corpus: KnowledgeCorpusSnapshot
    cases: tuple[PreparedAnswerCase, ...]
    artifact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    def calculated_digest(self) -> str:
        return _digest(self.model_dump(mode="json", exclude={"artifact_digest"}))

    def assert_integrity(self) -> None:
        if self.artifact_digest != self.calculated_digest():
            raise ValueError("prepared answer artifact digest does not match")
        if self.dataset_digest != self.dataset.dataset_digest:
            raise ValueError("prepared answer dataset digest does not match")
        self.settings.assert_compatible()
        with tempfile.TemporaryDirectory(prefix="voren-answer-prepare-check-") as temporary:
            store = _install_dataset(self.dataset, Path(temporary) / "knowledge.sqlite3")
            try:
                if self.corpus != store.snapshot_corpus():
                    raise ValueError("prepared corpus differs from source document versions")
                expected = _prepare_cases(self.dataset, self.settings, self.corpus, store)
                if self.cases != expected:
                    raise ValueError("prepared retrieval windows differ from rebuilt source evidence")
            finally:
                store.close()


class HumanSupportReview(FrozenModel):
    label: Literal["supported", "unsupported", "unreviewed"] = "unreviewed"
    reviewer: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def reviewed_labels_identify_a_reviewer(self) -> Self:
        if self.label == "unreviewed":
            if self.reviewer is not None:
                raise ValueError("unreviewed support label cannot name a reviewer")
        elif self.reviewer is None or not self.reviewer.strip():
            raise ValueError("reviewed support label requires a nonblank reviewer")
        return self


class AnswerProposal(FrozenModel):
    case_id: str
    corpus_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    retrieval_window_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Preserve the exact text, including invalid JSON and duplicate JSON keys.
    # The service enforces its smaller 64,000-byte proposal boundary.
    proposal_text: str = Field(max_length=128_000)
    human_support: HumanSupportReview = Field(default_factory=HumanSupportReview)


class AnswerProposals(FrozenModel):
    schema_version: Literal["voren-answer-proposals/v1"] = "voren-answer-proposals/v1"
    origin: Literal["external_model", "manual", "scripted"]
    description: str = Field(min_length=1, max_length=2_000)
    model_id: str | None = Field(default=None, min_length=1, max_length=200)
    cases: tuple[AnswerProposal, ...] = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def proposal_ids_are_unique(self) -> Self:
        identifiers = tuple(proposal.case_id for proposal in self.cases)
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("answer proposal case IDs must be unique")
        if self.model_id is not None and not self.model_id.strip():
            raise ValueError("answer proposal model ID must not be blank")
        return self


class AnswerEvaluationCaseResult(FrozenModel):
    case_id: str
    answerable: bool = Field(strict=True)
    result: KnowledgeAnswerResult
    human_support: HumanSupportReview


class AnswerEvaluationSummary(FrozenModel):
    case_count: int = Field(ge=1)
    answerable_case_count: int = Field(ge=0)
    no_answer_case_count: int = Field(ge=0)
    status_counts: dict[str, int]
    claim_count: int = Field(ge=0)
    citation_verified_answer_count: int = Field(ge=0)
    answerable_answered_count: int = Field(ge=0)
    answerable_abstained_count: int = Field(ge=0)
    answerable_unresolved_count: int = Field(ge=0)
    no_answer_answered_count: int = Field(ge=0)
    no_answer_abstained_count: int = Field(ge=0)
    no_answer_unresolved_count: int = Field(ge=0)
    answerable_answer_rate: float | None = Field(default=None, ge=0, le=1)
    no_answer_abstention_rate: float | None = Field(default=None, ge=0, le=1)
    reviewed_answer_count: int = Field(ge=0)
    human_supported_count: int = Field(ge=0)
    human_unsupported_count: int = Field(ge=0)
    unreviewed_answer_count: int = Field(ge=0)
    human_supported_fraction: float | None = Field(default=None, ge=0, le=1)


class AnswerEvaluationArtifact(FrozenModel):
    schema_version: Literal["voren-answer-evaluation/v1"] = "voren-answer-evaluation/v1"
    created_at: datetime
    code_revision: str
    code_dirty: bool = Field(strict=True)
    external_calls: Literal[False] = False
    prepared: PreparedAnswers
    proposals: AnswerProposals
    cases: tuple[AnswerEvaluationCaseResult, ...]
    summary: AnswerEvaluationSummary
    metric_definitions: dict[str, str]
    limitations: tuple[str, ...]
    decision_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    def calculated_digest(self) -> str:
        return _digest(self.model_dump(mode="json", exclude={"artifact_digest"}))

    def assert_integrity(self) -> None:
        if self.artifact_digest != self.calculated_digest():
            raise ValueError("answer evaluation artifact digest does not match")
        self.prepared.assert_integrity()
        replayed = _replay(self.prepared, self.proposals)
        if self.cases != replayed:
            raise ValueError("answer evaluation results differ from original proposal replay")
        if self.summary != summarize_answer_cases(replayed):
            raise ValueError("answer evaluation summary differs from replayed results")
        if self.decision_digest != _decision_digest(replayed):
            raise ValueError("answer evaluation decision digest differs from replayed results")
        if self.metric_definitions != _metric_definitions() or self.limitations != _LIMITATIONS:
            raise ValueError("answer evaluation definitions or limitations differ from this contract")


def _install_dataset(dataset: AnswerEvaluationDataset, database: Path) -> SQLiteKnowledgeStore:
    store = SQLiteKnowledgeStore(database)
    try:
        for source in dataset.documents:
            document = source.as_document()
            store.install(document)
            store.activate(document.ref, reason="offline answer evaluation dataset")
    except Exception:
        store.close()
        raise
    return store


def _window_digest(case: AnswerEvaluationCase, settings: AnswerEvaluationSettings,
                   corpus: KnowledgeCorpusSnapshot, hits: tuple[KnowledgeSearchHit, ...]) -> str:
    return _digest({
        "case_id": case.case_id, "question": case.question.strip(),
        "settings": settings.model_dump(mode="json"), "corpus_digest": corpus.digest,
        "hits": [hit.model_dump(mode="json") for hit in hits],
    })


def _prepare_cases(dataset: AnswerEvaluationDataset, settings: AnswerEvaluationSettings,
                   corpus: KnowledgeCorpusSnapshot, store: SQLiteKnowledgeStore) -> tuple[PreparedAnswerCase, ...]:
    rows = []
    for case in dataset.cases:
        hits = tuple(store.search(case.question.strip(), limit=settings.limit, mode="bm25", corpus=corpus))
        window_digest = _window_digest(case, settings, corpus, hits)
        rows.append(PreparedAnswerCase(
            case_id=case.case_id, case_ref=_digest({"case_id": case.case_id, "window_digest": window_digest}),
            question=case.question.strip(), answerable=case.answerable,
            hits=hits, retrieval_window_digest=window_digest,
        ))
    return tuple(rows)


def prepare_answers(dataset: AnswerEvaluationDataset, *, limit: int = 5,
                    code_revision: str = "unavailable", code_dirty: bool = False) -> PreparedAnswers:
    dataset = AnswerEvaluationDataset.model_validate(dataset.model_dump(mode="json"))
    settings = AnswerEvaluationSettings(limit=limit)
    with tempfile.TemporaryDirectory(prefix="voren-answer-prepare-") as temporary:
        store = _install_dataset(dataset, Path(temporary) / "knowledge.sqlite3")
        try:
            corpus = store.snapshot_corpus()
            cases = _prepare_cases(dataset, settings, corpus, store)
        finally:
            store.close()
    fields = dict(created_at=datetime.now(UTC), code_revision=code_revision, code_dirty=code_dirty,
                  dataset=dataset, dataset_digest=dataset.dataset_digest,
                  settings=settings, corpus=corpus, cases=cases)
    provisional = PreparedAnswers(**fields, artifact_digest="0" * 64)
    return PreparedAnswers(**fields, artifact_digest=provisional.calculated_digest())


def proposal_template(prepared: PreparedAnswers) -> AnswerProposals:
    """Blank text is deliberately invalid until an operator supplies raw output."""
    prepared.assert_integrity()
    return AnswerProposals(
        origin="manual", description="Blank template; replace proposal_text with original output and declare its origin.",
        cases=tuple(AnswerProposal(
            case_id=case.case_id, corpus_digest=prepared.corpus.digest,
            retrieval_window_digest=case.retrieval_window_digest, proposal_text="",
        ) for case in prepared.cases),
    )


def answer_model_inputs(prepared: PreparedAnswers) -> dict:
    """Export evidence and proposal schema without answerability/review labels.

    This is local material for an operator, not a provider wire request or an
    online generation command. Do not send the labelled prepared bundle itself
    to a model whose answer decisions are being measured.
    """
    prepared.assert_integrity()
    return {
        "schema_version": "voren-answer-model-inputs/v1",
        "prepared_artifact_digest": prepared.artifact_digest,
        "proposal_schema": KnowledgeAnswerDraft.model_json_schema(),
        "cases": [{
            "case_ref": case.case_ref, "question": case.question,
            "corpus_digest": prepared.corpus.digest,
            "retrieval_window_digest": case.retrieval_window_digest,
            "hits": [{"hit_id": hit.chunk_id, **hit.model_dump(mode="json")} for hit in case.hits],
        } for case in prepared.cases],
    }


def _validate_bindings(prepared: PreparedAnswers, proposals: AnswerProposals) -> dict[str, AnswerProposal]:
    expected = {case.case_id for case in prepared.cases}
    supplied = {proposal.case_id: proposal for proposal in proposals.cases}
    if set(supplied) != expected or len(supplied) != len(proposals.cases):
        raise ValueError("proposal cases must match all prepared case IDs exactly")
    for case in prepared.cases:
        proposal = supplied[case.case_id]
        if proposal.corpus_digest != prepared.corpus.digest:
            raise ValueError("proposal corpus digest differs from prepared source versions")
        if proposal.retrieval_window_digest != case.retrieval_window_digest:
            raise ValueError("proposal retrieval window digest differs from prepared evidence")
    return supplied


def _replay(prepared: PreparedAnswers, proposals: AnswerProposals) -> tuple[AnswerEvaluationCaseResult, ...]:
    supplied = _validate_bindings(prepared, proposals)
    rows = []
    with tempfile.TemporaryDirectory(prefix="voren-answer-replay-") as temporary:
        store = _install_dataset(prepared.dataset, Path(temporary) / "knowledge.sqlite3")
        try:
            for case in prepared.cases:
                proposal = supplied[case.case_id]
                model = ScriptedModelAdapter((ModelResponse(text=proposal.proposal_text),))
                result = KnowledgeAnswerService(store, model, corpus=prepared.corpus).answer(
                    case.question, limit=prepared.settings.limit,
                )
                if result.hits != case.hits or result.corpus != prepared.corpus:
                    raise ValueError("replayed retrieval differs from prepared evidence")
                if proposal.human_support.label != "unreviewed" and result.status != "answered":
                    raise ValueError("human semantic support labels require an answered result")
                rows.append(AnswerEvaluationCaseResult(
                    case_id=case.case_id, answerable=case.answerable,
                    result=result, human_support=proposal.human_support,
                ))
        finally:
            store.close()
    return tuple(rows)


def summarize_answer_cases(cases: tuple[AnswerEvaluationCaseResult, ...]) -> AnswerEvaluationSummary:
    statuses = Counter(case.result.status for case in cases)
    answerable = tuple(case for case in cases if case.answerable)
    absent = tuple(case for case in cases if not case.answerable)
    answered = tuple(case for case in cases if case.result.status == "answered")
    reviewed = tuple(case for case in answered if case.human_support.label != "unreviewed")
    supported = sum(case.human_support.label == "supported" for case in reviewed)
    a_answered = sum(case.result.status == "answered" for case in answerable)
    a_abstained = sum(case.result.status == "abstained" for case in answerable)
    n_answered = sum(case.result.status == "answered" for case in absent)
    n_abstained = sum(case.result.status == "abstained" for case in absent)
    return AnswerEvaluationSummary(
        case_count=len(cases), answerable_case_count=len(answerable), no_answer_case_count=len(absent),
        status_counts={status: statuses[status] for status in _STATUSES},
        claim_count=sum(len(case.result.claims) for case in cases),
        citation_verified_answer_count=sum(case.result.citation_integrity == "verified" for case in cases),
        answerable_answered_count=a_answered, answerable_abstained_count=a_abstained,
        answerable_unresolved_count=len(answerable) - a_answered - a_abstained,
        no_answer_answered_count=n_answered, no_answer_abstained_count=n_abstained,
        no_answer_unresolved_count=len(absent) - n_answered - n_abstained,
        answerable_answer_rate=a_answered / len(answerable) if answerable else None,
        no_answer_abstention_rate=n_abstained / len(absent) if absent else None,
        reviewed_answer_count=len(reviewed), human_supported_count=supported,
        human_unsupported_count=len(reviewed) - supported,
        unreviewed_answer_count=len(answered) - len(reviewed),
        human_supported_fraction=supported / len(reviewed) if reviewed else None,
    )


def _decision_digest(cases: tuple[AnswerEvaluationCaseResult, ...]) -> str:
    return _digest([case.model_dump(mode="json") for case in cases])


def _metric_definitions() -> dict[str, str]:
    return {
        "status_counts": "All protocol states are separate; rejected/failed/cancelled are unresolved, never correct abstentions.",
        "answerable_answer_rate": "answered / all supplied answerable cases; this is a decision rate, not semantic accuracy.",
        "no_answer_abstention_rate": "abstained / all supplied no-answer cases, including unresolved cases in the denominator.",
        "human_supported_fraction": "Supplied supported labels / reviewed answered cases; unreviewed answers are excluded.",
        "citation_verified_answer_count": "Answers whose exact quotes pass application checks; semantic support remains unverified in service results.",
    }


def evaluate_answers(prepared: PreparedAnswers, proposals: AnswerProposals, *,
                     code_revision: str = "unavailable", code_dirty: bool = False) -> AnswerEvaluationArtifact:
    prepared = PreparedAnswers.model_validate(prepared.model_dump(mode="json"))
    proposals = AnswerProposals.model_validate(proposals.model_dump(mode="json"))
    prepared.assert_integrity()
    cases = _replay(prepared, proposals)
    fields = dict(created_at=datetime.now(UTC), code_revision=code_revision, code_dirty=code_dirty,
                  prepared=prepared, proposals=proposals, cases=cases,
                  summary=summarize_answer_cases(cases), metric_definitions=_metric_definitions(),
                  limitations=_LIMITATIONS, decision_digest=_decision_digest(cases))
    provisional = AnswerEvaluationArtifact(**fields, artifact_digest="0" * 64)
    return AnswerEvaluationArtifact(**fields, artifact_digest=provisional.calculated_digest())


def _write_json(path: Path, value: BaseModel | dict) -> None:
    payload = value.model_dump_json(indent=2) if isinstance(value, BaseModel) else json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    if len((payload + "\n").encode("utf-8")) > _MAX_FILE_BYTES:
        raise ValueError("answer evaluation file exceeds the 32 MB output boundary")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as temporary:
            temporary_name = temporary.name
            temporary.write(payload + "\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _read_json(path: Path, model: type[BaseModel]):
    with path.open("rb") as source:
        raw = source.read(_MAX_FILE_BYTES + 1)
    if len(raw) > _MAX_FILE_BYTES:
        raise ValueError("answer evaluation file exceeds the 32 MB input boundary")
    # Outer duplicate keys are ambiguous too; raw proposal text stays untouched.
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate answer evaluation JSON key")
            result[key] = value
        return result
    json.loads(raw, object_pairs_hook=unique_object)
    return model.model_validate_json(raw)


def load_answer_dataset(path: Path) -> AnswerEvaluationDataset:
    return _read_json(path, AnswerEvaluationDataset)


def read_answer_proposals(path: Path) -> AnswerProposals:
    return _read_json(path, AnswerProposals)


def write_answer_proposals(path: Path, proposals: AnswerProposals) -> None:
    _write_json(path, proposals)


def write_answer_model_inputs(path: Path, prepared: PreparedAnswers) -> None:
    _write_json(path, answer_model_inputs(prepared))


def write_prepared_answers(path: Path, prepared: PreparedAnswers) -> None:
    prepared.assert_integrity()
    _write_json(path, prepared)


def read_prepared_answers(path: Path) -> PreparedAnswers:
    prepared = _read_json(path, PreparedAnswers)
    prepared.assert_integrity()
    return prepared


def write_answer_evaluation(path: Path, artifact: AnswerEvaluationArtifact) -> None:
    artifact.assert_integrity()
    _write_json(path, artifact)


def read_answer_evaluation(path: Path) -> AnswerEvaluationArtifact:
    artifact = _read_json(path, AnswerEvaluationArtifact)
    artifact.assert_integrity()
    return artifact
