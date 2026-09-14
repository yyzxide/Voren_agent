"""FastAPI and SSE surface for a local, single-operator Voren deployment."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterable
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from fastapi.staticfiles import StaticFiles

from voren.adapters.agentdojo_workspace import AgentDojoDependencyError
from voren.adapters.google_workspace import GoogleWorkspaceConfigurationError
from voren.providers.openai_responses import (
    ModelConfigurationError,
    OpenAIResponsesModelAdapter,
)
from voren.runtime.models import RuntimeResultStatus
from voren.skills.routing import SkillRoutingMode
from voren.web.demo_model import DemoWorkspaceModel
from voren.web.index import (
    WebRequestConflictError,
    WebRequestInProgressError,
    WebRunNotFoundError,
)
from voren.web.models import (
    CreateRunRequest,
    DecideRunRequest,
    HealthView,
    RunView,
)
from voren.web.service import (
    VorenWebService,
    WebApprovalRecoveryRequiredError,
    WebDecisionConflictError,
    create_agentdojo_web_workspace,
    create_google_web_workspace,
)


TERMINAL_STATUSES = {
    RuntimeResultStatus.COMPLETED.value,
    RuntimeResultStatus.FAILED.value,
    RuntimeResultStatus.CANCELLED.value,
    RuntimeResultStatus.LIMIT_EXCEEDED.value,
    "needs_reconciliation",
}


def create_app(
    *,
    service: VorenWebService | None = None,
    mode: str | None = None,
    workspace: str | None = None,
    database: Path | None = None,
) -> FastAPI:
    selected_mode = (mode or os.environ.get("VOREN_WEB_MODE") or "demo").strip()
    if selected_mode not in {"demo", "live"}:
        raise ValueError("VOREN_WEB_MODE must be 'demo' or 'live'")
    selected_database = database or Path(
        os.environ.get("VOREN_WEB_DATABASE", ".voren/web.sqlite3")
    )
    selected_knowledge_database = Path(
        os.environ.get("VOREN_KNOWLEDGE_DATABASE", ".voren/voren.sqlite3")
    )
    selected_skill_database = Path(
        os.environ.get("VOREN_SKILL_DATABASE", ".voren/voren.sqlite3")
    )
    selected_skill_store = Path(
        os.environ.get("VOREN_SKILL_STORE", ".voren/skills")
    )
    routing_value = os.environ.get("VOREN_WEB_SKILL_ROUTING", "auto").strip()
    if routing_value not in {"auto", "disabled"}:
        raise ValueError("VOREN_WEB_SKILL_ROUTING must be 'auto' or 'disabled'")
    selected_workspace = (
        workspace or os.environ.get("VOREN_WEB_WORKSPACE") or "agentdojo"
    ).strip()
    if selected_workspace not in {"agentdojo", "google"}:
        raise ValueError("VOREN_WEB_WORKSPACE must be 'agentdojo' or 'google'")
    if service is None:
        if selected_workspace == "google" and selected_mode != "live":
            raise ValueError("the Google Web workspace requires VOREN_WEB_MODE=live")
        workspace_factory = (
            create_google_web_workspace
            if selected_workspace == "google"
            else create_agentdojo_web_workspace
        )
        active_service = VorenWebService(
            database=selected_database,
            model_factory=_model_factory(selected_mode),
            mode=selected_mode,
            workspace_factory=workspace_factory,
            workspace_name=selected_workspace,
            workspace_recoverable=selected_workspace == "google",
            knowledge_database=selected_knowledge_database,
            skill_database=selected_skill_database,
            skill_store_root=selected_skill_store,
            skill_routing_mode=SkillRoutingMode(routing_value),
        )
    else:
        active_service = service
    static_root = Path(__file__).with_name("static")
    app = FastAPI(
        title="Voren Agent",
        version="0.1.0",
        description=(
            "Local controlled email/calendar Agent with exact-effect approval."
        ),
    )
    app.state.voren_service = active_service
    app.mount("/static", StaticFiles(directory=static_root), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(static_root / "index.html")

    @app.get("/api/health", response_model=HealthView)
    def health() -> HealthView:
        return HealthView(
            mode=active_service.mode,
            workspace=active_service.workspace_name,
            workspace_configured=_workspace_configured(
                active_service.workspace_name
            ),
            writes_target=(
                "google_draft_or_private_calendar_hold"
                if active_service.workspace_name == "google"
                else "controlled_agentdojo_workspace"
            ),
            live_model_configured=_live_model_configured(),
            knowledge_database=str(active_service.knowledge_database),
            skill_routing_mode=active_service.skill_routing_mode.value,
            active_skill_count=active_service.active_skill_count(),
            skill_database=str(active_service.skill_database),
        )

    @app.post("/api/runs", response_model=RunView)
    def create_run(payload: CreateRunRequest) -> RunView:
        try:
            return active_service.submit(payload)
        except WebRequestConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except WebRequestInProgressError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (
            ModelConfigurationError,
            AgentDojoDependencyError,
            GoogleWorkspaceConfigurationError,
        ) as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @app.get("/api/runs/{run_id}", response_model=RunView)
    def get_run(run_id: str) -> RunView:
        try:
            return active_service.get(run_id)
        except WebRunNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except WebRequestInProgressError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/api/runs/{run_id}/decision", response_model=RunView)
    def decide_run(run_id: str, payload: DecideRunRequest) -> RunView:
        try:
            return active_service.decide(run_id, payload)
        except WebRunNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (
            WebDecisionConflictError,
            WebApprovalRecoveryRequiredError,
        ) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except GoogleWorkspaceConfigurationError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @app.get("/api/runs/{run_id}/events")
    def list_events(
        run_id: str,
        after: int = Query(default=0, ge=0),
    ) -> dict:
        try:
            events = active_service.events(run_id, after=after)
        except WebRunNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {
            "events": [event.model_dump(mode="json") for event in events]
        }

    @app.get(
        "/api/runs/{run_id}/events/stream",
        response_class=EventSourceResponse,
    )
    async def stream_events(
        request: Request,
        run_id: str,
        after: int = Query(default=0, ge=0),
    ) -> AsyncIterable[ServerSentEvent]:
        try:
            active_service.get(run_id)
        except WebRunNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

        cursor = after
        while True:
            for event in active_service.events(run_id, after=cursor):
                cursor = event.sequence
                yield ServerSentEvent(
                    data=event.model_dump(mode="json"),
                    event=event.event_type.value,
                    id=str(event.sequence),
                )
            view = active_service.get(run_id)
            if view.status in TERMINAL_STATUSES or await request.is_disconnected():
                return
            await asyncio.sleep(0.25)

    return app


def _model_factory(mode: str):
    if mode == "demo":
        return DemoWorkspaceModel

    def create_live_model() -> OpenAIResponsesModelAdapter:
        model = os.environ.get("VOREN_MODEL")
        if not model:
            raise ModelConfigurationError(
                "set VOREN_MODEL before starting live web mode"
            )
        return OpenAIResponsesModelAdapter.from_environment(
            model=model,
            base_url=os.environ.get("OPENAI_BASE_URL"),
            provider_profile=os.environ.get("VOREN_RESPONSES_PROFILE"),
        )

    return create_live_model


def _live_model_configured() -> bool:
    profile = os.environ.get("VOREN_RESPONSES_PROFILE", "openai")
    has_key = bool(
        os.environ.get("DEEPSEEK_API_KEY")
        if profile == "deepseek"
        else os.environ.get("OPENAI_API_KEY")
    )
    return bool(os.environ.get("VOREN_MODEL")) and has_key


def _workspace_configured(workspace: str) -> bool:
    if workspace == "google":
        return bool(
            os.environ.get("GOOGLE_WORKSPACE_ACCESS_TOKEN")
            and os.environ.get("VOREN_GOOGLE_ACCOUNT_EMAIL")
        )
    return True


def main() -> None:
    host = os.environ.get("VOREN_WEB_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError(
            "Voren Web has no multi-user authentication; bind only to loopback"
        )
    uvicorn.run(
        create_app(),
        host=host,
        port=int(os.environ.get("VOREN_WEB_PORT", "8080")),
        log_level="info",
    )


if __name__ == "__main__":
    main()
