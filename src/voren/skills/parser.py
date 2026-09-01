"""Strict Agent Skills parser with bounded, symlink-free package ingestion."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from voren.skills.models import (
    AgentSkillMetadata,
    SkillContract,
    SkillFile,
    SkillPackage,
)


FRONTMATTER_KEYS = {
    "name",
    "description",
    "license",
    "compatibility",
    "metadata",
    "allowed-tools",
}


class SkillFormatError(ValueError):
    """Raised when a skill violates the portable format or Voren bounds."""


class AgentSkillParser:
    def __init__(
        self,
        *,
        max_files: int = 100,
        max_file_bytes: int = 1_000_000,
        max_package_bytes: int = 5_000_000,
    ) -> None:
        self._max_files = max_files
        self._max_file_bytes = max_file_bytes
        self._max_package_bytes = max_package_bytes

    def discover(self, root: str | Path) -> tuple[AgentSkillMetadata, ...]:
        """Return only routing metadata for direct child skill directories."""

        root_path = Path(root)
        if not root_path.is_dir():
            raise SkillFormatError(f"skill root {str(root_path)!r} is not a directory")
        discovered: list[AgentSkillMetadata] = []
        for child in sorted(root_path.iterdir(), key=lambda item: item.name):
            if child.is_dir() and (child / "SKILL.md").is_file():
                metadata, _ = self._parse_skill_markdown(child)
                discovered.append(metadata)
        names = [item.name for item in discovered]
        if len(names) != len(set(names)):
            raise SkillFormatError("skill names must be unique within one root")
        return tuple(discovered)

    def load(self, skill_directory: str | Path) -> SkillPackage:
        """Load instructions, Voren sidecar, and bounded package resources."""

        directory = Path(skill_directory)
        metadata, instructions = self._parse_skill_markdown(directory)
        sidecar_path = directory / "skill.yaml"
        if not sidecar_path.is_file() or sidecar_path.is_symlink():
            raise SkillFormatError("Voren skills require a regular skill.yaml sidecar")
        try:
            raw_contract = yaml.safe_load(sidecar_path.read_text(encoding="utf-8"))
            if not isinstance(raw_contract, dict):
                raise ValueError("skill.yaml must contain a mapping")
            contract = SkillContract.model_validate(raw_contract)
        except (OSError, UnicodeError, yaml.YAMLError, ValueError) as error:
            raise SkillFormatError(f"invalid skill.yaml: {error}") from error
        files = self._read_package_files(directory)
        return SkillPackage.build(
            metadata=metadata,
            instructions=instructions,
            contract=contract,
            files=files,
        )

    def _parse_skill_markdown(
        self, directory: Path
    ) -> tuple[AgentSkillMetadata, str]:
        if not directory.is_dir() or directory.is_symlink():
            raise SkillFormatError("skill path must be a regular directory")
        skill_path = directory / "SKILL.md"
        if not skill_path.is_file() or skill_path.is_symlink():
            raise SkillFormatError("skill directory must contain a regular SKILL.md")
        try:
            raw = skill_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise SkillFormatError(f"cannot read SKILL.md: {error}") from error
        frontmatter, instructions = self._split_frontmatter(raw)
        if not instructions.strip():
            raise SkillFormatError("SKILL.md must contain instruction content")
        unknown = set(frontmatter) - FRONTMATTER_KEYS
        if unknown:
            raise SkillFormatError(
                f"unsupported SKILL.md frontmatter fields: {sorted(unknown)}"
            )
        metadata_values = frontmatter.get("metadata", {})
        if not isinstance(metadata_values, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in metadata_values.items()
        ):
            raise SkillFormatError("SKILL.md metadata must map strings to strings")
        normalized: dict[str, Any] = {
            "name": frontmatter.get("name"),
            "description": frontmatter.get("description"),
            "license": frontmatter.get("license"),
            "compatibility": frontmatter.get("compatibility"),
            "metadata": metadata_values,
            "allowed_tools_hint": frontmatter.get("allowed-tools"),
        }
        try:
            metadata = AgentSkillMetadata.model_validate(normalized)
        except ValueError as error:
            raise SkillFormatError(f"invalid SKILL.md metadata: {error}") from error
        if metadata.name != directory.name:
            raise SkillFormatError("SKILL.md name must match its parent directory")
        return metadata, instructions.strip()

    @staticmethod
    def _split_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
        lines = raw.splitlines()
        if not lines or lines[0].strip() != "---":
            raise SkillFormatError("SKILL.md must start with YAML frontmatter")
        try:
            closing_index = next(
                index
                for index, line in enumerate(lines[1:], start=1)
                if line.strip() == "---"
            )
        except StopIteration as error:
            raise SkillFormatError("SKILL.md frontmatter is not closed") from error
        try:
            parsed = yaml.safe_load("\n".join(lines[1:closing_index]))
        except yaml.YAMLError as error:
            raise SkillFormatError(f"invalid SKILL.md YAML: {error}") from error
        if not isinstance(parsed, dict):
            raise SkillFormatError("SKILL.md frontmatter must be a mapping")
        return parsed, "\n".join(lines[closing_index + 1 :])

    def _read_package_files(self, directory: Path) -> tuple[SkillFile, ...]:
        files: list[SkillFile] = []
        total_bytes = 0
        for path in sorted(directory.rglob("*"), key=lambda item: item.as_posix()):
            if path.is_symlink():
                raise SkillFormatError("skill packages cannot contain symlinks")
            if path.is_dir():
                continue
            if not path.is_file():
                raise SkillFormatError("skill packages may contain only regular files")
            relative = path.relative_to(directory).as_posix()
            pure_relative = PurePosixPath(relative)
            if pure_relative.is_absolute() or ".." in pure_relative.parts:
                raise SkillFormatError("skill resource path escapes its package")
            content = path.read_bytes()
            if len(content) > self._max_file_bytes:
                raise SkillFormatError(f"skill resource {relative!r} is too large")
            total_bytes += len(content)
            if total_bytes > self._max_package_bytes:
                raise SkillFormatError("skill package exceeds total size limit")
            files.append(
                SkillFile(
                    relative_path=relative,
                    content=content,
                    digest=hashlib.sha256(content).hexdigest(),
                )
            )
            if len(files) > self._max_files:
                raise SkillFormatError("skill package contains too many files")
        return tuple(files)
