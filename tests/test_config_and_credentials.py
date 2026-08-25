"""Configuration layering and credential handling."""

from __future__ import annotations

import pytest
import yaml

from orchestrator.core.config import CONFIG_FILENAME, global_config_dir, load_settings
from orchestrator.core.credentials import CredentialStore, redact
from orchestrator.core.errors import ConfigurationError, CredentialError


def _write_global(payload: dict) -> None:
    target = global_config_dir() / CONFIG_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")


def test_defaults_when_nothing_is_configured():
    settings = load_settings(None)
    assert settings.engine.backend == "claude_code"
    assert settings.git.default_base_branch == "main"
    assert settings.sources == ["defaults"]


def test_repo_config_overrides_global(generic_repo):
    _write_global({"git": {"default_base_branch": "develop"}})
    repo_config = generic_repo / ".orchestrator" / "config.yaml"
    repo_config.parent.mkdir(parents=True)
    repo_config.write_text(yaml.safe_dump({"git": {"default_base_branch": "release"}}))

    settings = load_settings(generic_repo)
    assert settings.git.default_base_branch == "release"


def test_environment_overrides_files(generic_repo, monkeypatch):
    _write_global({"engine": {"backend": "claude_code"}})
    monkeypatch.setenv("ORC_ENGINE", "mock")
    assert load_settings(generic_repo).engine.backend == "mock"


def test_env_values_are_coerced(monkeypatch):
    monkeypatch.setenv("ORC_ENGINE_TIMEOUT", "42")
    monkeypatch.setenv("ORC_INTERACTIVE", "false")
    settings = load_settings(None)
    assert settings.engine.timeout_seconds == 42
    assert settings.behaviour.interactive is False


def test_deep_merge_keeps_untouched_sections(generic_repo):
    _write_global({"jira": {"base_url": "https://x.atlassian.net", "max_comments": 5}})
    repo_config = generic_repo / ".orchestrator" / "config.yaml"
    repo_config.parent.mkdir(parents=True)
    repo_config.write_text(yaml.safe_dump({"jira": {"max_comments": 9}}))

    settings = load_settings(generic_repo)
    assert settings.jira.base_url == "https://x.atlassian.net"
    assert settings.jira.max_comments == 9


def test_auto_approve_requires_explicit_acknowledgement(monkeypatch):
    _write_global({"behaviour": {"auto_approve": True}})
    with pytest.raises(ConfigurationError):
        load_settings(None)

    monkeypatch.setenv("ORC_I_UNDERSTAND_AUTO_APPROVE", "1")
    assert load_settings(None).behaviour.auto_approve is True


def test_invalid_yaml_is_reported_clearly():
    target = global_config_dir() / CONFIG_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("git: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_settings(None)


def test_settings_yaml_contains_no_secrets(monkeypatch):
    monkeypatch.setenv("ORC_JIRA_API_TOKEN", "super-secret")
    rendered = load_settings(None).to_yaml()
    assert "super-secret" not in rendered


# -- credentials ---------------------------------------------------------------- #


def test_env_wins_over_dotenv(tmp_path, monkeypatch):
    store = CredentialStore(tmp_path)
    store.set("ORC_JIRA_API_TOKEN", "from-file", prefer_keyring=False)
    monkeypatch.setenv("ORC_JIRA_API_TOKEN", "from-env")
    value, source = store.get("ORC_JIRA_API_TOKEN")
    assert value == "from-env"
    assert source.name == "environment"


def test_dotenv_is_written_with_owner_only_permissions(tmp_path):
    store = CredentialStore(tmp_path)
    store.set("ORC_GITHUB_TOKEN", "abc", prefer_keyring=False)
    assert oct(store.env_file.stat().st_mode)[-3:] == "600"


def test_delete_removes_the_secret(tmp_path):
    store = CredentialStore(tmp_path)
    store.set("ORC_GITHUB_TOKEN", "abc", prefer_keyring=False)
    store.delete("ORC_GITHUB_TOKEN")
    assert store.get("ORC_GITHUB_TOKEN")[0] is None


def test_require_raises_actionable_error(tmp_path):
    with pytest.raises(CredentialError) as exc:
        CredentialStore(tmp_path).require("ORC_JIRA_API_TOKEN", purpose="Jira")
    assert "set-secret" in str(exc.value)


def test_redact_never_shows_the_middle():
    assert redact("abcdefghijklmnop") == "abcd…mnop"
    assert redact("short") == "*****"
    assert redact(None) == "(unset)"
