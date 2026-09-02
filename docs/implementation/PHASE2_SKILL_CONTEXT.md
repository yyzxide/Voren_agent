# Phase 2 Version-Pinned Skill Context

This slice connects the immutable Skill Store to the model boundary without
turning skill prose into runtime authority.

## Context conditions

`SkillContextAssembler` produces one immutable `SkillContextSnapshot` before a
run starts:

- `no_skill` is an explicit empty baseline;
- `static_skill` requires explicit active skill names, freezes their exact
  `SkillVersionRef` values, verifies the content-addressed packages, and then
  renders their instructions.

`from_frozen()` reconstructs a previous snapshot from exact version references.
It never consults the current active pointer, so replacing an active skill does
not change an existing or resumed run.

The rendered block states that skill text is configured, version-pinned
procedural guidance. It
cannot override the operator request, provenance handling, runtime policy, or
the approval boundary. The block has a deterministic digest and a 64 KB default
size limit.

## Runtime invariants

Before creating a run, `AgentLoop` requires the snapshot's exact version refs to
match `RunConfig.skill_versions`. During loop construction it also verifies that
the skill contract's requested tool scope is a subset of the tools the runtime
actually registered. A skill can therefore describe how to use a capability,
but cannot create one.

For a new run, the append-only trace records `skill_context.assembled` with:

- the context condition and exact version references;
- context digest and byte count;
- declared tool/effect scopes; and
- the `configured_static` source/trust label.

The event deliberately excludes instruction prose. Full model context remains
only in the encrypted transcript checkpoint. The `no_skill` condition preserves
the original Phase 1 system instruction byte-for-byte.

## Integration bug caught by this boundary

The first checked-in skill originally requested `search_calendar_events`, while
the runtime exposes `get_day_calendar_events`. Store-only tests could not detect
that mismatch. The new capability check rejected the skill before a model call,
and the sidecar was corrected to the registered tool name.

## Current boundary

This slice makes `no_skill` and `static_skill` runtime inputs deterministic and
testable. Evaluation artifacts do not yet run both skill conditions as one
paired experiment, and semantic routing, Profile/Episode context, candidate
generation, and promotion gates remain later slices.
