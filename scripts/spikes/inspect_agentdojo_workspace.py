#!/usr/bin/env python3
"""Reproduce Voren's AgentDojo workspace Phase 0 observations.

This is a research spike, not the future Voren adapter. It intentionally uses
AgentDojo internals so that changes in the upstream API fail visibly.
"""

from __future__ import annotations

import argparse
import json
from importlib.metadata import version
from typing import Any

EXPECTED_DISTRIBUTION_VERSION = "0.1.35"
BENCHMARK_VERSION = "v1.2.2"
SUITE_NAME = "workspace"


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _load_suite():
    # Importing the suite loader first avoids an upstream circular-import edge
    # when workspace internals are imported before suite registration.
    from agentdojo.task_suite.load_suites import get_suite

    distribution_version = version("agentdojo")
    if distribution_version != EXPECTED_DISTRIBUTION_VERSION:
        raise RuntimeError(
            "AgentDojo version mismatch: "
            f"expected {EXPECTED_DISTRIBUTION_VERSION}, got {distribution_version}"
        )
    return get_suite(BENCHMARK_VERSION, SUITE_NAME)


def _run_user_ground_truth(suite, task_id: str) -> bool:
    from agentdojo.agent_pipeline.ground_truth_pipeline import GroundTruthPipeline

    task = suite.get_user_task_by_id(task_id)
    utility, _ = suite.run_task_with_pipeline(
        GroundTruthPipeline(task),
        task,
        injection_task=None,
        injections={},
        environment=suite.load_and_inject_default_environment({}),
    )
    return utility


def _inspect_golden_task(suite) -> dict[str, Any]:
    from agentdojo.agent_pipeline.ground_truth_pipeline import GroundTruthPipeline
    from agentdojo.default_suites.v1.workspace.task_suite import WorkspaceDeepDiff
    from agentdojo.functions_runtime import FunctionsRuntime

    task = suite.get_user_task_by_id("user_task_18")
    environment = task.init_environment(suite.load_and_inject_default_environment({}))
    pre_environment = environment.model_copy(deep=True)
    runtime = FunctionsRuntime(suite.tools)
    _, _, post_environment, _, _ = GroundTruthPipeline(task).query(
        task.PROMPT, runtime, environment
    )
    diff = WorkspaceDeepDiff(pre_environment, post_environment)

    event_id = pre_environment.calendar._get_next_id()
    email_id = pre_environment.inbox._get_next_id()
    event = post_environment.calendar.events[event_id]
    email = post_environment.inbox.emails[email_id]

    return {
        "task_id": task.ID,
        "utility": _run_user_ground_truth(suite, task.ID),
        "tool_calls": [
            {"name": call.function, "arguments": call.args}
            for call in task.ground_truth(pre_environment)
        ],
        "state_delta": {
            "categories": list(diff.keys()),
            "added_paths": sorted(map(str, diff.get("dictionary_item_added", []))),
        },
        "created_event": {
            "id": str(event.id_),
            "title": event.title,
            "location": event.location,
            "start_time": event.start_time,
            "end_time": event.end_time,
            "participants": sorted(map(str, event.participants)),
        },
        "invitation_email": {
            "id": str(email.id_),
            "subject": email.subject,
            "recipients": sorted(map(str, email.recipients)),
            "attachment_count": len(email.attachments),
        },
    }


def _inspect_tool_effects(suite) -> dict[str, Any]:
    from agentdojo.default_suites.v1.workspace.task_suite import WorkspaceDeepDiff
    from agentdojo.functions_runtime import FunctionsRuntime

    runtime = FunctionsRuntime(suite.tools)

    unread_environment = suite.load_and_inject_default_environment({})
    unread_pre = unread_environment.model_copy(deep=True)
    unread_result, _ = runtime.run_function(
        unread_environment, "get_unread_emails", {}, raise_on_error=True
    )
    unread_diff = WorkspaceDeepDiff(unread_pre, unread_environment)

    participant_environment = suite.load_and_inject_default_environment({})
    participant_pre = participant_environment.model_copy(deep=True)
    event_id = next(iter(participant_environment.calendar.events))
    runtime.run_function(
        participant_environment,
        "add_calendar_event_participants",
        {"event_id": str(event_id), "participants": ["phase0@example.com"]},
        raise_on_error=True,
    )
    participant_diff = WorkspaceDeepDiff(participant_pre, participant_environment)

    return {
        "get_unread_emails": {
            "returned_email_count": len(unread_result),
            "observed_delta_categories": list(unread_diff.keys()),
            "changed_paths": sorted(unread_diff.get("values_changed", {}).keys()),
            "classification": "stateful_read",
        },
        "add_calendar_event_participants": {
            "observed_delta": participant_diff.to_dict(),
            "outbound_email_count_delta": len(participant_environment.inbox.emails)
            - len(participant_pre.inbox.emails),
            "contract_note": (
                "Upstream docstring says new participants are emailed, but the "
                "observed implementation only changes the calendar event."
            ),
        },
    }


def _run_full_ground_truth_checks(suite) -> dict[str, Any]:
    from agentdojo.agent_pipeline.ground_truth_pipeline import GroundTruthPipeline

    user_results = {
        task_id: _run_user_ground_truth(suite, task_id)
        for task_id in suite.user_tasks
    }

    default_environment = suite.load_and_inject_default_environment({})
    reference_user_task = next(iter(suite.user_tasks.values()))
    injection_results: dict[str, bool | None] = {}
    for task_id, injection_task in suite.injection_tasks.items():
        ground_truth = injection_task.ground_truth(default_environment.model_copy(deep=True))
        if not ground_truth:
            injection_results[task_id] = None
            continue
        _, attack_goal_achieved = suite.run_task_with_pipeline(
            GroundTruthPipeline(injection_task),
            reference_user_task,
            injection_task=injection_task,
            injections={},
            environment=default_environment.model_copy(deep=True),
        )
        injection_results[task_id] = attack_goal_achieved

    return {
        "user_ground_truth": {
            "passed": sum(user_results.values()),
            "total": len(user_results),
            "failures": [task_id for task_id, passed in user_results.items() if not passed],
        },
        "injection_ground_truth": {
            "passed": sum(result is True for result in injection_results.values()),
            "implemented": sum(result is not None for result in injection_results.values()),
            "total": len(injection_results),
            "failures": [
                task_id for task_id, result in injection_results.items() if result is False
            ],
            "not_implemented": [
                task_id for task_id, result in injection_results.items() if result is None
            ],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--full-suite",
        action="store_true",
        help="also run every available user and implemented injection ground truth",
    )
    args = parser.parse_args()

    suite = _load_suite()
    report: dict[str, Any] = {
        "agentdojo_distribution_version": version("agentdojo"),
        "benchmark_version": BENCHMARK_VERSION,
        "suite": SUITE_NAME,
        "tool_count": len(suite.tools),
        "user_task_count": len(suite.user_tasks),
        "injection_task_count": len(suite.injection_tasks),
        "golden_task": _inspect_golden_task(suite),
        "tool_effect_observations": _inspect_tool_effects(suite),
    }
    if args.full_suite:
        report["ground_truth_checks"] = _run_full_ground_truth_checks(suite)

    print(json.dumps(report, indent=2, ensure_ascii=False, default=_json_default))


if __name__ == "__main__":
    main()
