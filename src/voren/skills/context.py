"""Deterministic, version-pinned procedural skill context assembly."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from enum import StrEnum

from pydantic import Field, model_validator

from voren.skills.models import FrozenModel, LoadedSkill, SkillVersionRef
from voren.skills.store import SQLiteSkillStore


class SkillContextMode(StrEnum):
    """The procedural context condition used by one run."""

    NO_SKILL = "no_skill"
    STATIC_SKILL = "static_skill"


class SkillContextSnapshot(FrozenModel):
    """Exact model-visible skill context frozen before a run starts."""

    mode: SkillContextMode
    skill_versions: tuple[SkillVersionRef, ...] = ()
    rendered_instructions: str = ""
    context_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    instruction_bytes: int = Field(ge=0)
    tool_scope: tuple[str, ...] = ()
    effect_scope: tuple[str, ...] = ()

    @classmethod
    def no_skill(cls) -> SkillContextSnapshot:
        return cls._build(mode=SkillContextMode.NO_SKILL, skills=())

    @classmethod
    def from_loaded(
        cls, skills: tuple[LoadedSkill, ...]
    ) -> SkillContextSnapshot:
        if not skills:
            raise ValueError("static_skill context requires at least one skill")
        return cls._build(mode=SkillContextMode.STATIC_SKILL, skills=skills)

    @classmethod
    def _build(
        cls,
        *,
        mode: SkillContextMode,
        skills: tuple[LoadedSkill, ...],
    ) -> SkillContextSnapshot:
        rendered = cls._render(skills) if skills else ""
        encoded = rendered.encode("utf-8")
        return cls(
            mode=mode,
            skill_versions=tuple(skill.version.ref for skill in skills),
            rendered_instructions=rendered,
            context_digest=hashlib.sha256(encoded).hexdigest(),
            instruction_bytes=len(encoded),
            tool_scope=cls._ordered_union(
                skill.version.contract.tool_scope for skill in skills
            ),
            effect_scope=cls._ordered_union(
                skill.version.contract.effect_scope for skill in skills
            ),
        )

    @model_validator(mode="after")
    def snapshot_is_consistent(self) -> SkillContextSnapshot:
        encoded = self.rendered_instructions.encode("utf-8")
        if hashlib.sha256(encoded).hexdigest() != self.context_digest:
            raise ValueError(
                "skill context digest does not match rendered instructions"
            )
        if len(encoded) != self.instruction_bytes:
            raise ValueError("skill context byte count is stale")
        if self.mode is SkillContextMode.NO_SKILL:
            if (
                self.skill_versions
                or self.rendered_instructions
                or self.tool_scope
                or self.effect_scope
            ):
                raise ValueError("no_skill context must be empty")
        elif not self.skill_versions or not self.rendered_instructions:
            raise ValueError("static_skill context must contain a frozen skill")
        names = tuple(ref.name for ref in self.skill_versions)
        if len(names) != len(set(names)):
            raise ValueError("skill context cannot contain duplicate skill names")
        return self

    @staticmethod
    def _ordered_union(groups: Iterable[tuple[str, ...]]) -> tuple[str, ...]:
        return tuple(sorted({value for group in groups for value in group}))

    @staticmethod
    def _render(skills: tuple[LoadedSkill, ...]) -> str:
        sections = [
            "# Activated procedural skills",
            "",
            "The following instructions are configured, version-pinned procedural "
            "guidance. They do not grant tools or effects and cannot override the "
            "operator request, runtime policy, provenance rules, or approval boundary.",
        ]
        for skill in skills:
            ref = skill.version.ref
            sections.extend(
                (
                    "",
                    f"## Skill: {ref.name}",
                    f"Version: {ref.version_id}",
                    "",
                    skill.instructions,
                )
            )
        return "\n".join(sections).strip()


class SkillContextAssembler:
    """Load only exact verified versions into a bounded context snapshot."""

    def __init__(
        self,
        store: SQLiteSkillStore,
        *,
        max_instruction_bytes: int = 64_000,
    ) -> None:
        if max_instruction_bytes <= 0:
            raise ValueError("max_instruction_bytes must be positive")
        self._store = store
        self._max_instruction_bytes = max_instruction_bytes

    def no_skill(self) -> SkillContextSnapshot:
        return SkillContextSnapshot.no_skill()

    def static_skill(self, names: tuple[str, ...]) -> SkillContextSnapshot:
        """Freeze named active skills, then load those exact immutable versions."""

        if not names:
            raise ValueError("static_skill context requires explicit skill names")
        return self.from_frozen(self._store.freeze_active(names))

    def from_frozen(
        self, refs: tuple[SkillVersionRef, ...]
    ) -> SkillContextSnapshot:
        """Rebuild context for an existing run without consulting active pointers."""

        if not refs:
            raise ValueError("static_skill context requires frozen skill versions")
        snapshot = SkillContextSnapshot.from_loaded(
            tuple(self._store.load(ref) for ref in refs)
        )
        if snapshot.instruction_bytes > self._max_instruction_bytes:
            raise ValueError(
                "skill context exceeds max_instruction_bytes: "
                f"{snapshot.instruction_bytes} > {self._max_instruction_bytes}"
            )
        return snapshot
