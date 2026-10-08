"""HTTP and browser contracts for explicit, independent knowledge answers."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from voren.web.knowledge import create_knowledge_router
except ImportError:
    WEB_AVAILABLE = False
else:
    WEB_AVAILABLE = True

from voren.knowledge.models import KnowledgeCorpusSnapshot, KnowledgeDocument, KnowledgeSourceKind, KnowledgeVersionRef
from voren.knowledge.store import SQLiteKnowledgeStore
from voren.providers.openai_responses import ModelConfigurationError
from voren.runtime.models import ModelResponse, ToolCall
from voren.testing.scripted_model import ScriptedModelAdapter


@unittest.skipUnless(WEB_AVAILABLE, "Web integration dependencies are unavailable")
class WebKnowledgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Path(self.temporary.name) / "knowledge.sqlite3"
        self.document = KnowledgeDocument.create(
            document_id="meeting:web-atlas", title="<script>Atlas review</script>",
            source_uri="javascript:alert('source')", source_kind=KnowledgeSourceKind.MEETING_NOTE,
            content="Mira owns the Atlas deployment checklist. The review is on Friday.",
            created_at=datetime(2026, 10, 8, tzinfo=UTC),
        )
        with_store = SQLiteKnowledgeStore(self.database)
        try:
            with_store.install(self.document)
            with_store.activate(self.document.ref, reason="operator reviewed test source")
        finally:
            with_store.close()
        self.factory = Mock()
        app = FastAPI()
        app.include_router(create_knowledge_router(database=self.database, model_factory=self.factory))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.question = "Atlas checklist owner"

    def search(self, **changes):
        return self.client.post("/api/knowledge/search", json={"question": self.question, **changes})

    def draft(self, hit):
        quote = "Mira owns the Atlas deployment checklist."
        return {"status": "answer", "claims": [{"text": "Mira owns the checklist.", "citations": [{
            "hit_id": hit["chunk_id"], "quote": quote, "start": 0, "end": len(quote),
        }]}]}

    def ask(self, **changes):
        return self.client.post("/api/knowledge/ask", json={"question": self.question, **changes})

    def test_search_is_offline_and_returns_exact_source_window(self) -> None:
        response = self.search()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["mode"], "bm25")
        self.assertEqual(body["corpus"]["refs"], [self.document.ref.model_dump()])
        hit = body["hits"][0]
        self.assertEqual(hit["ref"], self.document.ref.model_dump())
        self.assertEqual(hit["snippet"], self.document.content[hit["chunk_start"]:hit["chunk_end"]])
        self.assertEqual(hit["source_uri"], self.document.source_uri)
        self.factory.assert_not_called()

    def test_draft_answer_is_verified_without_online_factory(self) -> None:
        window = self.search().json()
        response = self.ask(corpus=window["corpus"], draft=json.dumps(self.draft(window["hits"][0])))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "answered")
        self.assertEqual(body["proposal_mode"], "operator_draft")
        self.assertEqual(body["citation_integrity"], "verified")
        self.assertEqual(body["semantic_support"], "unverified")
        citation = body["claims"][0]["citations"][0]
        self.assertEqual(citation["source_uri"], self.document.source_uri)
        self.assertEqual(citation["ref"], self.document.ref.model_dump())
        self.factory.assert_not_called()

    def test_original_duplicate_json_keys_are_rejected(self) -> None:
        response = self.ask(draft='{"status":"answer","status":"abstain","claims":[]}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "rejected")
        self.assertEqual(response.json()["error_code"], "invalid_answer_proposal")
        self.factory.assert_not_called()

    def test_abstention_and_forged_citation_remain_distinct(self) -> None:
        abstained = self.ask(draft='{"status":"abstain","claims":[],"reason":"Missing evidence"}').json()
        self.assertEqual(abstained["status"], "abstained")
        proposal = self.draft(self.search().json()["hits"][0])
        proposal["claims"][0]["citations"][0]["hit_id"] = "0" * 64
        rejected = self.ask(draft=json.dumps(proposal)).json()
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["claims"], [])
        self.factory.assert_not_called()

    def test_online_requires_explicit_exclusive_opt_in_and_bounded_input(self) -> None:
        proposals = (
            {}, {"allow_model_api": False}, {"allow_model_api": "true"},
            {"draft": "{}", "allow_model_api": True}, {"draft": "{}", "mode": "hybrid"},
            {"draft": "{}", "limit": True}, {"draft": "{}", "limit": "5"},
            {"draft": "{}", "limit": 21}, {"draft": "汉" * 22_000},
            {"question": " " * 10, "draft": "{}"},
        )
        for values in proposals:
            with self.subTest(values={key: ("large" if key == "draft" else value) for key, value in values.items()}):
                self.assertEqual(self.ask(**values).status_code, 422)
        self.assertEqual(self.search(limit=0).status_code, 422)
        self.assertEqual(self.search(question="x" * 501).status_code, 422)
        self.factory.assert_not_called()

    def test_empty_window_does_not_construct_or_call_online_model(self) -> None:
        self.factory.side_effect = RuntimeError("must never construct provider")
        response = self.ask(question="quasarneutrino991", allow_model_api=True)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "abstained")
        self.assertEqual(body["usage"]["requests_attempted"], 0)
        self.assertEqual(body["proposal_mode"], "online_model")
        self.factory.assert_not_called()

    def test_snapshot_limits_and_explicit_empty_snapshot_do_not_expand_the_window(self) -> None:
        empty = KnowledgeCorpusSnapshot.create(()).model_dump(mode="json")
        self.assertEqual(self.search(corpus=empty).json()["hits"], [])
        body = self.ask(corpus=empty, allow_model_api=True).json()
        self.assertEqual(body["status"], "abstained")
        self.factory.assert_not_called()
        oversized = KnowledgeCorpusSnapshot.create(tuple(
            KnowledgeVersionRef(document_id=f"doc:{index:04d}", version_id="a" * 64)
            for index in range(1_001)
        )).model_dump(mode="json")
        self.assertEqual(self.search(corpus=oversized).status_code, 422)
        self.assertEqual(self.ask(corpus=oversized, allow_model_api=True).status_code, 422)
        long_identifier = KnowledgeCorpusSnapshot.create((KnowledgeVersionRef(
            document_id="d" * 501, version_id="a" * 64,
        ),)).model_dump(mode="json")
        self.assertEqual(self.search(corpus=long_identifier).status_code, 422)

    def test_explicit_online_request_has_no_action_tools(self) -> None:
        window = self.search().json()
        model = ScriptedModelAdapter((ModelResponse(text=json.dumps(self.draft(window["hits"][0]))),))
        self.factory.return_value = model
        body = self.ask(corpus=window["corpus"], allow_model_api=True).json()
        self.assertEqual(body["status"], "answered")
        self.assertEqual(body["proposal_mode"], "online_model")
        self.factory.assert_called_once_with()
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(model.requests[0][1], ())

    def test_provider_and_configuration_errors_are_sanitized_failures(self) -> None:
        for error in (RuntimeError("secret-token upstream body"), ModelConfigurationError("secret-token endpoint")):
            with self.subTest(error=type(error).__name__):
                self.factory.side_effect = error
                response = self.ask(allow_model_api=True)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["status"], "failed")
                self.assertEqual(response.json()["error_code"], "model_request_failed")
                self.assertNotIn("secret-token", response.text)

    def test_online_provider_failure_and_unexpected_tools_never_become_answers(self) -> None:
        failed_model = Mock()
        failed_model.complete.side_effect = RuntimeError("secret-token provider response")
        self.factory.return_value = failed_model
        failed = self.ask(allow_model_api=True)
        self.assertEqual(failed.json()["status"], "failed")
        self.assertNotIn("secret-token", failed.text)
        tool_model = ScriptedModelAdapter((ModelResponse(tool_calls=(ToolCall(
            call_id="unexpected-write", name="send_email", arguments={"body": "Do not execute"},
        ),)),))
        self.factory.return_value = tool_model
        rejected = self.ask(allow_model_api=True).json()
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["error_code"], "unexpected_model_tool_calls")
        self.assertEqual(rejected["claims"], [])
        self.assertEqual(tool_model.requests[0][1], ())

    def test_search_snapshot_pins_answer_after_active_source_replacement(self) -> None:
        window = self.search().json()
        replacement = KnowledgeDocument.create(
            document_id=self.document.ref.document_id, title="Corrected Atlas review",
            source_uri="fixture://atlas-corrected", source_kind=KnowledgeSourceKind.MEETING_NOTE,
            content="Nora owns the Atlas deployment checklist.", created_at=datetime(2026, 10, 9, tzinfo=UTC),
        )
        store = SQLiteKnowledgeStore(self.database)
        try:
            store.install(replacement)
            store.activate(replacement.ref, reason="operator reviewed corrected source")
        finally:
            store.close()
        self.assertEqual(self.search().json()["hits"][0]["ref"], replacement.ref.model_dump())
        frozen = self.search(corpus=window["corpus"]).json()
        self.assertEqual(frozen["hits"][0]["ref"], self.document.ref.model_dump())
        result = self.ask(corpus=window["corpus"], draft=json.dumps(self.draft(window["hits"][0]))).json()
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["claims"][0]["citations"][0]["ref"], self.document.ref.model_dump())

    def test_corrupted_source_returns_bounded_error_not_content(self) -> None:
        window = self.search().json()
        store = SQLiteKnowledgeStore(self.database)
        try:
            store._connection.execute("UPDATE knowledge_versions SET content = ?", ("secret-token corruption",))
            store._connection.commit()
        finally:
            store.close()
        response = self.search(corpus=window["corpus"])
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "knowledge_evidence_unavailable")
        self.assertNotIn("secret-token", response.text)
        body = self.ask(corpus=window["corpus"], allow_model_api=True).json()
        self.assertEqual(body["status"], "failed")
        self.assertEqual(body["error_code"], "knowledge_retrieval_failed")
        self.factory.assert_not_called()


class WebKnowledgeBrowserTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for the browser contract")
    def test_browser_preserves_plain_text_sources_and_explicit_request_boundaries(self) -> None:
        project = Path(__file__).resolve().parents[1]
        # A minimal DOM runs the actual script, exercising request and rendering
        # behavior without external browser dependencies or network calls.
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Node {
  constructor(tag='div') { this.tagName=tag; this.children=[]; this.textContent=''; this.value=''; this.checked=false; this.disabled=false; this.listeners={}; const classes=new Set(); this.classList={add(name){classes.add(name);},toggle(name,force){if(force===true)classes.add(name);else if(force===false)classes.delete(name);else if(classes.has(name))classes.delete(name);else classes.add(name);},remove(name){classes.delete(name);},contains(name){return classes.has(name);}}; }
  append(...nodes) { this.children.push(...nodes); }
  appendChild(node) { this.append(node); }
  replaceChildren(...nodes) { this.children=nodes; }
  addEventListener(name, callback) { this.listeners[name]=callback; }
}
const nodes=new Map();
const get=id=>{if(!nodes.has(id)) nodes.set(id,new Node());return nodes.get(id);};
const calls=[];
const source={title:'<img onerror=evil()>',source_uri:'javascript:evil()',ref:{document_id:'doc',version_id:'a'.repeat(64)},snippet:'<script>evil()</script>',chunk_start:0,chunk_end:23,chunk_id:'b'.repeat(64)};
const corpus={refs:[source.ref],digest:'c'.repeat(64)};
const context={document:{getElementById:get,createElement:tag=>new Node(tag)},localStorage:{getItem(){return null;},setItem(){},removeItem(){}},crypto:{randomUUID(){return 'request-id';}},EventSource:class {},fetch:async(path,options)=>{calls.push({path,body:options?.body&&JSON.parse(options.body)});return {ok:true,json:async()=>path==='/api/health'?{mode:'demo',workspace:'agentdojo',active_profile_count:0,active_skill_count:0}:path.endsWith('/search')?{question:'Atlas',hits:[source],corpus}: {status:'abstained',proposal_mode:'operator_draft',usage:{requests_attempted:1},reason:'<script>reason()</script>',claims:[]}};}};
vm.createContext(context);
vm.runInContext(fs.readFileSync('src/voren/web/static/app.js','utf8'),context);
const evaluate=code=>vm.runInContext(code,context);
const event={preventDefault(){}};
(async()=>{
  get('knowledge-question').value='Atlas';
  await context.searchKnowledge(event);
  assert.equal(calls.filter(call=>call.path.endsWith('/ask')).length,0);
  const rendered=get('knowledge-sources').children[0];
  assert.equal(rendered.children[0].textContent,source.title);
  assert.equal(rendered.children[1].textContent,'来源：'+source.source_uri);
  assert.equal(rendered.children[4].textContent,source.snippet);
  assert(!rendered.children.some(node=>node.tagName==='a'));
  get('knowledge-draft').value='{"status":"abstain","status":"abstain","claims":[]}';
  context.updateKnowledgeButton();
  assert.equal(get('knowledge-answer-button').disabled,false);
  await context.askKnowledge(event);
  const offline=calls.find(call=>call.path.endsWith('/ask')).body;
  assert.equal(offline.allow_model_api,false);
  assert.equal(offline.draft,get('knowledge-draft').value);
  assert.equal(offline.corpus.digest,corpus.digest);
  assert(get('knowledge-answer').children.some(node=>node.textContent.includes('semantic_support=unverified')));
  get('knowledge-question').value='Changed';
  context.updateKnowledgeButton();
  assert.equal(get('knowledge-answer-button').disabled,true);
  await context.askKnowledge(event);
  assert.equal(calls.filter(call=>call.path.endsWith('/ask')).length,1);
  get('knowledge-question').value='Atlas';
  get('knowledge-online').checked=true;
  context.updateKnowledgeButton();
  assert.equal(calls.filter(call=>call.path.endsWith('/ask')).length,1);
  await context.askKnowledge(event);
  const online=calls.filter(call=>call.path.endsWith('/ask'))[1].body;
  assert.equal(online.allow_model_api,true);
  assert.equal(online.draft,null);
  context.renderKnowledgeContext(null);
  assert(get('knowledge-context').textContent.includes('旧运行'));
  context.renderKnowledgeContext(null,'knowledge_evidence_unavailable');
  assert(get('knowledge-context').textContent.includes('快照无法确认'));
  assert(!get('knowledge-context').textContent.includes('旧运行'));
  context.renderKnowledgeContext({refs:[],digest:'a'.repeat(64)});
  assert(get('knowledge-context').textContent.includes('空资料集'));
  context.renderKnowledgeContext(corpus);
  assert(get('knowledge-context').textContent.includes(source.ref.version_id));
  for(const reason of ['knowledge_configuration_changed','knowledge_evidence_unavailable','model_configuration_changed','transcript_unavailable','workspace_contract_changed']){
    assert(context.continuationConfigurationBlocked({recovery_required:true,recovery_reason:reason}));
  }
  context.renderApproval({status:'waiting_approval',recovery_required:true,recovery_reason:'workspace_contract_changed',proposal:{digest:'d'.repeat(64),effects:[]}});
  assert.equal(get('approval-card').classList.contains('hidden'),false);
  assert.equal(get('approve-button').disabled,true);
  assert.equal(get('reject-button').disabled,false);
  assert.equal(get('reject-button').classList.contains('hidden'),false);
  assert(get('messages').children.some(node=>node.textContent.includes('动作契约')));
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        result = subprocess.run(["node", "-e", script], cwd=project, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
