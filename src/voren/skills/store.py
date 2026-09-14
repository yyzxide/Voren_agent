"""Content-addressed immutable skill versions and an atomic active pointer."""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from voren.skills.models import (
    ActiveSkillSummary,
    AgentSkillMetadata,
    LoadedSkill,
    SkillContract,
    SkillFile,
    SkillPackage,
    SkillVersion,
    SkillVersionRef,
)
from voren.skills.parser import AgentSkillParser, SkillFormatError


class SQLiteSkillStore:
    """Persist version metadata in SQLite and packages as immutable objects."""

    def __init__(
        self,
        database: str | Path,
        *,
        root: str | Path,
        parser: AgentSkillParser | None = None,
    ) -> None:
        self._root = Path(root)
        self._objects = self._root / "objects"
        self._objects.mkdir(parents=True, exist_ok=True)
        self._parser = parser or AgentSkillParser()
        self._connection = sqlite3.connect(str(database))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS skill_versions (
                skill_name TEXT NOT NULL,
                version_id TEXT NOT NULL,
                content_digest TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                contract_json TEXT NOT NULL,
                package_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(skill_name, version_id),
                UNIQUE(content_digest)
            );

            CREATE TABLE IF NOT EXISTS active_skills (
                skill_name TEXT PRIMARY KEY,
                version_id TEXT NOT NULL,
                activated_at TEXT NOT NULL,
                activation_reason TEXT NOT NULL,
                FOREIGN KEY(skill_name, version_id)
                    REFERENCES skill_versions(skill_name, version_id)
            );
            """
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def install(
        self,
        package: SkillPackage,
        *,
        created_at: datetime | None = None,
    ) -> SkillVersion:
        """Install an immutable version without changing the active pointer."""

        ref = SkillVersionRef(
            name=package.metadata.name,
            version_id=package.content_digest,
            content_digest=package.content_digest,
        )
        package_path = self._materialize(package)
        timestamp = created_at or datetime.now(UTC)
        metadata_json = package.metadata.model_dump_json()
        contract_json = package.contract.model_dump_json()
        relative_package_path = package_path.relative_to(self._root).as_posix()
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO skill_versions (
                    skill_name, version_id, content_digest, metadata_json,
                    contract_json, package_path, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(skill_name, version_id) DO NOTHING
                """,
                (
                    ref.name,
                    ref.version_id,
                    ref.content_digest,
                    metadata_json,
                    contract_json,
                    relative_package_path,
                    timestamp.isoformat(),
                ),
            )
        installed = self.get_version(ref)
        if (
            installed.metadata != package.metadata
            or installed.contract != package.contract
            or installed.package_path != relative_package_path
        ):
            raise SkillFormatError("installed skill version metadata is inconsistent")
        return installed

    def activate(
        self,
        ref: SkillVersionRef,
        *,
        reason: str,
        activated_at: datetime | None = None,
    ) -> ActiveSkillSummary:
        """Atomically move one skill's active pointer to an installed version."""

        if not reason.strip():
            raise ValueError("skill activation requires a non-empty reason")
        version = self.get_version(ref)
        timestamp = activated_at or datetime.now(UTC)
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO active_skills (
                    skill_name, version_id, activated_at, activation_reason
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(skill_name) DO UPDATE SET
                    version_id = excluded.version_id,
                    activated_at = excluded.activated_at,
                    activation_reason = excluded.activation_reason
                """,
                (ref.name, ref.version_id, timestamp.isoformat(), reason),
            )
        return ActiveSkillSummary(
            ref=version.ref,
            description=version.metadata.description,
            activated_at=timestamp,
            activation_reason=reason,
        )

    def discover_active(self) -> tuple[ActiveSkillSummary, ...]:
        """Load routing metadata only; do not load instructions or resources."""

        rows = self._connection.execute(
            """
            SELECT v.skill_name, v.version_id, v.content_digest, v.metadata_json,
                   a.activated_at, a.activation_reason
            FROM active_skills AS a
            JOIN skill_versions AS v
              ON v.skill_name = a.skill_name AND v.version_id = a.version_id
            ORDER BY v.skill_name
            """
        ).fetchall()
        summaries: list[ActiveSkillSummary] = []
        for row in rows:
            metadata = AgentSkillMetadata.model_validate_json(row["metadata_json"])
            summaries.append(
                ActiveSkillSummary(
                    ref=self._ref_from_row(row),
                    description=metadata.description,
                    activated_at=row["activated_at"],
                    activation_reason=row["activation_reason"],
                )
            )
        return tuple(summaries)

    def freeze_active(
        self, names: tuple[str, ...] | None = None
    ) -> tuple[SkillVersionRef, ...]:
        """Snapshot exact active versions for an immutable run config."""

        if names is not None and len(names) != len(set(names)):
            raise ValueError("skill names to freeze must be unique")
        active = {summary.ref.name: summary.ref for summary in self.discover_active()}
        selected_names = tuple(sorted(active)) if names is None else names
        missing = [name for name in selected_names if name not in active]
        if missing:
            raise SkillFormatError(f"skills are not active: {missing}")
        return tuple(active[name] for name in selected_names)

    def load(self, ref: SkillVersionRef) -> LoadedSkill:
        """Load full instructions for one frozen version, regardless of active state."""

        version = self.get_version(ref)
        package = self._load_verified_package(version)
        resources = tuple(
            item.relative_path
            for item in package.files
            if item.relative_path not in {"SKILL.md", "skill.yaml"}
        )
        return LoadedSkill(
            version=version,
            instructions=package.instructions,
            resources=resources,
        )

    def load_package(self, ref: SkillVersionRef) -> SkillPackage:
        """Load an integrity-checked package for offline review or learning."""

        version = self.get_version(ref)
        return self._load_verified_package(version)

    def load_resource(
        self, ref: SkillVersionRef, relative_path: str
    ) -> SkillFile:
        """Load one declared resource from an exact frozen version on demand."""

        if relative_path in {"SKILL.md", "skill.yaml"}:
            raise SkillFormatError("use skill activation APIs for manifest content")
        version = self.get_version(ref)
        package = self._load_verified_package(version)
        for item in package.files:
            if item.relative_path == relative_path:
                return item
        raise SkillFormatError(
            f"skill resource {relative_path!r} does not exist in {ref.name!r}"
        )

    def get_version(self, ref: SkillVersionRef) -> SkillVersion:
        row = self._connection.execute(
            """
            SELECT * FROM skill_versions
            WHERE skill_name = ? AND version_id = ?
            """,
            (ref.name, ref.version_id),
        ).fetchone()
        if row is None:
            raise SkillFormatError(
                f"unknown skill version {ref.name!r}@{ref.version_id}"
            )
        stored_ref = self._ref_from_row(row)
        if stored_ref != ref:
            raise SkillFormatError("skill reference digest does not match the store")
        return SkillVersion(
            ref=stored_ref,
            metadata=AgentSkillMetadata.model_validate_json(row["metadata_json"]),
            contract=SkillContract.model_validate_json(row["contract_json"]),
            package_path=row["package_path"],
            created_at=row["created_at"],
        )

    def _materialize(self, package: SkillPackage) -> Path:
        object_directory = self._objects / package.content_digest
        target = object_directory / package.metadata.name
        if object_directory.exists():
            existing = self._parser.load(target)
            if existing.content_digest != package.content_digest:
                raise SkillFormatError("content-addressed skill object is corrupted")
            return target

        temporary = Path(tempfile.mkdtemp(prefix=".install-", dir=self._objects))
        try:
            temporary_skill = temporary / package.metadata.name
            temporary_skill.mkdir()
            for item in package.files:
                destination = temporary_skill / item.relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(item.content)
            try:
                os.replace(temporary, object_directory)
            except OSError:
                if not object_directory.exists():
                    raise
            installed = self._parser.load(target)
            if installed.content_digest != package.content_digest:
                raise SkillFormatError("materialized skill digest does not match package")
            return target
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def _resolve_package_path(self, relative_path: str) -> Path:
        candidate = (self._root / relative_path).resolve()
        root = self._root.resolve()
        if not candidate.is_relative_to(root):
            raise SkillFormatError("stored skill path escapes the store root")
        return candidate

    def _load_verified_package(self, version: SkillVersion) -> SkillPackage:
        package_path = self._resolve_package_path(version.package_path)
        package = self._parser.load(package_path)
        if (
            package.content_digest != version.ref.content_digest
            or package.metadata != version.metadata
            or package.contract != version.contract
        ):
            raise SkillFormatError("stored skill package no longer matches its version")
        return package

    @staticmethod
    def _ref_from_row(row: sqlite3.Row) -> SkillVersionRef:
        return SkillVersionRef(
            name=row["skill_name"],
            version_id=row["version_id"],
            content_digest=row["content_digest"],
        )
