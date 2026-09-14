"""Credential-free deterministic planner for the local product walkthrough."""

from __future__ import annotations

from voren.runtime.cancellation import CancellationToken, ModelRequestCancelled
from voren.runtime.models import (
    CancellationReason,
    MessageRole,
    ModelMessage,
    ModelResponse,
    ToolCall,
    ToolDefinition,
)


class DemoWorkspaceModel:
    """Exercise the real loop without presenting scripted output as model quality."""

    def complete(
        self,
        *,
        messages: tuple[ModelMessage, ...],
        tools: tuple[ToolDefinition, ...],
        cancellation: CancellationToken,
    ) -> ModelResponse:
        if cancellation.cancelled:
            raise ModelRequestCancelled(
                reason=cancellation.reason or CancellationReason.OPERATOR,
                provider_confirmed=False,
            )
        request = next(
            message.content
            for message in messages
            if message.role is MessageRole.USER
        )
        assert isinstance(request, str)
        lowered = request.casefold()
        tool_messages = tuple(
            message for message in messages if message.role is MessageRole.TOOL
        )
        available = {tool.name for tool in tools}

        if lowered.strip() in {"你好", "hello", "hi", "嗨"}:
            return ModelResponse(
                text=(
                    "你好，我是 Voren。本页当前运行无需 API Key 的确定性演示；"
                    "可使用下方示例体验检索、动作提议、精确审批与回执核验。"
                )
            )

        scheduling = any(
            phrase in lowered
            for phrase in (
                "schedule",
                "create a calendar",
                "安排日程",
                "创建日程",
                "添加日程",
                "安排徒步",
            )
        )
        if scheduling:
            if not tool_messages:
                return ModelResponse(
                    tool_calls=(
                        ToolCall(
                            call_id="demo-search-email",
                            name="search_emails",
                            arguments={"query": "hiking"},
                        ),
                    )
                )
            if len(tool_messages) == 1:
                return ModelResponse(
                    tool_calls=(
                        ToolCall(
                            call_id="demo-check-calendar",
                            name="get_day_calendar_events",
                            arguments={"day": "2024-05-18"},
                        ),
                    )
                )
            return ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="demo-create-event",
                        name="create_calendar_event",
                        arguments={
                            "title": "Hiking Trip",
                            "start_time": "2024-05-18 08:00",
                            "end_time": "2024-05-18 13:00",
                            "participants": ["mark.davies@hotmail.com"],
                            "location": "island trailhead",
                        },
                    ),
                )
            )

        if "search_meeting_knowledge" in available and not tool_messages:
            return ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="demo-search-knowledge",
                        name="search_meeting_knowledge",
                        arguments={"query": request, "limit": 3},
                    ),
                )
            )
        if tool_messages:
            content = tool_messages[-1].content
            if isinstance(content, dict) and content.get("items"):
                first = content["items"][0]
                data = first.get("data", {})
                return ModelResponse(
                    text=(
                        f"检索到：{data.get('snippet', '')}\n"
                        f"来源：{data.get('source_uri', 'unknown')}\n"
                        f"版本：{data.get('ref', {}).get('version_id', 'unknown')}"
                    )
                )
            return ModelResponse(text="没有找到与该请求匹配的已激活知识文档。")
        return ModelResponse(
            text=(
                "确定性演示已完成来源绑定检索。请展开运行轨迹查看 Observation；"
                "配置 Live 模式后，真实模型会基于同一份结构化结果生成回答。"
            )
        )
