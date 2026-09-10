from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from rockcoder.config import AppConfig, ConfigError, ProviderConfig, load_config, save_config


class TestLoadConfigCompatibilityPaths:
    def test_prefers_rockcoder_project_config_when_both_exist(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        project_new = tmp_path / ".rockcoder"
        project_legacy = tmp_path / ".mewcode"
        project_new.mkdir()
        project_legacy.mkdir()

        (project_new / "config.yaml").write_text(textwrap.dedent("""\
            providers:
              - name: new
                protocol: openai
                base_url: https://example.com
                model: gpt-4o
                api_key: sk-new
        """), encoding="utf-8")
        (project_legacy / "config.yaml").write_text(textwrap.dedent("""\
            providers:
              - name: legacy
                protocol: openai
                base_url: https://example.com
                model: gpt-4o
                api_key: sk-legacy
        """), encoding="utf-8")

        with patch("rockcoder.config.Path.home", return_value=home), patch("rockcoder.config.Path.cwd", return_value=tmp_path):
            config = load_config()

        assert config.providers[0].name == "new"

    def test_falls_back_to_legacy_project_config(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        project_legacy = tmp_path / ".mewcode"
        project_legacy.mkdir()
        (project_legacy / "config.yaml").write_text(textwrap.dedent("""\
            providers:
              - name: legacy
                protocol: openai
                base_url: https://example.com
                model: gpt-4o
                api_key: sk-legacy
        """), encoding="utf-8")

        with patch("rockcoder.config.Path.home", return_value=home), patch("rockcoder.config.Path.cwd", return_value=tmp_path):
            config = load_config()

        assert config.providers[0].name == "legacy"

    def test_error_message_mentions_rockcoder_path(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()

        with patch("rockcoder.config.Path.home", return_value=home), patch("rockcoder.config.Path.cwd", return_value=tmp_path):
            with pytest.raises(ConfigError, match=r"\.rockcoder/config\.yaml"):
                load_config()

    def test_local_config_cannot_override_without_base_config(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        project_new = project / ".rockcoder"
        project_new.mkdir()
        (project_new / "config.local.yaml").write_text(textwrap.dedent("""\
            permission_mode: bypassPermissions
        """), encoding="utf-8")

        with patch("rockcoder.config.Path.home", return_value=home), patch("rockcoder.config.Path.cwd", return_value=project):
            with pytest.raises(ConfigError, match="providers"):
                load_config()

    def test_save_config_persists_permission_mode(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".rockcoder" / "config.yaml"
        config_path.parent.mkdir()
        config_path.write_text(textwrap.dedent("""\
            providers:
              - name: base
                protocol: openai
                base_url: https://example.com
                model: gpt-4o
                api_key: sk-base
            permission_mode: default
        """), encoding="utf-8")

        config = load_config(config_path)
        config.permission_mode = "bypassPermissions"
        save_config(config_path, config)

        reloaded = load_config(config_path)
        assert reloaded.permission_mode == "bypassPermissions"
        assert reloaded.providers[0].name == "base"
