# Phase 2 Deterministic Skill Routing

[简体中文](PHASE2_SKILL_ROUTING.zh-CN.md)

## Boundary

Runtime Skill selection now connects an operator request to an already active,
reviewed Skill. It is deliberately not another model call and does not inspect
email, calendar, knowledge, or tool-observation content.

An active Skill opts into automatic selection with a comma-separated
`metadata.routing-keywords` value in `SKILL.md`. The router normalizes the
operator request, matches only those configured phrases, and ranks matches by
the total matched phrase length. Automatic mode loads a Skill only when there
is one uniquely best compatible result. A tie fails closed to `no_skill`.

## Capability check before instruction loading

The router reads active version metadata and `skill.yaml` contracts, but not
the instruction body. It removes every candidate whose `tool_scope` is not a
subset of the current workspace's real tool names. Only after exact version
references have been selected does `SkillContextAssembler` integrity-check and
load their instructions.

This ordering means routing cannot grant a tool. For example, the checked-in
AgentDojo scheduling Skill is not silently reused by the narrower Google
workspace because its action names and effect contract differ.

## Reproducibility and operator control

`voren agentdojo` and `voren google` use conservative automatic routing by
default. `--skill NAME` explicitly freezes reviewed active versions, while
`--no-skill` disables routing for a baseline run. The two options are mutually
exclusive.

Every decision contains only request-free evidence: a SHA-256 request digest,
available tool names, exact selected versions, matched configured keywords,
incompatible candidates, ambiguity, and a canonical decision digest. That
record is stored in `RunConfig.metadata.skill_routing`; selected versions are
also independently frozen in `RunConfig.skill_versions` and emitted by the
existing metadata-only `skill_context.assembled` event.

This is deterministic intent routing, not a claim of embedding-based semantic
retrieval. Adding probabilistic routing would require a separate evaluated
policy and is outside the current project acceptance boundary.
