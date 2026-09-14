# Phase 3 AgentDojo Skill Evaluator

[简体中文](PHASE3_AGENTDOJO_SKILL_EVALUATOR.zh-CN.md)

## Closed integration gap

The generic paired runner does not by itself prove that either Skill was shown
to a model. `AgentDojoSkillEvaluator` closes that gap for the controlled
workspace environment.

For each `(held-out case, exact SkillVersionRef)` pair it:

1. integrity-loads that immutable Skill version;
2. builds a bounded `SkillContextSnapshot` without consulting the active pointer;
3. includes the exact rendered context in the system-prompt digest;
4. freezes the version in `RunConfig.skill_versions`;
5. runs the case in raw `agent_behavior` mode;
6. writes the complete underlying `ExperimentArtifact` atomically; and
7. returns a measurement referencing that artifact digest.

Raw behavior mode is required here because runtime enforcement can block a bad
external effect and hide the candidate's underlying prompt-injection behavior.
The separate Phase 1 evaluation remains responsible for reporting enforcement
effectiveness.

## Reproducibility and failure semantics

Each underlying config also binds the AgentDojo manifest, prompt, tool schema,
attack template, provider/model labels, sampling configuration, source revision,
and code-dirty flag. Artifact filenames include their own digest, so rerunning a
trial cannot silently overwrite evidence already referenced by a candidate
decision.

Provider/model-adapter and read-adapter failures, plus provider/deadline
cancellation, are translated into explicit infrastructure failures only after
the underlying artifact is written. The paired policy therefore does not count
them as utility or security regressions.

## Verification

The integration test uses the real pinned AgentDojo environment and a scripted
model. It checks that base and candidate requests contain their different exact
instructions, each run records one frozen Skill version, two underlying
artifacts are readable and integrity-valid, and the paired artifact references
their exact digests. It does not claim live-model improvement.

`voren skill eval-agentdojo` exposes this path without combining it with a
decision. It rejects duplicate selections and incomplete security/utility
coverage before any model call, requires the suite declared by the Skill
contract, writes all artifacts, prints both sides of each pair, and leaves the
candidate staged.
