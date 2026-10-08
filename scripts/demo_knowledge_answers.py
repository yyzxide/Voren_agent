#!/usr/bin/env python3
"""Controlled answer-proposal demo, not an evaluation of a real model's judgement."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from voren.evaluation.artifacts import detect_source_revision
from voren.knowledge.answers import KnowledgeAnswerService
from voren.knowledge.models import KnowledgeDocument, KnowledgeSourceKind
from voren.knowledge.store import SQLiteKnowledgeStore
from voren.runtime.models import ModelResponse, ToolCall
from voren.testing.scripted_model import ScriptedModelAdapter


def run_demo() -> dict:
    """Exercise explicit proposals; expected statuses are author-supplied labels."""
    repository = Path(__file__).resolve().parents[1]
    revision = detect_source_revision(repository)
    rows = []
    content = (
        "Mira owns the Atlas deployment checklist. "
        "The Atlas release review is on Friday at 09:30. "
        "No emergency pager number is recorded in these meeting notes."
    )
    document = KnowledgeDocument.create(
        document_id="meeting:atlas",
        title="Atlas release review",
        source_uri="fixture://answers/atlas",
        source_kind=KnowledgeSourceKind.MEETING_NOTE,
        content=content,
        created_at=datetime(2026, 10, 8, tzinfo=UTC),
    )
    with tempfile.TemporaryDirectory(prefix="voren-answer-demo-") as temporary:
        store = SQLiteKnowledgeStore(Path(temporary) / "knowledge.sqlite3")
        try:
            store.install(document)
            store.activate(document.ref, reason="controlled answer demo fixture")
            corpus = store.snapshot_corpus()
            hit = store.search("Atlas checklist owner", corpus=corpus)[0]
            quote = "Mira owns the Atlas deployment checklist."
            start = document.content.index(quote)
            citation = {"hit_id": hit.chunk_id, "quote": quote, "start": start, "end": start + len(quote)}
            supported = {"status": "answer", "claims": [{"text": "Mira owns the checklist.", "citations": [citation]}]}

            def propose(case_id, question, draft, expected, *, category, note="", pinned=corpus, response=None):
                model = ScriptedModelAdapter((response or ModelResponse(text=json.dumps(draft)),))
                result = KnowledgeAnswerService(store, model, corpus=pinned).answer(question)
                rows.append({
                    "case_id": case_id,
                    "category": category,
                    "question": question,
                    "expected_status": expected,
                    "passed": result.status == expected,
                    "note": note,
                    "model_proposal": draft,
                    "result": result.model_dump(mode="json"),
                })

            propose("supported-answer", "Atlas checklist owner", supported, "answered", category="valid_citation")
            propose("explicit-abstention", "Atlas emergency pager number", {
                "status": "abstain", "claims": [], "reason": "The retrieved notes do not contain the requested number.",
            }, "abstained", category="abstention", note="The proposal itself decides to abstain; this does not test real-model detection.")
            propose("empty-window", "quasarneutrino991", supported, "abstained", category="empty_retrieval",
                    note="No model call is needed; this says only that this retrieval window is empty.")
            for case_id, invalid in (
                ("unknown-hit", {**citation, "hit_id": "0" * 64}),
                ("forged-quote", {**citation, "quote": "Nora owns the Atlas deployment checklist."}),
                ("wrong-span", {**citation, "start": start + 1, "end": start + len(quote) + 1}),
            ):
                propose(case_id, "Atlas checklist owner", {
                    "status": "answer", "claims": [{"text": "Mira owns the checklist.", "citations": [invalid]}],
                }, "rejected", category="invalid_citation")
            propose("uncited-claim", "Atlas checklist owner", {
                "status": "answer", "claims": [{"text": "Mira owns the checklist.", "citations": []}],
            }, "rejected", category="invalid_proposal")
            propose("unexpected-tool-call", "Atlas checklist owner", None, "rejected", category="invalid_proposal",
                    response=ModelResponse(tool_calls=(ToolCall(call_id="forbidden", name="send_email", arguments={}),)))
            propose("real-quote-unrelated-claim", "Atlas emergency pager number", {
                "status": "answer", "claims": [{"text": "The emergency pager number is 555-0199.", "citations": [citation]}],
            }, "answered", category="semantic_boundary",
                    note="Deliberately unsupported claim with a genuine quotation: citation checking cannot prove semantic support.")
            corrected = KnowledgeDocument.create(
                document_id=document.ref.document_id,
                title=document.title,
                source_uri=document.source_uri,
                source_kind=document.source_kind,
                content="Nora now owns the Atlas deployment checklist.",
                created_at=datetime(2026, 10, 8, 1, tzinfo=UTC),
            )
            store.install(corrected)
            store.activate(corrected.ref, reason="controlled mid-request correction")
            propose("frozen-source-version", "Atlas checklist owner", supported, "answered", category="source_snapshot",
                    note="The original snapshot retains Mira; a new request would see the explicitly activated Nora version.")
        finally:
            store.close()

    invalid = [row for row in rows if row["category"] == "invalid_citation"]
    boundary = [row for row in rows if row["category"] == "semantic_boundary"]
    artifact = {
        "schema_version": "voren-answer-protocol-demo/v1",
        "created_at": datetime.now(UTC).isoformat(),
        "code_revision": revision.revision,
        "code_dirty": revision.dirty,
        "scenario_count": len(rows),
        "passed_count": sum(row["passed"] for row in rows),
        "invalid_citation_cases": len(invalid),
        "invalid_citation_rejected": sum(row["result"]["status"] == "rejected" for row in invalid),
        "semantic_boundary_cases": len(boundary),
        "semantic_boundary_accepted_unverified": sum(
            row["result"]["status"] == "answered" and row["result"]["semantic_support"] == "unverified" for row in boundary
        ),
        "model_mode": "explicit-scripted-proposals",
        "external_calls": False,
        "limitations": [
            "Controlled proposals are authored with the feature; this is protocol evidence, not real-model answer accuracy.",
            "Valid source quotations do not prove that a claim follows from them or answers the question.",
            "The previous retrieval no-answer false positives remain a separate measurement; this demo does not replace 3/6 with a new rate.",
            "No automatic action, external account or online model is used.",
        ],
        "cases": rows,
    }
    canonical = json.dumps(artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    artifact["artifact_digest"] = hashlib.sha256(canonical.encode()).hexdigest()
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".voren/artifacts/knowledge-answers.json"))
    args = parser.parse_args()
    artifact = run_demo()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "artifact": str(args.output.resolve()),
        **{key: artifact[key] for key in (
            "scenario_count", "passed_count", "invalid_citation_cases", "invalid_citation_rejected",
            "semantic_boundary_accepted_unverified", "artifact_digest", "model_mode", "external_calls",
        )},
    }, ensure_ascii=False, indent=2))
    return 0 if artifact["passed_count"] == artifact["scenario_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
