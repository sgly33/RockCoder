from __future__ import annotations

from pathlib import Path

from rockcoder.compat_paths import (
    migrate_legacy_data,
    resolve_project_data_dir,
    resolve_user_data_dir,
)


def test_project_dir_always_uses_canonical_directory(tmp_path: Path) -> None:
    (tmp_path / ".rockcoder").mkdir()
    (tmp_path / ".mewcode").mkdir()

    result = resolve_project_data_dir(tmp_path)

    assert result.path == tmp_path / ".rockcoder"
    assert result.is_legacy is False


def test_project_dir_stays_canonical_when_only_legacy_exists(tmp_path: Path) -> None:
    (tmp_path / ".mewcode").mkdir()

    result = resolve_project_data_dir(tmp_path)

    assert result.path == tmp_path / ".rockcoder"
    assert result.is_legacy is False


def test_project_dir_creates_rockcoder_when_neither_exists(tmp_path: Path) -> None:
    result = resolve_project_data_dir(tmp_path)

    assert result.path == tmp_path / ".rockcoder"
    assert result.path.exists()
    assert result.is_legacy is False


def test_user_dir_creates_rockcoder_when_neither_exists(tmp_path: Path) -> None:
    result = resolve_user_data_dir(tmp_path)

    assert result.path == tmp_path / ".rockcoder"
    assert result.path.exists()
    assert result.is_legacy is False


def test_migration_copies_nested_files_when_new_dir_is_empty(tmp_path: Path) -> None:
    legacy = tmp_path / ".mewcode"
    (legacy / "nested").mkdir(parents=True)
    (legacy / "config.yaml").write_text("model: test\n", encoding="utf-8")
    (legacy / "nested" / "note.txt").write_text("hello", encoding="utf-8")
    (tmp_path / ".rockcoder").mkdir()

    report = migrate_legacy_data(tmp_path)

    assert report.successful
    assert report.copied == (Path("config.yaml"), Path("nested/note.txt"))
    assert (tmp_path / ".rockcoder" / "config.yaml").read_text(encoding="utf-8") == "model: test\n"
    assert (tmp_path / ".rockcoder" / "nested" / "note.txt").read_text(encoding="utf-8") == "hello"
    assert (legacy / "config.yaml").exists()
    assert (legacy / "nested" / "note.txt").exists()


def test_migration_reports_conflicts_without_overwriting(tmp_path: Path) -> None:
    legacy = tmp_path / ".mewcode"
    new = tmp_path / ".rockcoder"
    legacy.mkdir()
    new.mkdir()
    (legacy / "config.yaml").write_text("legacy", encoding="utf-8")
    (new / "config.yaml").write_text("current", encoding="utf-8")

    report = migrate_legacy_data(tmp_path)

    assert not report.successful
    assert len(report.conflicts) == 1
    assert report.conflicts[0].relative_path == Path("config.yaml")
    assert (new / "config.yaml").read_text(encoding="utf-8") == "current"


def test_migration_is_idempotent_and_skips_identical_files(tmp_path: Path) -> None:
    legacy = tmp_path / ".mewcode"
    legacy.mkdir()
    (legacy / "state.json").write_text("{}", encoding="utf-8")

    first = migrate_legacy_data(tmp_path)
    second = migrate_legacy_data(tmp_path)

    assert first.successful
    assert second.successful
    assert second.copied == ()
    assert second.skipped == (Path("state.json"),)
    assert (tmp_path / ".rockcoder" / "state.json").read_text(encoding="utf-8") == "{}"
    assert (legacy / "state.json").exists()


def test_migration_with_missing_legacy_source_is_noop(tmp_path: Path) -> None:
    report = migrate_legacy_data(tmp_path)

    assert report.successful
    assert report.source_exists is False
    assert report.changed is False
    assert not (tmp_path / ".rockcoder").exists()
