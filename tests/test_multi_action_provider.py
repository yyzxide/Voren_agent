from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from voren.actions.gateway import ActionGateway
from voren.actions.ledger import SQLiteOperationLedger
from voren.actions.models import ApprovalDecision, ReceiptStatus
from voren.adapters.fake_workspace import FakeWorkspaceAdapter
from voren.adapters.workspace_contracts import (
    WORKSPACE_CONTRACT_VERSION,
    create_calendar_event_definition,
    send_email_definition,
)
from voren.providers.openai_responses import (
    OpenAIResponsesConfig,
    OpenAIResponsesModelAdapter,
)
from voren.runs.manager import RunManager
from voren.runs.models import RunConfig, RunEventType, RunStatus
from voren.runs.store import SQLiteRunStore
from voren.runtime.agent_loop import AgentLoop
from voren.runtime.models import RuntimeResultStatus
from voren.runtime.tools import external_action_tool
from voren.runtime.transcripts import SQLiteTranscriptStore


class OfflineResponsesTransport:
    endpoint = "https://offline.example/v1/responses"

    def __init__(self, responses: tuple[dict, ...]) -> None:
        self.responses = responses
        self.requests: list[dict] = []

    def create_response(self, payload: dict) -> dict:
        self.requests.append(payload)
        if len(self.requests) > len(self.responses):
            raise AssertionError("unexpected provider request")
        return self.responses[len(self.requests) - 1]

    def retrieve_response(self, response_id: str) -> dict:
        raise AssertionError("offline responses are already complete")

    def cancel_response(self, response_id: str) -> dict:
        raise AssertionError("offline responses do not require cancellation")


class NoReadTools:
    definitions = ()

    def execute(self, **kwargs):
        raise AssertionError("this workflow has no read calls")


class MultiActionProviderTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / "runtime.sqlite3"
        self.now = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)
        # This object represents the external service, whose effects survive
        # every reconstruction of local clients, adapters and SQLite stores.
        self.world = FakeWorkspaceAdapter()
        self.first = self.action_response(
            "1", "create_calendar_event", {
                "title": "Planning meeting",
                "start_time": "2026-09-27T09:00:00",
                "end_time": "2026-09-27T10:00:00",
            },
        )
        self.second = self.action_response(
            "2", "send_email", {
                "recipients": ["colleague@example.com"],
                "subject": "Meeting created",
                "body": "The planning meeting is on the calendar.",
            },
        )
        self.final = {
            "status": "completed",
            "output": [{
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "Both actions verified."}],
            }],
            "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        }

    @staticmethod
    def action_response(number: str, name: str, arguments: dict) -> dict:
        return {
            "status": "completed",
            "output": [
                {"type": "reasoning", "id": f"reasoning-{number}",
                 "encrypted_content": f"opaque-provider-reasoning-{number}"},
                {"type": "function_call", "id": f"function-{number}",
                 "call_id": f"call-{number}", "name": name,
                 "arguments": json.dumps(arguments)},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        }

    @contextmanager
    def open_stack(self, responses: tuple[dict, ...] = ()):
        ledger = SQLiteOperationLedger(self.database)
        store = SQLiteRunStore(self.database)
        transcripts = SQLiteTranscriptStore(self.database, key=b"t" * 32)
        definitions = (create_calendar_event_definition(), send_email_definition())
        gateway = ActionGateway(
            definitions=definitions, adapter=self.world, ledger=ledger,
            clock=lambda: self.now,
        )
        manager = RunManager(
            store=store, operation_ledger=ledger, action_gateway=gateway,
            run_id_factory=lambda: "provider-multi-action", clock=lambda: self.now,
        )
        transport = OfflineResponsesTransport(responses)
        provider = OpenAIResponsesModelAdapter(
            config=OpenAIResponsesConfig(model="offline-model"), transport=transport
        )
        loop = AgentLoop(
            model=provider, read_tools=NoReadTools(), run_manager=manager,
            transcript_store=transcripts,
            action_tools=tuple(
                external_action_tool(definition, description=definition.name)
                for definition in definitions
            ),
        )
        try:
            yield loop, manager, store, transcripts, transport
        finally:
            transcripts.close()
            store.close()
            ledger.close()

    def approve(self, manager, proposal) -> None:
        receipt = manager.resume_with_approval(
            "provider-multi-action",
            ApprovalDecision.for_proposal(
                proposal, approval_id=f"approval-{proposal.operation_id}",
                decided_by="operator:test", approved=True, decided_at=self.now,
                ttl=timedelta(minutes=5),
            ),
        )
        self.assertEqual(receipt.status, ReceiptStatus.VERIFIED)

    def exercise_reconstruction(self, *, interrupt_acknowledgement: bool) -> None:
        with self.open_stack((self.first,)) as (loop, _, _, _, transport):
            first = loop.run(
                user_request="Create the meeting, then send the follow-up email.",
                config=RunConfig(
                    workflow="two_actions", world_adapter="offline_workspace",
                    policy_version="exact-effects-v1",
                    action_contract_versions=(WORKSPACE_CONTRACT_VERSION,),
                    continue_after_action=True,
                ),
            )
            self.assertEqual(first.status, RuntimeResultStatus.WAITING_APPROVAL)
            self.assertEqual(len(transport.requests), 1)
            self.assertEqual(self.world.commit_attempts, 0)

        with self.open_stack() as (loop, manager, store, _, transport):
            paused = loop.resume(first.run_id)
            self.assertEqual(paused.pending_proposal, first.pending_proposal)
            self.assertEqual(transport.requests, [])
            self.approve(manager, paused.pending_proposal)
            self.assertEqual(store.get_run(first.run_id).status, RunStatus.RUNNING)
            self.assertEqual(self.world.commit_attempts, 1)

        if interrupt_acknowledgement:
            with self.open_stack() as (loop, manager, _, transcripts, transport):
                def interrupted_acknowledgement(*args):
                    raise KeyboardInterrupt("stop after receipt checkpoint before acknowledgement")

                manager.acknowledge_action_result = interrupted_acknowledgement
                with self.assertRaises(KeyboardInterrupt):
                    loop.resume(first.run_id)
                checkpoint = transcripts.load(first.run_id)
                self.assertIsNone(checkpoint.pending_response)
                self.assertEqual(checkpoint.messages[-1].content["type"], "action_receipt")
                self.assertEqual(transport.requests, [])

        with self.open_stack((self.second,)) as (loop, _, _, _, transport):
            second = loop.resume(first.run_id)
            self.assertEqual(second.status, RuntimeResultStatus.WAITING_APPROVAL)
            self.assertEqual(second.model_steps, 2)
            self.assertEqual(second.tool_calls, 2)
            self.assertEqual(self.world.commit_attempts, 1)
            self.assertEqual(transport.requests[0]["input"][1:3], self.first["output"])
            receipt_output = json.loads(transport.requests[0]["input"][3]["output"])
            self.assertEqual(receipt_output["receipt"]["status"], "verified")

        with self.open_stack() as (_, manager, _, _, transport):
            self.approve(manager, second.pending_proposal)
            self.assertEqual(self.world.commit_attempts, 2)
            self.assertEqual(transport.requests, [])

        with self.open_stack((self.final,)) as (loop, _, store, _, transport):
            final = loop.resume(first.run_id)
            self.assertEqual(final.status, RuntimeResultStatus.COMPLETED)
            self.assertEqual(final.final_text, "Both actions verified.")
            self.assertEqual(final.model_steps, 3)
            self.assertEqual(final.tool_calls, 2)
            self.assertEqual(final.usage.model_requests, 3)
            self.assertEqual(final.usage.reported_model_requests, 3)
            self.assertEqual(final.usage.total_tokens, 36)
            sent = transport.requests[0]["input"]
            self.assertEqual(sent[1:3], self.first["output"])
            self.assertEqual(sent[4:6], self.second["output"])
            self.assertEqual(sent[6]["type"], "function_call_output")
            events = store.list_events(first.run_id)
            self.assertEqual(sum(
                event.event_type is RunEventType.ACTION_RESULT_CONSUMED for event in events
            ), 2)
            event_text = json.dumps([event.payload for event in events])
            self.assertNotIn("opaque-provider-reasoning", event_text)
            self.assertNotIn("Both actions verified.", event_text)

        with self.open_stack() as (loop, _, store, _, transport):
            repeated = loop.resume(first.run_id)
            self.assertEqual(repeated, final)
            self.assertEqual(transport.requests, [])
            self.assertEqual(store.get_run(first.run_id).status, RunStatus.COMPLETED)
        self.assertEqual(self.world.commit_attempts, 2)
        self.assertEqual(len(self.world.events), 1)
        self.assertEqual(len(self.world.emails), 2)
        with sqlite3.connect(self.database) as connection:
            ciphertext = connection.execute(
                "SELECT ciphertext FROM run_transcripts WHERE run_id = ?", (first.run_id,)
            ).fetchone()[0]
            self.assertNotIn(b"opaque-provider-reasoning", ciphertext)

    def test_two_actions_replay_original_outputs_across_reconstruction(self) -> None:
        self.exercise_reconstruction(interrupt_acknowledgement=False)

    def test_receipt_checkpoint_survives_interrupted_acknowledgement(self) -> None:
        self.exercise_reconstruction(interrupt_acknowledgement=True)


if __name__ == "__main__":
    unittest.main()
