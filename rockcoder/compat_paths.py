from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
from typing import Iterable


@dataclass(frozen=True)
class ResolvedDataDir:
    path: Path
    is_legacy: bool


@dataclass(frozen=True)
class MigrationConflict:
    """A source entry that could not be copied without overwriting data."""

    relative_path: Path
    source: Path
    destination: Path
    reason: str


@dataclass(frozen=True)
class MigrationReport:
    """Result of an explicit legacy data migration.

    ``copied`` and ``created_directories`` contain paths relative to
    ``destination``.  Existing files with identical contents are reported in
    ``skipped``; they are deliberately not rewritten, which makes a rerun
    idempotent.  No operation in this module removes anything from
    ``source``.
    """

    source: Path
    destination: Path
    source_exists: bool
    source_populated: bool
    destination_created: bool
    copied: tuple[Path, ...] = ()
    created_directories: tuple[Path, ...] = ()
    skipped: tuple[Path, ...] = ()
    conflicts: tuple[MigrationConflict, ...] = ()
    verification_errors: tuple[Path, ...] = ()
    errors: tuple[str, ...] = ()
    verification_performed: bool = True

    @property
    def verified(self) -> bool:
        """Whether every copied or compared file passed verification."""

        return not self.verification_errors and not self.errors

    @property
    def successful(self) -> bool:
        """Whether migration completed without conflicts or operational errors."""

        return self.verified and not self.conflicts

    @property
    def changed(self) -> bool:
        """Whether this invocation created directories or copied files."""

        return bool(self.copied or self.created_directories)


def _resolve_dir(base: str | Path, new_name: str, legacy_name: str) -> ResolvedDataDir:
    """Return the canonical data directory for normal runtime operations.

    Legacy data is handled only by :func:`migrate_legacy_data`; keeping that
    boundary here prevents different consumers from silently splitting state
    between two directory trees.
    """
    del legacy_name  # retained in the private signature for call-site clarity
    new_path = Path(base) / new_name
    new_path.mkdir(parents=True, exist_ok=True)
    return ResolvedDataDir(path=new_path, is_legacy=False)


def _iter_source_entries(source: Path) -> tuple[list[Path], list[Path]]:
    """Return source directories and files without following symlinks."""

    directories: list[Path] = []
    files: list[Path] = []
    for root, dir_names, file_names in os.walk(source, followlinks=False):
        root_path = Path(root)
        # A symlink in dir_names is not traversed.  Treating it as a file-like
        # entry lets the caller report it as a conflict instead of following a
        # link outside the migration tree.
        linked_dirs = [name for name in dir_names if (root_path / name).is_symlink()]
        dir_names[:] = [name for name in dir_names if name not in linked_dirs]
        directories.extend(root_path / name for name in dir_names)
        files.extend(root_path / name for name in file_names)
        files.extend(root_path / name for name in linked_dirs)
    directories.sort(key=lambda path: path.relative_to(source).as_posix())
    files.sort(key=lambda path: path.relative_to(source).as_posix())
    return directories, files


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _same_file(source: Path, destination: Path) -> bool:
    return source.is_file() and destination.is_file() and _sha256(source) == _sha256(destination)


