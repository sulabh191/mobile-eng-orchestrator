"""Layered configuration.

Precedence (highest wins):

    environment (ORC_*)  >  <repo>/.orchestrator/config.yaml  >  global config.yaml  >  defaults

Secrets are deliberately *not* part of this model — see
:mod:`orchestrator.core.credentials`. Everything here is safe to print.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from platformdirs import user_config_dir, user_state_dir
from pydantic import Field

from orchestrator.core.credentials import CredentialStore
from orchestrator.core.errors import ConfigurationError
from orchestrator.core.models import StrictModel

APP_NAME = "engineering-orchestrator"
CONFIG_FILENAME = "config.yaml"
REPO_CONFIG_RELPATH = Path(".orchestrator") / "config.yaml"


def global_config_dir() -> Path:
    override = os.environ.get("ORC_CONFIG_DIR")
    return Path(override).expanduser() if override else Path(user_config_dir(APP_NAME))


def global_state_dir() -> Path:
    override = os.environ.get("ORC_STATE_DIR")
    return Path(override).expanduser() if override else Path(user_state_dir(APP_NAME))


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #


class JiraSettings(StrictModel):
    base_url: str | None = None
    email: str | None = None
    default_project: str | None = None
    #: Custom field id holding acceptance criteria, e.g. "customfield_10101".
    acceptance_criteria_field: str | None = None
    timeout_seconds: float = 30.0
    max_comments: int = 20
    verify_ssl: bool = True


class GitSettings(StrictModel):
    provider: str = Field(default="github", description="github | gitlab | none")
    default_base_branch: str = "main"
    branch_template: str = "{prefix}/{issue_key_lower}-{slug}"
    branch_prefix_by_type: dict[str, str] = Field(
        default_factory=lambda: {
            "bug": "fix",
            "defect": "fix",
            "story": "feature",
            "task": "chore",
            "spike": "spike",
        }
    )
    commit_template: str = "{type}({scope}): {summary}\n\n{body}\n\nRefs: {issue_key}"
    sign_commits: bool = False
    push_remote: str = "origin"
    #: Never operate directly on these branches.
    protected_branches: list[str] = Field(default_factory=lambda: ["main", "master", "develop"])


class EngineSettings(StrictModel):
    backend: str = Field(default="claude_code", description="claude_code | agent_sdk | mock")
    model: str | None = None
    timeout_seconds: float = 900.0
    max_output_tokens: int = 16000
    #: Path to the `claude` executable for the claude_code backend.
    claude_binary: str = "claude"
    #: Extra CLI flags passed verbatim to the claude_code backend.
    extra_args: list[str] = Field(default_factory=list)
    #: Persist every prompt/response pair into the run's artifacts directory.
    record_transcripts: bool = True


class ValidationSettings(StrictModel):
    #: Stop at the first failing required check instead of running them all.
    fail_fast: bool = False
    per_check_timeout_seconds: float = 1800.0
    #: How many implement -> validate remediation loops to allow before giving up.
    max_remediation_attempts: int = 2
    skip: list[str] = Field(default_factory=list, description="Check names to skip")
    #: Extra project-specific checks: [{name, command, required}]
    extra_checks: list[dict[str, Any]] = Field(default_factory=list)
    ios: dict[str, Any] = Field(default_factory=dict)
    android: dict[str, Any] = Field(default_factory=dict)


class BehaviourSettings(StrictModel):
    #: Auto-approve every gate. Refused unless ORC_I_UNDERSTAND_AUTO_APPROVE=1.
    auto_approve: bool = False
    interactive: bool = True
    log_level: str = "INFO"
    #: Absolute cap on files a single run may modify; a guard against runaway edits.
    max_touched_files: int = 60
    #: Paths the implementer may never write to, relative to the target repo.
    protected_paths: list[str] = Field(
        default_factory=lambda: [
            ".git/",
            ".orchestrator/",
            "**/*.p12",
            "**/*.mobileprovision",
            "**/*.keystore",
            "**/*.jks",
            "**/google-services.json",
            "**/GoogleService-Info.plist",
            "**/.env",
        ]
    )


class Settings(StrictModel):
    """The complete, secret-free configuration for one invocation."""

    jira: JiraSettings = Field(default_factory=JiraSettings)
    git: GitSettings = Field(default_factory=GitSettings)
    engine: EngineSettings = Field(default_factory=EngineSettings)
    validation: ValidationSettings = Field(default_factory=ValidationSettings)
    behaviour: BehaviourSettings = Field(default_factory=BehaviourSettings)

    #: Where each layer came from, for `orc config show` and `orc doctor`.
    sources: list[str] = Field(default_factory=list)

    # -- helpers ------------------------------------------------------------ #

    @property
    def credentials(self) -> CredentialStore:
        return CredentialStore(global_config_dir())

    def to_yaml(self) -> str:
        data = self.model_dump(mode="json", exclude={"sources"})
        return yaml.safe_dump(data, sort_keys=False, default_flow_style=False)


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

#: env var -> dotted settings path
ENV_MAP: dict[str, str] = {
    "ORC_JIRA_BASE_URL": "jira.base_url",
    "ORC_JIRA_EMAIL": "jira.email",
    "ORC_JIRA_DEFAULT_PROJECT": "jira.default_project",
    "ORC_JIRA_AC_FIELD": "jira.acceptance_criteria_field",
    "ORC_GIT_PROVIDER": "git.provider",
    "ORC_GIT_DEFAULT_BASE_BRANCH": "git.default_base_branch",
    "ORC_GIT_PUSH_REMOTE": "git.push_remote",
    "ORC_ENGINE": "engine.backend",
    "ORC_ENGINE_MODEL": "engine.model",
    "ORC_ENGINE_TIMEOUT": "engine.timeout_seconds",
    "ORC_CLAUDE_BINARY": "engine.claude_binary",
    "ORC_AUTO_APPROVE": "behaviour.auto_approve",
    "ORC_INTERACTIVE": "behaviour.interactive",
    "ORC_LOG_LEVEL": "behaviour.log_level",
}

_BOOL_TRUE = {"1", "true", "yes", "on"}
_BOOL_FALSE = {"0", "false", "no", "off"}


def _coerce(raw: str) -> Any:
    lowered = raw.lower()
    if lowered in _BOOL_TRUE:
        return True
    if lowered in _BOOL_FALSE:
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _assign(tree: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cursor = tree
    for part in parts[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[parts[-1]] = value


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ConfigurationError(f"{path} must contain a YAML mapping at the top level.")
    return loaded


def load_settings(repo_path: Path | None = None, *, overrides: dict[str, Any] | None = None) -> Settings:
    """Merge every configuration layer into one :class:`Settings`."""
    sources: list[str] = ["defaults"]
    merged: dict[str, Any] = {}

    global_file = global_config_dir() / CONFIG_FILENAME
    if global_file.exists():
        merged = _deep_merge(merged, _read_yaml(global_file))
        sources.append(str(global_file))

    if repo_path is not None:
        repo_file = Path(repo_path) / REPO_CONFIG_RELPATH
        if repo_file.exists():
            merged = _deep_merge(merged, _read_yaml(repo_file))
            sources.append(str(repo_file))

    env_layer: dict[str, Any] = {}
    for env_key, dotted in ENV_MAP.items():
        if env_key in os.environ and os.environ[env_key] != "":
            _assign(env_layer, dotted, _coerce(os.environ[env_key]))
    if env_layer:
        merged = _deep_merge(merged, env_layer)
        sources.append("environment")

    if overrides:
        flat: dict[str, Any] = {}
        for dotted, value in overrides.items():
            if value is not None:
                _assign(flat, dotted, value)
        if flat:
            merged = _deep_merge(merged, flat)
            sources.append("command line")

    settings = Settings.model_validate(merged)
    settings.sources = sources

    if settings.behaviour.auto_approve and os.environ.get("ORC_I_UNDERSTAND_AUTO_APPROVE") != "1":
        raise ConfigurationError(
            "auto_approve is enabled but the safety acknowledgement is not set.",
            hint=(
                "Auto-approval skips every human gate, including commit/push. "
                "Export ORC_I_UNDERSTAND_AUTO_APPROVE=1 to confirm you intend this."
            ),
        )
    return settings


@lru_cache(maxsize=8)
def cached_settings(repo_key: str) -> Settings:  # pragma: no cover - convenience
    return load_settings(Path(repo_key) if repo_key else None)


def write_global_config(settings: Settings) -> Path:
    target = global_config_dir() / CONFIG_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(settings.to_yaml(), encoding="utf-8")
    return target
