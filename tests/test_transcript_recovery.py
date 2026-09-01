from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from voren.actions.gateway import ActionGateway
from voren.actions.ledger import SQLiteOperationLedger
from voren.adapters.fake_workspace import FakeWorkspaceAdapter
from voren.adapters.workspace_contracts import (
    WORKSPACE_CONTRACT_VERSION,
    create_calendar_event_definition,
)
from voren.observations.models import (
    ObservationItem,
    Provenance,
    SourceKind,
    ToolObservation,
    TrustLevel,
)
from voren.observations.read_tools import READ_TOOL_DEFINITIONS
from voren.runs.manager import RunManager
from voren.runs.models import RunConfig, RunEventType, RunStatus
from voren.runs.store import SQLiteRunStore
from voren.runtime.agent_loop import AgentLoop
from voren.runtime.models import (
    MessageRole,
    ModelResponse,
    ModelUsage,
    RuntimeResultStatus,
    ToolCall,
)
from voren.runtime.tools import external_action_tool
from voren.runtime.transcripts import (
    SQLiteTranscriptStore,
    TranscriptIntegrityError,
)
from voren.testing.scripted_model import ScriptedModelAdapter


SENSITIVE_BODY = "Private hiking details from the operator's mailbox"


class CheckpointReadAdapter:
    definitions = READ_TOOL_DEFINITIONS

    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(self, *, tool_call_id: str, tool_name: str, arguments: dict):
        self.calls.append(tool_call_id)
        return ToolObservation.succeeded(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            items=(
                ObservationItem(
                    data={"id": "email-7", "body": SENSITIVE_BODY},
                    provenance=Provenance(
                        trust=TrustLevel.EXTERNAL_UNTRUSTED,
                        source=SourceKind.EMAIL,
                        source_ref="email-7",
                        retrieved_by=f"test:{tool_name}",
                        retrieved_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
                        instruction_authority=False,
                    ),
                ),
            ),
        )


class InterruptOnSecondModelRequest:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *, messages, tools, cancellation):
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="read-before-restart",
                        name="search_emails",
                        arguments={"query": "hiking"},
                    ),
                ),
                usage=ModelUsage(input_tokens=10, output_tokens=2, total_tokens=12),
            )
        raise KeyboardInterrupt("simulated process stop")


class TranscriptRecoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database = Path(self.temporary_directory.name) / "voren.sqlite3"
        self.key = b"k" * 32

    @staticmethod
    def config() -> RunConfig:
        return RunConfig(
            workflow="checkpoint_recovery",
            world_adapter="deterministic_workspace",
            policy_version="provenance-and-approval-v1",
            action_contract_versions=(WORKSPACE_CONTRACT_VERSION,),
        )

    def open_loop(self, *, model, key: bytes | None = None):
        ledger = SQLiteOperationLedger(self.database)
        store = SQLiteRunStore(self.database)
        transcript_store = SQLiteTranscriptStore(
            self.database, key=key if key is not None else self.key
        )
        world = FakeWorkspaceAdapter()
        action_definition = create_calendar_event_definition()
        gateway = ActionGateway(
            definitions=(action_definition,),
            adapter=world,
            ledger=ledger,
        )
        manager = RunManager(
            store=store,
            operation_ledger=ledger,
            action_gateway=gateway,
            run_id_factory=lambda: "run-recovery-1",
            event_id_factory=lambda: f"event-{uuid4()}",
            clock=lambda: datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        )
        reads = CheckpointReadAdapter()
        loop = AgentLoop(
            model=model,
            read_tools=reads,
            action_tools=(
                external_action_tool(
                    action_definition,
                    description="Propose one exact calendar action for approval.",
                ),
            ),
            run_manager=manager,
            transcript_store=transcript_store,
        )
        return loop, reads, transcript_store, store, ledger

    @staticmethod
    def close_stack(transcript_store, store, ledger) -> None:
        transcript_store.close()
        store.close()
        ledger.close()

    def create_interrupted_run(self):
        stack = self.open_loop(model=InterruptOnSecondModelRequest())
        loop, _, transcript_store, store, ledger = stack
        with self.assertRaises(KeyboardInterrupt):
            loop.run(user_request="Summarize the hiking email.", config=self.config())
        self.assertEqual(store.get_run("run-recovery-1").status, RunStatus.RUNNING)
        self.assertTrue(transcript_store.contains("run-recovery-1"))
        return stack

    def test_restart_restores_context_budgets_and_does_not_replay_read(self) -> None:
        first_stack = self.create_interrupted_run()
        _, _, transcript_store, store, ledger = first_stack
        with sqlite3.connect(self.database) as connection:
            ciphertext = connection.execute(
                "SELECT ciphertext FROM run_transcripts WHERE run_id = ?",
                ("run-recovery-1",),
            ).fetchone()[0]
        self.assertNotIn(SENSITIVE_BODY.encode("utf-8"), ciphertext)
        self.close_stack(transcript_store, store, ledger)

        resumed_model = ScriptedModelAdapter(
            (
                ModelResponse(
                    text="The hiking email was found.",
                    usage=ModelUsage(
                        input_tokens=8, output_tokens=2, total_tokens=10
                    ),
                ),
            )
        )
        loop, reads, transcript_store, store, ledger = self.open_loop(
            model=resumed_model
        )
        try:
            result = loop.resume("run-recovery-1")

            self.assertEqual(result.status, RuntimeResultStatus.COMPLETED)
            self.assertEqual(result.model_steps, 2)
            self.assertEqual(result.tool_calls, 1)
            self.assertEqual(result.usage.model_requests, 2)
            self.assertEqual(result.usage.total_tokens, 22)
            self.assertEqual(reads.calls, [])
            self.assertFalse(transcript_store.contains("run-recovery-1"))
            restored_messages = resumed_model.requests[0][0]
            self.assertEqual(restored_messages[-1].role, MessageRole.TOOL)
            self.assertIn(SENSITIVE_BODY, str(restored_messages[-1].content))
            requested_step_two = [
                event
                for event in store.list_events("run-recovery-1")
                if event.event_type is RunEventType.MODEL_REQUESTED
                and event.payload["step"] == 2
            ]
            self.assertEqual(len(requested_step_two), 1)
        finally:
            self.close_stack(transcript_store, store, ledger)

    def test_wrong_key_cannot_decrypt_checkpoint(self) -> None:
        stack = self.create_interrupted_run()
        _, _, transcript_store, store, ledger = stack
        self.close_stack(transcript_store, store, ledger)

        wrong_store = SQLiteTranscriptStore(self.database, key=b"w" * 32)
        try:
            with self.assertRaises(TranscriptIntegrityError):
                wrong_store.load("run-recovery-1")
        finally:
            wrong_store.close()

    def test_ciphertext_tampering_is_detected(self) -> None:
        stack = self.create_interrupted_run()
        _, _, transcript_store, store, ledger = stack
        self.close_stack(transcript_store, store, ledger)
        with sqlite3.connect(self.database) as connection:
            ciphertext = connection.execute(
                "SELECT ciphertext FROM run_transcripts WHERE run_id = ?",
                ("run-recovery-1",),
            ).fetchone()[0]
            tampered = bytes([ciphertext[0] ^ 1]) + ciphertext[1:]
            connection.execute(
                "UPDATE run_transcripts SET ciphertext = ? WHERE run_id = ?",
                (tampered, "run-recovery-1"),
            )

        reopened = SQLiteTranscriptStore(self.database, key=self.key)
        try:
            with self.assertRaises(TranscriptIntegrityError):
                reopened.load("run-recovery-1")
        finally:
            reopened.close()


if __name__ == "__main__":
    unittest.main()