def _copy_file_atomically(source: Path, destination: Path) -> None:
    """Copy a file without exposing a partially-written destination."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=destination.parent, prefix=f".{destination.name}.", delete=False
        ) as temporary:
            temporary_name = temporary.name
            with source.open("rb") as source_stream:
                shutil.copyfileobj(source_stream, temporary)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, destination)
        temporary_name = None
        # Preserve permissions and timestamps where possible, while keeping
        # content migration successful on filesystems that do not support all
        # metadata operations.
        try:
            shutil.copystat(source, destination, follow_symlinks=False)
        except OSError:
            pass
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except OSError:
                pass


def migrate_legacy_data(
    base: str | Path,
    *,
    verify: bool = True,
    new_name: str = ".rockcoder",
    legacy_name: str = ".mewcode",
) -> MigrationReport:
    """Safely copy legacy data into the current data directory.

    This is intentionally explicit: resolvers continue to choose an existing
    ``.rockcoder`` directory exactly as before, and callers opt into migration
    by calling this function.  If ``.rockcoder`` is empty while ``.mewcode``
    contains data, the legacy entries are copied recursively.  If both sides
    contain an entry with different content, it is reported as a conflict and
    neither side is changed.  Identical entries are skipped, so rerunning the
    migration is safe and idempotent.  The legacy source is never deleted or
    modified.

    ``verify`` performs SHA-256 content verification for copied and existing
    files.  Operational failures are collected in the report rather than
    causing a partial migration to be mistaken for success.
    """

    base_path = Path(base)
    source = base_path / legacy_name
    destination = base_path / new_name
    verification_errors: list[Path] = []
    errors: list[str] = []
    conflicts: list[MigrationConflict] = []
    copied: list[Path] = []
    created_directories: list[Path] = []
    skipped: list[Path] = []

    if not source.exists():
        return MigrationReport(
            source=source,
            destination=destination,
            source_exists=False,
            source_populated=False,
            destination_created=False,
            verification_performed=verify,
        )

    if not source.is_dir():
        conflicts.append(
            MigrationConflict(
                relative_path=Path("."),
                source=source,
                destination=destination,
                reason="legacy source is not a directory",
            )
        )
        return MigrationReport(
            source=source,
            destination=destination,
            source_exists=True,
            source_populated=True,
            destination_created=False,
            conflicts=tuple(conflicts),
            verification_performed=verify,
        )

    try:
        source_directories, source_files = _iter_source_entries(source)
    except OSError as exc:
        errors.append(f"could not inspect legacy source {source}: {exc}")
        return MigrationReport(
            source=source,
            destination=destination,
            source_exists=True,
            source_populated=True,
            destination_created=False,
            errors=tuple(errors),
            verification_performed=verify,
        )

    source_populated = bool(source_directories or source_files)
    destination_created = False
    if destination.exists() and not destination.is_dir():
        conflicts.append(
            MigrationConflict(
                relative_path=Path("."),
                source=source,
                destination=destination,
                reason="destination exists and is not a directory",
            )
        )
        return MigrationReport(
            source=source,
            destination=destination,
            source_exists=True,
            source_populated=source_populated,
            destination_created=False,
            conflicts=tuple(conflicts),
            verification_performed=verify,
        )

    if not destination.exists():
        try:
            destination.mkdir(parents=True, exist_ok=True)
            destination_created = True
        except OSError as exc:
            errors.append(f"could not create destination {destination}: {exc}")
            return MigrationReport(
                source=source,
                destination=destination,
                source_exists=True,
                source_populated=source_populated,
                destination_created=False,
                errors=tuple(errors),
                verification_performed=verify,
            )

    # Create directories before files.  An existing directory is harmless;
    # an existing file at a directory path is a conflict and is never removed.
    for source_directory in source_directories:
        relative_path = source_directory.relative_to(source)
        destination_directory = destination / relative_path
        if destination_directory.exists():
            if destination_directory.is_dir() and not destination_directory.is_symlink():
                continue
            conflicts.append(
                MigrationConflict(
                    relative_path=relative_path,
                    source=source_directory,
                    destination=destination_directory,
                    reason="source directory conflicts with a non-directory destination",
                )
            )
            continue
        try:
            destination_directory.mkdir(parents=True, exist_ok=True)
            created_directories.append(relative_path)
        except OSError as exc:
            errors.append(f"could not create destination directory {destination_directory}: {exc}")

    for source_file in source_files:
        relative_path = source_file.relative_to(source)
        destination_file = destination / relative_path

        if source_file.is_symlink():
            conflicts.append(
                MigrationConflict(
                    relative_path=relative_path,
                    source=source_file,
                    destination=destination_file,
                    reason="symbolic links are not copied",
                )
            )
            continue
        if not source_file.is_file():
            conflicts.append(
                MigrationConflict(
                    relative_path=relative_path,
                    source=source_file,
                    destination=destination_file,
                    reason="source entry is not a regular file",
                )
            )
            continue

        if destination_file.exists() or destination_file.is_symlink():
            if destination_file.is_symlink() or not destination_file.is_file():
                conflicts.append(
                    MigrationConflict(
                        relative_path=relative_path,
                        source=source_file,
                        destination=destination_file,
                        reason="source file conflicts with a non-file destination",
                    )
                )
                continue
            try:
                identical = _same_file(source_file, destination_file)
            except OSError as exc:
                errors.append(f"could not compare {source_file} and {destination_file}: {exc}")
                continue
            if identical:
                skipped.append(relative_path)
                continue
            conflicts.append(
                MigrationConflict(
                    relative_path=relative_path,
                    source=source_file,
                    destination=destination_file,
                    reason="destination file has different content",
                )
            )
            continue

        try:
            _copy_file_atomically(source_file, destination_file)
            if verify and not _same_file(source_file, destination_file):
                verification_errors.append(relative_path)
            copied.append(relative_path)
        except OSError as exc:
            errors.append(f"could not copy {source_file} to {destination_file}: {exc}")
            # A failed copy may have left a destination behind.  It is safe to
            # remove only this newly-created destination, never the source.
            try:
                if destination_file.exists():
                    destination_file.unlink()
            except OSError:
                pass

    return MigrationReport(
        source=source,
        destination=destination,
        source_exists=True,
        source_populated=source_populated,
        destination_created=destination_created,
        copied=tuple(copied),
        created_directories=tuple(created_directories),
        skipped=tuple(skipped),
        conflicts=tuple(conflicts),
        verification_errors=tuple(verification_errors),
        errors=tuple(errors),
        verification_performed=verify,
    )


# Explicit names for callers that want to document the scope of migration.
def migrate_project_data(work_dir: str | Path, *, verify: bool = True) -> MigrationReport:
    return migrate_legacy_data(work_dir, verify=verify)


def migrate_user_data(home_dir: str | Path, *, verify: bool = True) -> MigrationReport:
    return migrate_legacy_data(home_dir, verify=verify)


# Keep a descriptive alias for callers that prefer the directory terminology.
migrate_legacy_data_dir = migrate_legacy_data


def resolve_project_data_dir(work_dir: str | Path) -> ResolvedDataDir:
    return _resolve_dir(work_dir, ".rockcoder", ".mewcode")


def resolve_user_data_dir(home_dir: str | Path) -> ResolvedDataDir:
    return _resolve_dir(home_dir, ".rockcoder", ".mewcode")
