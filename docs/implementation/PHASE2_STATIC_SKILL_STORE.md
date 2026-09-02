# Phase 2 Static Skill Store

[中文版](PHASE2_STATIC_SKILL_STORE.zh-CN.md)

## Scope

This slice establishes the durable representation needed before Voren can put a
skill into model context or learn a candidate skill. It implements:

- strict discovery of direct-child Agent Skills directories;
- progressive metadata, instruction, and resource loading APIs;
- a required Voren `skill.yaml` execution/evaluation sidecar;
- content-addressed immutable versions;
- an explicit atomic active-version pointer; and
- exact skill-version references in the frozen `RunConfig`.

The checked-in `skills/schedule-from-email` package is the first manually
written static scheduling skill.

## Portable format and Voren extension

The outer package follows the [official Agent Skills specification](https://agentskills.io/specification): a directory
name matching the `name` in `SKILL.md`, required `name` and `description` YAML
frontmatter, optional standard metadata, Markdown instructions, and optional
resource directories.

Voren adds `skill.yaml` without changing `SKILL.md`. The sidecar records:

- semantic scope;
- the maximum tool scope requested by this skill;
- expected external-effect kinds; and
- evaluation suites that should cover the skill.

These fields do not grant authority. The runtime's configured tools, action
gateway, policy, and exact approval can only narrow the sidecar scope. In
particular, the experimental Agent Skills `allowed-tools` frontmatter field is
preserved as `allowed_tools_hint` for interoperability but is never translated
into a Voren runtime permission.

## Progressive disclosure

The APIs expose three distinct context surfaces:

1. `discover_active()` returns only the exact version reference and routing
   description.
2. `load(ref)` returns the full `SKILL.md` instructions and a list of available
   resource paths.
3. `load_resource(ref, path)` returns one integrity-checked resource on demand.

The implementation currently verifies the complete package from disk at load
time, but only the selected layer is returned to the caller and therefore added
to model context.

## Immutability and activation

Installation computes a canonical digest from every relative file path, file
digest, and byte length. The complete validated package is copied to:

```text
objects/<content-digest>/<skill-name>/
```

The content digest is also the version ID. Reinstalling identical bytes is
idempotent; changed instructions, sidecar, or resources always create a new
version. A package is installed inactive. `activate(ref, reason=...)` changes a
single SQLite pointer atomically and never rewrites an old object.

`freeze_active()` returns exact `SkillVersionRef` values. A run stores those
references in `RunConfig`, so changing the active pointer later changes new-run
digests without changing an already frozen run. Old Phase 1 configs with no
skills retain their previous config digest representation.

## Admission and integrity checks

The parser rejects:

- invalid or mismatched skill names;
- unsupported frontmatter and malformed sidecars;
- non-string portable metadata;
- symlinks and non-regular package entries;
- path escapes; and
- excessive per-file, package, or file-count sizes.

Installed objects are re-parsed and content-address checked when activated
content or a resource is loaded. File-system permissions are not treated as a
security boundary: external mutation remains possible, but it is detected
before content enters model context.

## Current boundary

This storage slice deliberately does not itself:

- implement Profile or Episode stores;
- select skills semantically;
- create model-written candidates; or
- evaluate, promote, reject, or roll back learned versions.

The following [version-pinned context slice](PHASE2_SKILL_CONTEXT.md) now injects
an explicitly selected, frozen version into `AgentLoop`. The foreground loop
still has no skill-store write path, so tool observations cannot install or
activate a skill.
