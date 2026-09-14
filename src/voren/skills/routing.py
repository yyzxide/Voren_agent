"""Conservative, metadata-only routing for active procedural Skills."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from enum import StrEnum

from pydantic import Field, model_validator

from voren.skills.models import FrozenModel, SkillVersionRef
from voren.skills.parser import SkillFormatError
from voren.skills.store import SQLiteSkillStore


ROUTING_KEYWORDS_METADATA = "routing-keywords"


class SkillRoutingError(ValueError):
    """Raised when an explicit runtime Skill selection is not safe to load."""


class SkillRoutingMode(StrEnum):
    AUTO = "auto"
    EXPLICIT = "explicit"
    DISABLED = "disabled"


class SkillRouteMatch(FrozenModel):
    ref: SkillVersionRef
    matched_keywords: tuple[str, ...] = Field(min_length=1)
    score: int = Field(gt=0)


class IncompatibleSkill(FrozenModel):
    ref: SkillVersionRef
    missing_tools: tuple[str, ...] = Field(min_length=1)


class SkillRouteDecision(FrozenModel):
    """Request-free routing evidence safe to persist with a RunConfig."""

    mode: SkillRoutingMode
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    available_tools: tuple[str, ...]
    selected_versions: tuple[SkillVersionRef, ...] = ()
    matches: tuple[SkillRouteMatch, ...] = ()
    incompatible_skills: tuple[IncompatibleSkill, ...] = ()
    ambiguous_skills: tuple[str, ...] = ()
    decision_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def build(
        cls,
        *,
        mode: SkillRoutingMode,
        request: str,
        available_tools: tuple[str, ...],
        selected_versions: tuple[SkillVersionRef, ...] = (),
        matches: tuple[SkillRouteMatch, ...] = (),
        incompatible_skills: tuple[IncompatibleSkill, ...] = (),
        ambiguous_skills: tuple[str, ...] = (),
    ) -> SkillRouteDecision:
        payload = {
            "mode": mode.value,
            "request_digest": cls.digest_request(request),
            "available_tools": sorted(set(available_tools)),
            "selected_versions": [
                ref.model_dump(mode="json") for ref in selected_versions
            ],
            "matches": [match.model_dump(mode="json") for match in matches],
            "incompatible_skills": [
                item.model_dump(mode="json") for item in incompatible_skills
            ],
            "ambiguous_skills": list(ambiguous_skills),
        }
        return cls(
            **payload,
            decision_digest=cls._digest_payload(payload),
        )

    @model_validator(mode="after")
    def decision_is_canonical(self) -> SkillRouteDecision:
        payload = self.model_dump(mode="json", exclude={"decision_digest"})
        if self.decision_digest != self._digest_payload(payload):
            raise ValueError("skill routing decision digest is stale")
        selected_names = tuple(ref.name for ref in self.selected_versions)
        if len(selected_names) != len(set(selected_names)):
            raise ValueError("skill routing cannot select duplicate names")
        if self.ambiguous_skills and self.selected_versions:
            raise ValueError("ambiguous automatic routing cannot select a Skill")
        if self.mode is SkillRoutingMode.DISABLED and (
            self.selected_versions or self.matches or self.ambiguous_skills
        ):
            raise ValueError("disabled routing must not select or match Skills")
        return self

    @staticmethod
    def digest_request(request: str) -> str:
        normalized = unicodedata.normalize("NFKC", request.strip())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _digest_payload(payload: dict) -> str:
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SkillRouter:
    """Select at most one uniquely best compatible Skill in automatic mode.

    Automatic routing reads only immutable metadata and contracts. Full Skill
    instructions are loaded later, after exact versions have been selected.
    """

    def __init__(self, store: SQLiteSkillStore) -> None:
        self._store = store

    def disabled(
        self, *, request: str, available_tools: tuple[str, ...]
    ) -> SkillRouteDecision:
        return SkillRouteDecision.build(
            mode=SkillRoutingMode.DISABLED,
            request=request,
            available_tools=available_tools,
        )

    def explicit(
        self,
        *,
        request: str,
        available_tools: tuple[str, ...],
        names: tuple[str, ...],
    ) -> SkillRouteDecision:
        if not names:
            raise SkillRoutingError("explicit routing requires at least one Skill")
        if len(names) != len(set(names)):
            raise SkillRoutingError("explicit Skill names must be unique")
        try:
            refs = self._store.freeze_active(tuple(sorted(names)))
        except SkillFormatError as error:
            raise SkillRoutingError(str(error)) from error
        available = set(available_tools)
        incompatible = self._incompatible(refs, available)
        if incompatible:
            details = "; ".join(
                f"{item.ref.name}: {', '.join(item.missing_tools)}"
                for item in incompatible
            )
            raise SkillRoutingError(
                "explicit Skills request unavailable workspace tools: " + details
            )
        return SkillRouteDecision.build(
            mode=SkillRoutingMode.EXPLICIT,
            request=request,
            available_tools=available_tools,
            selected_versions=refs,
        )

    def auto(
        self, *, request: str, available_tools: tuple[str, ...]
    ) -> SkillRouteDecision:
        normalized_request = self._normalize(request)
        available = set(available_tools)
        matches: list[SkillRouteMatch] = []
        incompatible: list[IncompatibleSkill] = []
        for summary in self._store.discover_active():
            version = self._store.get_version(summary.ref)
            missing = tuple(sorted(set(version.contract.tool_scope) - available))
            if missing:
                incompatible.append(
                    IncompatibleSkill(ref=version.ref, missing_tools=missing)
                )
                continue
            keywords = self._routing_keywords(
                version.metadata.metadata.get(ROUTING_KEYWORDS_METADATA, "")
            )
            matched = tuple(
                keyword
                for keyword in keywords
                if self._keyword_matches(normalized_request, keyword)
            )
            if matched:
                matches.append(
                    SkillRouteMatch(
                        ref=version.ref,
                        matched_keywords=matched,
                        score=sum(
                            len(keyword.replace(" ", "")) for keyword in matched
                        ),
                    )
                )

        ranked = tuple(
            sorted(matches, key=lambda item: (-item.score, item.ref.name))
        )
        selected: tuple[SkillVersionRef, ...] = ()
        ambiguous: tuple[str, ...] = ()
        if ranked:
            best_score = ranked[0].score
            best = tuple(item for item in ranked if item.score == best_score)
            if len(best) == 1:
                selected = (best[0].ref,)
            else:
                ambiguous = tuple(item.ref.name for item in best)
        return SkillRouteDecision.build(
            mode=SkillRoutingMode.AUTO,
            request=request,
            available_tools=available_tools,
            selected_versions=selected,
            matches=ranked,
            incompatible_skills=tuple(
                sorted(incompatible, key=lambda item: item.ref.name)
            ),
            ambiguous_skills=ambiguous,
        )

    def _incompatible(
        self,
        refs: tuple[SkillVersionRef, ...],
        available: set[str],
    ) -> tuple[IncompatibleSkill, ...]:
        incompatible: list[IncompatibleSkill] = []
        for ref in refs:
            version = self._store.get_version(ref)
            missing = tuple(sorted(set(version.contract.tool_scope) - available))
            if missing:
                incompatible.append(
                    IncompatibleSkill(ref=ref, missing_tools=missing)
                )
        return tuple(incompatible)

    @classmethod
    def _routing_keywords(cls, raw: str) -> tuple[str, ...]:
        keywords: list[str] = []
        for value in re.split(r"[,\n]", raw):
            normalized = cls._normalize(value)
            if len(normalized) >= 2 and normalized not in keywords:
                keywords.append(normalized)
        return tuple(keywords)

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", value).casefold().split())

    @staticmethod
    def _keyword_matches(request: str, keyword: str) -> bool:
        if any("\u4e00" <= character <= "\u9fff" for character in keyword):
            return keyword in request
        words = keyword.split()
        pattern = r"(?<![a-z0-9])" + r"\s+".join(
            re.escape(word) for word in words
        ) + r"(?![a-z0-9])"
        return re.search(pattern, request) is not None
